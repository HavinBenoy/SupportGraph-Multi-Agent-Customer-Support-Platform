"""The support graph (build step 3): triage -> route -> one branch -> finalize.

    START -> triage -> route --+--> knowledge  (corrective RAG over public documents)
                               +--> action     (step 0 ReAct agent + read-only tools, signed-in students only)
                               +--> clarify    (fixed wording: refuse / ask / close)
                               +--> escalate   (flags a human; the real handoff brief is step 4)
                                        \\--> finalize -> END

Who decides what: triage SUGGESTS (a model); `decide_route` DECIDES (code); the tools AUTHORISE (trusted scope in the run config).
Triage output never reaches a tool as a permission. Each node below is a Langfuse span of its own.
"""

import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.agents.triage import MAX_TRIAGE_CHARS, Triager
from app.config import Settings, get_settings
from app.graph.builder import build_graph
from app.graph.nodes import replies
from app.graph.nodes.router import ACTION, CLARIFY, ESCALATE, KNOWLEDGE, decide_route, is_signed_in
from app.graph.state import TicketState
from app.graph.subgraphs.knowledge import build_knowledge_graph
from app.llm.gateway import get_triager
from app.observability.tracing import instrument_config
from app.tenants.loader import load_tenant_config
from app.tools.scope import ANONYMOUS, ScopeError


def _cfg(config: RunnableConfig) -> dict:
    return (config or {}).get("configurable") or {}


def _text(state: TicketState) -> str:
    human = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
    return " ".join(str(human[-1].content).split())[:MAX_TRIAGE_CHARS] if human else ""


def build_support_graph(*, triager: Triager | None = None, knowledge=None, action=None,
                        action_model: BaseChatModel | None = None, settings: Settings | None = None, checkpointer=None):
    s = settings or get_settings()
    triager = triager or get_triager(s)
    knowledge = knowledge or build_knowledge_graph(settings=s)
    action = action or build_graph(model=action_model)

    def triage(state: TicketState, config: RunnableConfig) -> dict:
        if not _cfg(config).get("tenant_id"):
            raise ScopeError("Support graph run without tenant_id in config['configurable']")  # fail closed
        return {"triage": triager.classify(_text(state)).model_dump()}

    def route(state: TicketState, config: RunnableConfig) -> dict:
        from app.agents.schemas import TriageResult

        decision, reason = decide_route(TriageResult.model_validate(state["triage"]), text=_text(state),
                                        signed_in=is_signed_in(_cfg(config).get("student_id")),
                                        min_confidence=s.triage_min_confidence)
        return {"route": decision, "route_reason": reason}

    def knowledge_node(state: TicketState, config: RunnableConfig) -> dict:
        out = knowledge.invoke({"question": _text(state)}, config)
        r = out["result"]
        return {"handled_by": KNOWLEDGE, "reply": r["answer"], "abstained": r["abstained"],
                "citations": r.get("citations", []), "queries": out.get("queries", []),
                "sources": [c for c in r.get("chunks", []) if c["source_id"] in r.get("citations", [])]}

    def action_node(state: TicketState, config: RunnableConfig) -> dict:
        out = action.invoke({"messages": [HumanMessage(_text(state))]}, config)
        called = [c["name"] for m in out["messages"] if isinstance(m, AIMessage) for c in m.tool_calls]
        return {"handled_by": ACTION, "reply": str(out["messages"][-1].content), "tools_called": called}

    def clarify_node(state: TicketState) -> dict:
        return {"handled_by": CLARIFY, "reply": replies.CLARIFY[state["route_reason"]]}

    def escalate_node(state: TicketState) -> dict:
        return {"handled_by": ESCALATE, "reply": replies.ESCALATE, "needs_human": True,
                "escalation_reason": state["route_reason"]}

    def finalize(state: TicketState, config: RunnableConfig) -> dict:
        """Last stop for every branch. Today: label answers that come from sample documents, so demo data is never
        mistaken for the institute's real policy. Step 4 puts the QA critic and approval gate here."""
        reply = state.get("reply") or replies.CLARIFY["low_confidence"]
        if state.get("handled_by") == KNOWLEDGE and not state.get("abstained") and load_tenant_config(_cfg(config)["tenant_id"]).is_sample:
            reply = f"{reply}\n\n{replies.SAMPLE_NOTICE}"
        return {"reply": reply, "messages": [AIMessage(reply)]}

    g = StateGraph(TicketState)
    for name, fn in (("triage", triage), ("route", route), (KNOWLEDGE, knowledge_node), (ACTION, action_node),
                     (CLARIFY, clarify_node), (ESCALATE, escalate_node), ("finalize", finalize)):
        g.add_node(name, fn)
    g.add_edge(START, "triage")
    g.add_edge("triage", "route")
    g.add_conditional_edges("route", lambda st: st["route"], {b: b for b in (KNOWLEDGE, ACTION, CLARIFY, ESCALATE)})
    for branch in (KNOWLEDGE, ACTION, CLARIFY, ESCALATE):
        g.add_edge(branch, "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def support_config(tenant_id: str, *, student_id: str = ANONYMOUS, ticket_id: str | None = None, **extra: Any) -> dict:
    """Run config for the support graph: trusted scope plus (if enabled) a Langfuse trace.
    Session `<tenant>:<ticket>`, user = student_id, tag `tenant:<tenant>`. `extra` goes into `configurable` (tests pin `today`)."""
    config = {"configurable": {"tenant_id": tenant_id, "student_id": student_id, **extra}, "recursion_limit": 25}
    return instrument_config(config, tenant_id=tenant_id, student_id=student_id,
                             ticket_id=ticket_id or f"t-{uuid.uuid4().hex[:8]}")


def ask_support(graph, text: str, *, tenant_id: str, student_id: str = ANONYMOUS, ticket_id: str | None = None,
                **extra: Any) -> dict:
    """Run one message through the graph and return the final state."""
    return graph.invoke({"messages": [HumanMessage(text)]},
                        support_config(tenant_id, student_id=student_id, ticket_id=ticket_id, **extra))
