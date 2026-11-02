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
    route: str              # knowledge | action | clarify | escalate
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
