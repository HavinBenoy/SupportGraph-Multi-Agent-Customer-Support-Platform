"""Authorization boundaries and approval policy for write actions. Pure rules plus read-only DB lookups; no LLM.

Who may ask:    only a signed-in student, and only about THEIR OWN enrolments/record (the Scope comes from trusted config).
What is valid:  checked against the database, not against what the model or the student claims.
Who approves:   APPROVAL below. "staff" = a person on the institute's team decides; "student" = the student confirms
                their own change; there is deliberately no "auto" tier today: nothing the bot writes happens unattended.
Never a tool:   fee concessions, refunds, certificate or attendance changes. Those are escalations (a human, with a brief).
"""

import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.schemas import ActionProposal
from app.db.models import AttendanceRecord, MakeupRequest
from app.db import repo
from app.guardrails.validators import normalize_batch_id
from app.tenants.schema import TenantConfig
from app.tenants.scope import Scope

APPROVAL: dict[str, str] = {"request_makeup_class": "staff", "update_contact_phone": "student"}
ACTIONS_NEEDING_RECORDS = frozenset({"request_makeup_class"})

_PHONE = re.compile(r"^\+?\d{10,13}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class Validation:
    ok: bool
    code: str = "ok"                       # a key of replies.WRITE_REFUSALS when not ok
    args: dict = field(default_factory=dict)  # the CLEAN arguments the executor will use (never the raw proposal)
    options: list[str] = field(default_factory=list)  # e.g. the dates the student may pick from


def _missed_dates(db: Session, scope: Scope, enrollment_id: str, window_days: int) -> list:
    since = scope.today - timedelta(days=window_days)
    stmt = (select(AttendanceRecord.session_date)
            .where(AttendanceRecord.tenant_id == scope.tenant_id, AttendanceRecord.enrollment_id == enrollment_id,
                   AttendanceRecord.present.is_(False), AttendanceRecord.session_date >= since,
                   AttendanceRecord.session_date < scope.today)
            .order_by(AttendanceRecord.session_date.desc()))
    return list(db.execute(stmt).scalars())


def validate_proposal(db: Session, scope: Scope, p: ActionProposal, cfg: TenantConfig) -> Validation:
    if p.action == "update_contact_phone":
        if not p.phone:
            return Validation(False, "missing_phone")
        phone = re.sub(r"[\s-]", "", p.phone)
        return Validation(True, args={"phone": phone}) if _PHONE.fullmatch(phone) else Validation(False, "bad_phone")

    # request_makeup_class
    batch = normalize_batch_id(p.batch_id) if p.batch_id else None
    if batch is None:
        return Validation(False, "missing_batch")
    rows = [r for r in repo.get_my_enrollments(db, scope, batch) if r[0].status == "active"]
    if not rows:  # not enrolled, not active, someone else's or another tenant's batch: one identical answer (no oracle)
        return Validation(False, "batch_not_found")
    enrollment = rows[0][0]
    missed = _missed_dates(db, scope, enrollment.id, cfg.makeup_window_days)
    if not missed:
        return Validation(False, "no_missed_session")
    if p.session_date is None:
        if len(missed) > 1:
            return Validation(False, "which_session", options=[d.isoformat() for d in missed])
        chosen = missed[0]
    elif p.session_date in missed:
        chosen = p.session_date
    else:  # future, outside the window, or a session the student attended: not requestable
        return Validation(False, "not_a_missed_session", options=[d.isoformat() for d in missed])
    pending = db.scalar(select(MakeupRequest.id).where(
        MakeupRequest.tenant_id == scope.tenant_id, MakeupRequest.student_id == scope.student_id,
        MakeupRequest.batch_id == batch, MakeupRequest.session_date == chosen, MakeupRequest.status == "pending"))
    reason = _CONTROL.sub(" ", " ".join((p.reason or "").split()))[:300] or "Student requested a make-up class"
    return Validation(True, args={"batch_id": batch, "session_date": chosen, "reason": reason, "duplicate_of": pending})
