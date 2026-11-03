"""The staff side of escalations and approvals: list, claim, resolve. Database only; every function is tenant-scoped.
No authentication exists yet (see README "known limits"): the `staff_id` is taken on trust from the caller, so only a
trusted staff console may call this."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import write_repo
from app.db.models import Escalation, MakeupRequest

PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


def list_escalations(db: Session, tenant_id: str, *, team: str | None = None, status: str = "open") -> list[Escalation]:
    stmt = select(Escalation).where(Escalation.tenant_id == tenant_id, Escalation.status == status)
    if team:
        stmt = stmt.where(Escalation.team == team)
    rows = list(db.execute(stmt).scalars())
    return sorted(rows, key=lambda r: (PRIORITY_ORDER.get(r.priority, 9), r.created_at))


def claim(db: Session, tenant_id: str, escalation_id: str, staff_id: str) -> Escalation:
    row = db.get(Escalation, (tenant_id, escalation_id))
    if row is None:
        raise write_repo.WriteError("unknown escalation")
    if row.status != "open":
        raise write_repo.WriteError(f"escalation already {row.status}")
    row.status, row.claimed_by = "claimed", staff_id
    write_repo.audit(db, tenant_id, f"staff:{staff_id}", "escalation.claimed", row.ticket_id, {"escalation_id": row.id})
    return row


def resolve(db: Session, tenant_id: str, escalation_id: str, staff_id: str, note: str) -> Escalation:
    row = db.get(Escalation, (tenant_id, escalation_id))
    if row is None:
        raise write_repo.WriteError("unknown escalation")
    if row.status == "resolved":
        raise write_repo.WriteError("escalation already resolved")
    if row.status == "claimed" and row.claimed_by != staff_id:
        raise write_repo.WriteError("escalation is claimed by someone else")
    row.status, row.claimed_by, row.resolution_note = "resolved", staff_id, note[:1000]
    row.resolved_at = datetime.now(timezone.utc)
    write_repo.set_ticket_status(db, tenant_id, row.ticket_id, "resolved")
    write_repo.audit(db, tenant_id, f"staff:{staff_id}", "escalation.resolved", row.ticket_id, {"escalation_id": row.id})
    return row


def list_pending_makeups(db: Session, tenant_id: str) -> list[MakeupRequest]:
    stmt = (select(MakeupRequest).where(MakeupRequest.tenant_id == tenant_id, MakeupRequest.status == "pending")
            .order_by(MakeupRequest.created_at))
    return list(db.execute(stmt).scalars())
