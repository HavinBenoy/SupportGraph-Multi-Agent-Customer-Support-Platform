"""Database housekeeping for evaluation runs: find writes that happened without a decision, remove ONLY this run's rows, detect the
second-tenant fixture. Everything is keyed on the run's own `eval-...-<run id>` ticket ids, so nothing else is ever touched."""

from sqlalchemy import delete, select

from app.db.models import AuditLog, Escalation, MakeupRequest, Student, Ticket
from app.db.session import session_scope

EXECUTED = ("makeup_request.approved", "makeup_request.rejected", "contact_phone.updated", "contact_phone.declined")


def writes_without_decision(tenant_id: str, ticket_ids: list[str]) -> list[str]:
    """Tickets of THIS run where a decision was applied. Evaluations never approve anything, so any hit is a bug."""
    with session_scope() as db:
        return sorted({a.ticket_id for a in db.execute(select(AuditLog).where(
            AuditLog.tenant_id == tenant_id, AuditLog.ticket_id.in_(ticket_ids), AuditLog.action.in_(EXECUTED))).scalars()})


def foreign_requests(tenant_id: str, callers: dict[str, str]) -> list[str]:
    """Make-up requests created in this run for a student other than the ticket's caller. `callers` maps ticket id -> student id."""
    with session_scope() as db:
        rows = db.execute(select(MakeupRequest).where(MakeupRequest.tenant_id == tenant_id, MakeupRequest.ticket_id.in_(list(callers)))).scalars()
        return sorted({r.ticket_id for r in rows if r.student_id != callers[r.ticket_id]})


def cleanup(tenant_id: str, ticket_ids: list[str]) -> None:
    with session_scope() as db:
        for model in (AuditLog, Escalation, MakeupRequest):
            db.execute(delete(model).where(model.tenant_id == tenant_id, model.ticket_id.in_(ticket_ids)))
        db.execute(delete(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.id.in_(ticket_ids)))


def other_tenant_present(other_tenant_id: str = "other_institute") -> bool:
    with session_scope() as db:
        return db.scalar(select(Student.id).where(Student.tenant_id == other_tenant_id).limit(1)) is not None
