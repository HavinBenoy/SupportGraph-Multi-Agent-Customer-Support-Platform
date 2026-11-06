from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class TicketState(TypedDict, total=False):
    """Shared state every node reads and writes. Grows as we add agents (QA critic, approval, ...).

    `add_messages` is a *reducer*: when a node returns {"messages": [new_msg]} LangGraph
    APPENDS it to the list instead of replacing the list.

    Build step 3 adds the fields below. Each is written by exactly one node, so it is always clear who decided what:
      triage -> triage            route -> route, route_reason
      knowledge/action/clarify/escalate -> the answer fields       finalize -> reply
    The trusted scope (tenant_id, student_id) is NOT here: it lives in the run config, out of the model's reach.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    triage: dict            # TriageResult.model_dump(): a suggestion, never an authorisation
    route: str              # knowledge | action | clarify | escalate | write
    route_reason: str       # why the router chose it (also used to pick the clarify wording)
    handled_by: str         # which branch produced the reply
    reply: str              # the text sent to the person
    abstained: bool         # knowledge branch: true when it declined to answer
    citations: list[str]
    sources: list[dict]     # the cited passages (for groundedness checks in evals)
    queries: list[str]      # knowledge branch: every search query tried (at most `knowledge_max_attempts`)
    tools_called: list[str]  # action branch: names of the tools the agent called
    needs_human: bool       # escalate branch
    escalation_reason: str | None

    # --- build step 4 ---
    ticket_id: str          # the case this run belongs to (also the thread id suffix)
    proposal: dict | None   # write branch: what the model PROPOSED (data, grants nothing)
    check: dict             # write branch: code's verdict {ok, code, args, options, request_id, duplicate}
    approval_role: str      # "staff" | "student": who must decide (from the approval policy, never from the model)
    outcome: str            # write branch: "approved" | "rejected" | "refused" | "duplicate"
    escalation_id: str | None
    qa_issues: list[dict]   # [{"code","detail"}] from the last QA pass
    qa_revisions: int       # how many times the reply was revised
    qa_blocked: bool        # true when the reply was replaced by the safe fallback

    # --- M2.1 ---
    intent: str             # conversation-control intent found by code (greeting/thanks/acknowledgement/goodbye/interruption), else ""
    end_call: bool          # true for a goodbye: a voice call may hang up after speaking the reply (text chat ignores it)

    # --- M2.2 ---
    resolved: str           # the caller's text with the course from an earlier question added (follow-up/correction), else ""
    context_kind: str       # "follow_up" | "correction" | ""
