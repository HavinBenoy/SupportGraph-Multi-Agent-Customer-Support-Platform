"""The support graph (build steps 3 and 4).

    START -> triage -> route --+--> knowledge  corrective RAG over public documents
                               +--> action     step 0 ReAct agent + read-only tools (signed-in students only)
                               +--> write      propose -> prepare -> approval (PAUSES) -> execute   [step 4]
                               +--> clarify    fixed wording: refuse / ask / close
                               +--> escalate   persists a ticket + a brief for a human team          [step 4]
                                       \\--> qa -(pass)-> finalize -> END
                                             \\-(fixable)-> revise -> qa      (capped)
                                             \\-(else)-> fallback -> finalize

Who decides what: triage SUGGESTS (a model); `decide_route` DECIDES (code); the tools AUTHORISE (trusted scope in the run
config); a person or the student APPROVES anything that writes. A run waiting for approval is saved by the checkpointer
and continues, possibly in another process, when `review.resume_ticket` answers it.
"""

import re
import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.actions.proposer import Proposer
from app.agents.schemas import TriageResult
from app.agents.triage import Triager
from app.config import Settings, get_settings
from app.graph.builder import build_graph
import logging

from app.llm.errors import ModelUnavailable
from app.graph.nodes import replies
from app.graph.nodes.common import PER_RUN_RESET, cfg_of, text_of
from app.graph.nodes.escalate import escalate
from app.graph.nodes.qa import make_qa_nodes
from app.guardrails.escalation_floor import escalation_floor, raise_priority
from app.graph.nodes.router import ACTION, CLARIFY, ESCALATE, KNOWLEDGE, WRITE, decide_route, is_signed_in
from app.graph.nodes.write import make_write_nodes
from app.graph.state import TicketState
from app.graph.subgraphs.knowledge import build_knowledge_graph
from app.llm.gateway import get_critic, get_proposer, get_reviser, get_triager
from app.observability.tracing import instrument_config
from app.qa.critic import Critic, Reviser
from app.tenants.loader import load_tenant_config
from app.tools.scope import ANONYMOUS, ScopeError

_TICKET_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


log = logging.getLogger(__name__)


def build_support_graph(*, triager: Triager | None = None, knowledge=None, action=None, action_model: BaseChatModel | None = None,
                        proposer: Proposer | None = None, critic: Critic | None = None, reviser: Reviser | None = None,
                        settings: Settings | None = None, checkpointer=None):
    s = settings or get_settings()
    triager = triager or get_triager(s)
    knowledge = knowledge or build_knowledge_graph(settings=s)
    action = action or build_graph(model=action_model)
    write = make_write_nodes(proposer or get_proposer(s))
    qa = make_qa_nodes(reviser or get_reviser(s), critic if critic is not None else get_critic(s), s)
    # No checkpointer given = in-memory (lost on restart). Production passes `get_checkpointer(settings)`.
    checkpointer = checkpointer if checkpointer is not None else InMemorySaver()

    def triage(state: TicketState, config: RunnableConfig) -> dict:
        if not cfg_of(config).get("tenant_id"):
            raise ScopeError("Support graph run without tenant_id in config['configurable']")  # fail closed
        return {**PER_RUN_RESET, "triage": triager.classify(text_of(state)).model_dump()}

    def route(state: TicketState, config: RunnableConfig) -> dict:
        decision, reason = decide_route(TriageResult.model_validate(state["triage"]), text=text_of(state),
                                        signed_in=is_signed_in(cfg_of(config).get("student_id")),
                                        min_confidence=s.triage_min_confidence, other_floor=s.triage_other_floor)
        if decision != ESCALATE and reason != "injection_suspected":
            floor = escalation_floor(text_of(state))  # model-independent safety net: it can only ADD an escalation
            if floor:
                category, priority = floor
                triage = {**state["triage"], "category": category, "priority": raise_priority(state["triage"].get("priority", "low"), priority)}
                return {"route": ESCALATE, "route_reason": category, "triage": triage}
        return {"route": decision, "route_reason": reason}

    def knowledge_node(state: TicketState, config: RunnableConfig) -> dict:
        try:
            out = knowledge.invoke({"question": text_of(state)}, config)
        except ModelUnavailable as e:
            log.warning("knowledge: model unavailable (%s)", e.reason)
            return {"handled_by": KNOWLEDGE, "reply": replies.MODEL_UNAVAILABLE, "abstained": True,
                    "citations": [], "queries": [], "sources": []}
        r = out["result"]
        return {"handled_by": KNOWLEDGE, "reply": r["answer"], "abstained": r["abstained"],
                "citations": r.get("citations", []), "queries": out.get("queries", []),
                "sources": [c for c in r.get("chunks", []) if c["source_id"] in r.get("citations", [])]}

    def action_node(state: TicketState, config: RunnableConfig) -> dict:
        try:
            out = action.invoke({"messages": [HumanMessage(text_of(state))]}, config)
        except ModelUnavailable as e:
            log.warning("action: model unavailable (%s)", e.reason)
            return {"handled_by": ACTION, "reply": replies.MODEL_UNAVAILABLE, "tools_called": []}
        called = [c["name"] for m in out["messages"] if isinstance(m, AIMessage) for c in m.tool_calls]
        return {"handled_by": ACTION, "reply": str(out["messages"][-1].content), "tools_called": called}

    def clarify_node(state: TicketState) -> dict:
        return {"handled_by": CLARIFY, "reply": replies.CLARIFY[state["route_reason"]]}

    def escalate_node(state: TicketState, config: RunnableConfig) -> dict:
        return escalate(state, config, reason=state["route_reason"])

    def finalize(state: TicketState, config: RunnableConfig) -> dict:
        """Last stop for every branch. Labels answers that come from sample documents, so demo data is never mistaken for
        the institute's real policy, and appends the assistant message."""
        reply = state.get("reply") or replies.CLARIFY["low_confidence"]
        if (state.get("handled_by") == KNOWLEDGE and not state.get("abstained") and not state.get("qa_blocked")
                and load_tenant_config(cfg_of(config)["tenant_id"]).is_sample):
            reply = f"{reply}\n\n{replies.SAMPLE_NOTICE}"
        return {"reply": reply, "messages": [AIMessage(reply)]}

    g = StateGraph(TicketState)
    nodes = {"triage": triage, "route": route, KNOWLEDGE: knowledge_node, ACTION: action_node, CLARIFY: clarify_node,
             ESCALATE: escalate_node, "propose": write["propose"], "prepare": write["prepare"], "approval": write["approval"],
             "execute": write["execute"], "qa": qa["qa"], "revise": qa["revise"], "fallback": qa["fallback"], "finalize": finalize}
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_edge(START, "triage")
    g.add_edge("triage", "route")
    entry = {KNOWLEDGE: KNOWLEDGE, ACTION: ACTION, CLARIFY: CLARIFY, ESCALATE: ESCALATE, WRITE: "propose"}
    g.add_conditional_edges("route", lambda st: st["route"], entry)
    for branch in (KNOWLEDGE, ACTION, CLARIFY, ESCALATE):
        g.add_edge(branch, "qa")
    g.add_edge("propose", "prepare")
    g.add_conditional_edges("prepare", write["after_prepare"], {"approval": "approval", "done": "qa"})
    g.add_edge("approval", "execute")
    g.add_edge("execute", "qa")
    g.add_conditional_edges("qa", qa["after_qa"], {"finalize": "finalize", "revise": "revise", "fallback": "fallback"})
    g.add_edge("revise", "qa")
    g.add_edge("fallback", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def thread_id(tenant_id: str, student_id: str, ticket_id: str) -> str:
    """One thread per (tenant, student, ticket). Including the student means a student who guesses another's ticket id
    lands in an empty thread of their own, never in someone else's conversation or pending approval."""
    if not _TICKET_ID.fullmatch(ticket_id):
        raise ScopeError("ticket_id must be 1-64 characters of letters, digits, '.', '_' or '-'")
    return f"{tenant_id}:{student_id}:{ticket_id}"


def support_config(tenant_id: str, *, student_id: str = ANONYMOUS, ticket_id: str | None = None, **extra: Any) -> dict:
    """Run config: trusted scope, the checkpoint thread, and (if enabled) a Langfuse trace.
    Session `<tenant>:<ticket>`, user = student_id, tag `tenant:<tenant>`. `extra` goes into `configurable` (tests pin `today`)."""
    ticket_id = ticket_id or f"t-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"tenant_id": tenant_id, "student_id": student_id, "ticket_id": ticket_id,
                               "thread_id": thread_id(tenant_id, student_id, ticket_id), **extra}, "recursion_limit": 40}
    return instrument_config(config, tenant_id=tenant_id, student_id=student_id, ticket_id=ticket_id)


def _with_pause(graph, out: dict, config: dict) -> dict:
    """If the run stopped at an approval, expose what the student should be told and who must decide."""
    pending = out.get("__interrupt__")
    if not pending:
        return out
    payload = pending[0].value
    return {**out, "reply": payload["student_message"], "awaiting": payload["role"], "pending": payload}


def ask_support(graph, text: str, *, tenant_id: str, student_id: str = ANONYMOUS, ticket_id: str | None = None, **extra: Any) -> dict:
    """Run one message through the graph and return the final state. If the run paused for an approval the result also has
    `awaiting` ("staff" | "student") and `pending` (the interrupt payload); `reply` is then what to show the student now."""
    config = support_config(tenant_id, student_id=student_id, ticket_id=ticket_id, **extra)
    return _with_pause(graph, graph.invoke({"messages": [HumanMessage(text)]}, config), config)
