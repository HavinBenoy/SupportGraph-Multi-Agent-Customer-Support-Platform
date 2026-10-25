from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class TicketState(TypedDict):
    """Shared state every node reads and writes. Grows as we add agents (triage, drafts, ...).

    `add_messages` is a *reducer*: when a node returns {"messages": [new_msg]} LangGraph
    APPENDS it to the list instead of replacing the list.
    """

    messages: Annotated[list[AnyMessage], add_messages]
