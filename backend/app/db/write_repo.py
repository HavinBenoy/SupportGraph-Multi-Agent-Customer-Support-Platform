"""Every database WRITE of build step 4. Read queries stay in repo.py.

Rules enforced here, not in prompts:
  * every function takes the tenant (through Scope/tenant_id) and student-owned rows are checked against the student;
  * a ticket id can never be reused by a different student (a ticket is a private thread);
  * creating things is idempotent (the same key returns the existing row), so resuming a paused run twice or retrying
    after a crash cannot duplicate a request;
  * every change appends an audit row in the same transaction.
Callers own the transaction (`session_scope()`): a failed write rolls back together with its audit row.
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Escalation, MakeupRequest, Student, Ticket
from app.tenants.scope import Scope


class WriteError(RuntimeError):
    """A write was refused (wrong owner, already decided, unknown row). The message is safe to log, not to show a user."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def audit(db: Session, tenant_id: str, actor: str, action: str, ticket_id: str | None = None, detail: dict | None = None) -> None:
    db.add(AuditLog(tenant_id=tenant_id, ticket_id=ticket_id, actor=actor, action=action, detail=detail or {}))


def ensure_ticket(db: Session, tenant_id: str, ticket_id: str, student_id: str | None, *, category: str = "other",
                  priority: str = "low", status: str | None = None) -> Ticket:
    """Create the ticket on first use; afterwards only update it. `student_id` None = anonymous prospect."""
    t = db.get(Ticket, (tenant_id, ticket_id))
    if t is None:
        t = Ticket(tenant_id=tenant_id, id=ticket_id, student_id=student_id, category=category, priority=priority,
                   status=status or "open")
        db.add(t)
        db.flush()
        return t
    if t.student_id != student_id:  # someone else's thread id: refuse, do not merge conversations
        raise WriteError("ticket belongs to a different student")
    t.category, t.priority, t.updated_at = category, priority, _now()
    if status:
        t.status = status
    return t


def set_ticket_status(db: Session, tenant_id: str, ticket_id: str, status: str) -> None:
    t = db.get(Ticket, (tenant_id, ticket_id))
    if t is None:
        raise WriteError("unknown ticket")
    t.status, t.updated_at = status, _now()


def create_makeup_request(db: Session, scope: Scope, *, ticket_id: str, batch_id: str, session_date: date, reason: str) -> MakeupRequest:
    """Idempotent per (ticket, batch, date). Always created `pending`: approval is a separate, staff-only step."""
    key = f"{ticket_id}:makeup:{batch_id}:{session_date.isoformat()}"
    existing = db.scalar(select(MakeupRequest).where(MakeupRequest.tenant_id == scope.tenant_id, MakeupRequest.idempotency_key == key))
    if existing is not None:
        if existing.student_id != scope.student_id:
            raise WriteError("request belongs to a different student")
        return existing
    row = MakeupRequest(tenant_id=scope.tenant_id, id=f"MK-{uuid.uuid4().hex[:8].upper()}", student_id=scope.student_id,
                        batch_id=batch_id, session_date=session_date, reason=reason, status="pending", ticket_id=ticket_id,
                        idempotency_key=key)
    db.add(row)
    db.flush()
    audit(db, scope.tenant_id, f"student:{scope.student_id}", "makeup_request.created", ticket_id,
          {"request_id": row.id, "batch_id": batch_id, "session_date": session_date.isoformat()})
    return row


def decide_makeup_request(db: Session, tenant_id: str, request_id: str, *, approve: bool, reviewer_id: str) -> MakeupRequest:
    """Only a pending request of THIS tenant can be decided, once. A second decision (or a replay) is refused."""
    # FOR UPDATE: two reviewers deciding at the same moment queue up, and the second sees "already approved" (Postgres; SQLite has no row locks).
    row = db.execute(select(MakeupRequest).where(MakeupRequest.tenant_id == tenant_id, MakeupRequest.id == request_id)
                     .with_for_update()).scalar_one_or_none()
    if row is None:
        raise WriteError("unknown request")
    if row.status != "pending":
        raise WriteError(f"request already {row.status}")
    row.status, row.decided_by, row.decided_at = ("approved" if approve else "rejected"), reviewer_id, _now()
    audit(db, tenant_id, f"staff:{reviewer_id}", f"makeup_request.{row.status}", row.ticket_id, {"request_id": row.id})
    return row


def update_student_phone(db: Session, scope: Scope, phone: str) -> str | None:
    """Changes ONLY the calling student's own phone. Returns the previous value (for the audit trail, masked by the caller)."""
    s = db.get(Student, (scope.tenant_id, scope.student_id))
    if s is None:
        raise WriteError("unknown student")
    old, s.phone = s.phone, phone
    return old


def create_escalation(db: Session, tenant_id: str, *, ticket_id: str, team: str, priority: str, reason: str, brief: dict) -> Escalation:
    """At most one escalation per ticket: a repeat call returns the existing one (the first brief stays the record)."""
    existing = db.scalar(select(Escalation).where(Escalation.tenant_id == tenant_id, Escalation.ticket_id == ticket_id))
    if existing is not None:
        return existing
    row = Escalation(tenant_id=tenant_id, id=f"ESC-{uuid.uuid4().hex[:8].upper()}", ticket_id=ticket_id, team=team,
                     priority=priority, reason=reason, brief=brief)
    db.add(row)
    db.flush()
    audit(db, tenant_id, "system", "escalation.created", ticket_id, {"escalation_id": row.id, "team": team, "reason": reason})
    return row
