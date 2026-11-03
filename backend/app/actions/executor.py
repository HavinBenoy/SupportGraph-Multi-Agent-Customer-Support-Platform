"""The write tools of build step 4. They are plain functions, NOT LangChain tools: the LLM never sees them and cannot
call them. Only graph code reaches them, after validation (policy.py) and after the required approval.

Authorization boundary, enforced again here at the last moment (defence in depth, in case a node above is wrong):
  - the reviewer must belong to the same tenant as the scope;
  - the reviewer's role must be the one APPROVAL requires for that action (a student cannot approve a staff action);
  - a student may only confirm THEIR OWN change (reviewer_id == scope.student_id).
Anything else raises AuthorizationError and nothing is written.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.actions.policy import APPROVAL
from app.db import write_repo
from app.guardrails.pii import mask_phone
from app.tenants.scope import Reviewer, Scope

WRITE_ACTIONS = frozenset(APPROVAL)


class AuthorizationError(PermissionError):
    """The decision came from someone who may not make it. Safe to log; never shown to the student verbatim."""


@dataclass(frozen=True)
class ExecResult:
    outcome: str            # "approved" | "rejected"
    request_id: str | None = None


def authorize_decision(scope: Scope, action: str, reviewer: Reviewer) -> None:
    if action not in APPROVAL:
        raise AuthorizationError(f"unknown action {action!r}")
    if reviewer.tenant_id != scope.tenant_id:
        raise AuthorizationError("reviewer belongs to a different tenant")
    if reviewer.role != APPROVAL[action]:
        raise AuthorizationError(f"{action} must be decided by role {APPROVAL[action]!r}, not {reviewer.role!r}")
    if reviewer.role == "student" and reviewer.reviewer_id != scope.student_id:
        raise AuthorizationError("a student can only confirm their own change")


def prepare_action(db: Session, scope: Scope, *, ticket_id: str, action: str, args: dict) -> str | None:
    """Everything that is safe to do BEFORE the decision, and idempotent: record the pending request so staff can see it.
    Returns the request id (make-up) or None."""
    if action == "request_makeup_class":
        row = write_repo.create_makeup_request(db, scope, ticket_id=ticket_id, batch_id=args["batch_id"],
                                               session_date=args["session_date"], reason=args["reason"])
        return row.id
    return None


def execute_action(db: Session, scope: Scope, *, ticket_id: str, action: str, args: dict, request_id: str | None,
                   approve: bool, reviewer: Reviewer) -> ExecResult:
    authorize_decision(scope, action, reviewer)
    if action == "request_makeup_class":
        if request_id is None:
            raise write_repo.WriteError("make-up decision without a request id")
        row = write_repo.decide_makeup_request(db, scope.tenant_id, request_id, approve=approve, reviewer_id=reviewer.reviewer_id)
        if row.student_id != scope.student_id:  # the request must belong to the student whose ticket this is
            raise AuthorizationError("request belongs to a different student")
        return ExecResult(row.status, row.id)
    # update_contact_phone
    if approve:
        old = write_repo.update_student_phone(db, scope, args["phone"])
        write_repo.audit(db, scope.tenant_id, reviewer.actor, "contact_phone.updated", ticket_id,
                         {"old": mask_phone(old), "new": mask_phone(args["phone"])})
        return ExecResult("approved")
    write_repo.audit(db, scope.tenant_id, reviewer.actor, "contact_phone.declined", ticket_id, {})
    return ExecResult("rejected")
