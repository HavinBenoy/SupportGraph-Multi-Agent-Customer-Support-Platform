"""Small helpers shared by the support graph's node factories."""

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.agents.triage import MAX_TRIAGE_CHARS
from app.tools.scope import ScopeError

# Fields a run produces. Cleared at the start of every run: with a checkpointer the thread keeps its last state, and a
# stale `check` or `qa_revisions` from the previous message must never leak into the next one.
PER_RUN_RESET = {
    "reply": "", "abstained": False, "citations": [], "sources": [], "queries": [], "tools_called": [], "needs_human": False,
    "escalation_reason": None, "escalation_id": None, "proposal": None, "check": {}, "outcome": "", "approval_role": "",
    "qa_issues": [], "qa_revisions": 0, "qa_blocked": False, "handled_by": "",
}


def cfg_of(config: RunnableConfig) -> dict:
    return (config or {}).get("configurable") or {}


def text_of(state: dict) -> str:
    human = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
    return " ".join(str(human[-1].content).split())[:MAX_TRIAGE_CHARS] if human else ""


def ticket_of(config: RunnableConfig) -> str:
    t = cfg_of(config).get("ticket_id")
    if not t:
        raise ScopeError("Support graph run without ticket_id in config['configurable']")  # fail closed
    return t
