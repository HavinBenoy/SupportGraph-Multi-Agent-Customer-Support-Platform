"""The QA validation loop. Every reply passes through here before the person sees it.

    qa --pass--> finalize
       --fail, fixable, revisions left--> revise --> qa (again)
       --fail otherwise--> fallback --> finalize

Fixable = a problem local to a sentence (an unsupported number, a forbidden promise). Everything else (a leak, another
student's id, an uncited answer, a question entity missing from the sources) cannot be repaired by editing, so the reply is
replaced by a safe fallback at once. A security-class failure additionally escalates to a human with a brief.
The loop is capped by `qa_max_revisions`, so it cannot spin.
"""

from functools import lru_cache

from langchain_core.runnables import RunnableConfig

from app.config import Settings
from app.graph.nodes import replies
from app.graph.nodes.common import cfg_of, text_of
from app.graph.nodes.escalate import escalate
from app.qa.checks import QaIssue, run_checks
from app.qa.critic import Critic, Reviser
from app.rag.evaluate import internal_markers
from app.tools.scope import ANONYMOUS

REVISABLE = frozenset({"unsupported_number", "forbidden_promise", "critic_ungrounded"})


@lru_cache(maxsize=32)
def _markers(tenant_id: str) -> tuple[str, ...]:
    return tuple(internal_markers(tenant_id))


def make_qa_nodes(reviser: Reviser, critic: Critic | None, settings: Settings) -> dict:
    def qa(state: dict, config: RunnableConfig) -> dict:
        cfg = cfg_of(config)
        sid = cfg.get("student_id")
        issues = run_checks(reply=state.get("reply", ""), question=text_of(state), student_id=None if sid == ANONYMOUS else sid,
                            markers=list(_markers(cfg["tenant_id"])), route=state.get("route", ""),
                            abstained=state.get("abstained", False), citations=state.get("citations", []),
                            sources=state.get("sources", []))
        if critic and not issues and state.get("route") == "knowledge" and not state.get("abstained"):
            issues = critic.review(question=text_of(state), reply=state["reply"], sources=state.get("sources", []))
        return {"qa_issues": [{"code": i.code, "detail": i.detail} for i in issues]}

    def after_qa(state: dict) -> str:
        issues = state.get("qa_issues", [])
        if not issues:
            return "finalize"
        if state.get("qa_revisions", 0) < settings.qa_max_revisions and all(i["code"] in REVISABLE for i in issues):
            return "revise"
        return "fallback"

    def revise(state: dict) -> dict:
        issues = [QaIssue(i["code"], i["detail"]) for i in state["qa_issues"]]
        new = reviser.revise(reply=state["reply"], issues=issues, sources=state.get("sources", []))
        return {"reply": new, "qa_revisions": state.get("qa_revisions", 0) + 1}

    def fallback(state: dict, config: RunnableConfig) -> dict:
        out = {"reply": replies.QA_FALLBACK, "qa_blocked": True, "abstained": True, "citations": []}
        if any(QaIssue(i["code"]).is_security for i in state.get("qa_issues", [])):
            out.update(escalate(state, config, reason="qa_security"))
        return out

    return {"qa": qa, "after_qa": after_qa, "revise": revise, "fallback": fallback}
