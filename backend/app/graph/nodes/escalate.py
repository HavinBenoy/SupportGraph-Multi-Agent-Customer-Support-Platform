"""Escalation (build step 4): persist the case and a brief for a human team, then tell the person honestly what happened.

Used by the escalate branch and by the QA loop when a reply is blocked for a security reason. If the database write
fails the person still gets a truthful message (no reference, "contact support directly"): never a claim that something
was recorded when it was not."""

import logging
from datetime import date

from langchain_core.runnables import RunnableConfig
from sqlalchemy.exc import SQLAlchemyError

from app.db import write_repo
from app.db.session import session_scope
from app.escalation.brief import build_brief
from app.graph.nodes import replies
from app.graph.nodes.common import cfg_of, text_of, ticket_of
from app.tenants.loader import load_tenant_config
from app.tools.scope import ANONYMOUS

log = logging.getLogger(__name__)


def escalate(state: dict, config: RunnableConfig, *, reason: str) -> dict:
    cfg = cfg_of(config)
    tenant_id, ticket_id = cfg["tenant_id"], ticket_of(config)
    sid = cfg.get("student_id")
    student = sid if sid and sid != ANONYMOUS else None
    triage = state.get("triage") or {}
    tcfg = load_tenant_config(tenant_id)
    trail = {"route": state.get("route"), "route_reason": state.get("route_reason"), "handled_by": state.get("handled_by"),
             "searches": state.get("queries", []), "tools_called": state.get("tools_called", []),
             "qa_issues": [i["code"] for i in state.get("qa_issues", [])]}
    today = date.fromisoformat(cfg["today"]) if cfg.get("today") else date.today()
    base = {"needs_human": True, "escalation_reason": reason, "handled_by": "escalate"}
    try:
        with session_scope() as db:
            write_repo.ensure_ticket(db, tenant_id, ticket_id, student, category=triage.get("category", "other"),
                                     priority=triage.get("priority", "low"), status="escalated")
            brief = build_brief(db, tcfg, tenant_id=tenant_id, student_id=student, ticket_id=ticket_id, today=today,
                                reason=reason, triage=triage, text=text_of(state), trail=trail)
            esc = write_repo.create_escalation(db, tenant_id, ticket_id=ticket_id, team=brief.team, priority=brief.priority,
                                               reason=reason, brief=brief.model_dump(mode="json"))
            ref, team = esc.id, esc.team
    except (SQLAlchemyError, write_repo.WriteError) as e:
        log.error("Escalation could not be recorded (%s)", type(e).__name__)
        return {**base, "escalation_id": None, "reply": replies.ESCALATE_UNRECORDED}
    if reason == "human_request":
        template = replies.HUMAN_SIGNED_IN if student else replies.HUMAN_ANONYMOUS
    else:
        template = replies.ESCALATE_SIGNED_IN if student else replies.ESCALATE_ANONYMOUS
    return {**base, "escalation_id": ref, "reply": template.format(team=team.replace("-", " "), ref=ref)}
