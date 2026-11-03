"""The write branch: propose -> prepare -> approval (pauses) -> execute.

    propose   a model (or offline rules) turns the message into a PROPOSAL. Data only.
    prepare   CODE validates it against the database, records a pending request, picks the approver from the policy.
              Safe to repeat: everything here is idempotent, because the node after it pauses.
    approval  interrupt(): the run stops and is saved by the checkpointer until a staff member (make-up class) or the
              student (phone change) answers. The pause is the ONLY thing between a proposal and a write.
    execute   re-checks who decided (executor.authorize_decision) and only then writes, with an audit row.

Why `interrupt()` lives alone in its node: when a paused run resumes, LangGraph re-runs that node from its first line.
Anything with side effects placed before the interrupt would run twice, so the side effects are in `prepare`/`execute`.
"""

import logging
from datetime import date

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.actions.executor import AuthorizationError, authorize_decision, execute_action, prepare_action
from app.actions.policy import APPROVAL, validate_proposal
from app.actions.proposer import Proposer
from app.actions.schemas import ActionProposal
from app.db import write_repo
from app.db.session import session_scope
from app.graph.nodes import replies
from app.graph.nodes.common import cfg_of, text_of, ticket_of
from app.guardrails.pii import mask_phone
from app.tenants.loader import load_tenant_config
from app.tenants.scope import Reviewer
from app.tools.scope import require_scope

WRITE = "write"
MAX_INVALID_DECISIONS = 3
log = logging.getLogger(__name__)


def _refuse(code: str, *, days: int, options: list[str] | None = None) -> dict:
    reply = replies.WRITE_REFUSALS[code].format(days=days, options=", ".join(options or []))
    return {"handled_by": WRITE, "outcome": "refused", "reply": reply, "check": {"ok": False, "code": code}}


def make_write_nodes(proposer: Proposer) -> dict:
    def propose(state: dict, config: RunnableConfig) -> dict:
        p = proposer.propose(text_of(state))
        return {"proposal": p.model_dump(mode="json") if p else None}

    def prepare(state: dict, config: RunnableConfig) -> dict:
        scope = require_scope(config)  # a prospect cannot reach here (router), and this raises for them too
        ticket_id = ticket_of(config)
        tcfg = load_tenant_config(scope.tenant_id)
        days = tcfg.makeup_window_days
        if not state.get("proposal"):
            return _refuse("unclear", days=days)
        proposal = ActionProposal.model_validate(state["proposal"])
        triage = state.get("triage") or {}
        with session_scope() as db:
            write_repo.ensure_ticket(db, scope.tenant_id, ticket_id, scope.student_id, category=triage.get("category", "other"),
                                     priority=triage.get("priority", "low"))
            v = validate_proposal(db, scope, proposal, tcfg)
            if not v.ok:
                write_repo.audit(db, scope.tenant_id, f"student:{scope.student_id}", "action.refused", ticket_id,
                                 {"action": proposal.action, "code": v.code})
                return _refuse(v.code, days=days, options=v.options)
            args = {k: (x.isoformat() if isinstance(x, date) else x) for k, x in v.args.items()}
            batch, when = args.get("batch_id"), args.get("session_date")
            if v.args.get("duplicate_of"):
                return {"handled_by": WRITE, "outcome": "duplicate", "check": {"ok": True, "args": args, "request_id": v.args["duplicate_of"]},
                        "reply": replies.MAKEUP_DUPLICATE.format(batch=batch, date=when, ref=v.args["duplicate_of"])}
            request_id = prepare_action(db, scope, ticket_id=ticket_id, action=proposal.action, args=v.args)
            write_repo.set_ticket_status(db, scope.tenant_id, ticket_id, "awaiting_approval")
            write_repo.audit(db, scope.tenant_id, f"student:{scope.student_id}", "action.proposed", ticket_id,
                             {"action": proposal.action, "approver": APPROVAL[proposal.action], "request_id": request_id})
        return {"handled_by": WRITE, "approval_role": APPROVAL[proposal.action],
                "check": {"ok": True, "args": args, "request_id": request_id}}

    def after_prepare(state: dict) -> str:
        return "approval" if state.get("check", {}).get("ok") and not state.get("outcome") else "done"

    def approval(state: dict, config: RunnableConfig) -> dict:
        scope = require_scope(config)
        action, role, chk = state["proposal"]["action"], state["approval_role"], state["check"]
        if action == "request_makeup_class":
            student_message = replies.MAKEUP_SUBMITTED.format(batch=chk["args"]["batch_id"], date=chk["args"]["session_date"], ref=chk["request_id"])
            summary = f"Make-up class for {chk['args']['batch_id']} on {chk['args']['session_date']}"
        else:
            student_message = replies.PHONE_CONFIRM.format(phone=chk["args"]["phone"])
            summary = f"Change phone number to {mask_phone(chk['args']['phone'])}"
        payload = {"kind": "approval", "action": action, "role": role, "ticket_id": ticket_of(config), "tenant_id": scope.tenant_id,
                   "request_id": chk.get("request_id"), "summary": summary, "student_message": student_message}
        # When resumed, LangGraph re-runs this node from its first line and feeds the saved answers back in order. A wrong
        # answer (wrong role/tenant/person) is therefore ignored by asking AGAIN (a second interrupt), not by raising:
        # a raised error would keep the bad answer stored and block every later, valid one. Bounded, so it cannot loop forever.
        for _ in range(MAX_INVALID_DECISIONS + 1):
            raw = interrupt(payload)
            try:
                if not isinstance(raw, dict):
                    raise AuthorizationError("decision is not a mapping")
                reviewer = Reviewer(tenant_id=str(raw.get("tenant_id", "")), reviewer_id=str(raw.get("reviewer_id", "")), role=str(raw.get("role", "")))
                authorize_decision(scope, action, reviewer)
                break
            except AuthorizationError as e:
                log.warning("Ignored an approval from the wrong party for %s: %s", ticket_of(config), e)
        else:
            raise AuthorizationError("too many invalid decisions")
        return {"check": {**chk, "reviewer": {"id": reviewer.reviewer_id, "role": reviewer.role}, "approve": raw.get("decision") == "approve"}}

    def execute(state: dict, config: RunnableConfig) -> dict:
        scope = require_scope(config)
        action, chk = state["proposal"]["action"], state["check"]
        reviewer = Reviewer(tenant_id=scope.tenant_id, reviewer_id=chk["reviewer"]["id"], role=chk["reviewer"]["role"])
        args = dict(chk["args"])
        if "session_date" in args:
            args["session_date"] = date.fromisoformat(args["session_date"])
        with session_scope() as db:
            res = execute_action(db, scope, ticket_id=ticket_of(config), action=action, args=args, request_id=chk.get("request_id"),
                                 approve=chk["approve"], reviewer=reviewer)
            write_repo.set_ticket_status(db, scope.tenant_id, ticket_of(config), "resolved")
        reply = replies.OUTCOMES[(action, res.outcome)].format(ref=res.request_id, phone=mask_phone(args.get("phone")))
        return {"outcome": res.outcome, "reply": reply}

    return {"propose": propose, "prepare": prepare, "after_prepare": after_prepare, "approval": approval, "execute": execute}


__all__ = ["AuthorizationError", "WRITE", "make_write_nodes"]
