"""Escalation briefs: what a human needs to pick a case up without re-reading the chat or re-asking the student.

Built by CODE from trusted data (the run state and the student's own records through the scoped repo), not written by a
model, so it cannot invent facts. Contact details in the message excerpt are masked. A brief never contains another
student's data, internal documents, or credentials; the test-suite asserts that.
"""

from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import repo
from app.guardrails.pii import mask_pii
from app.tenants.schema import TenantConfig
from app.tenants.scope import Scope

EXCERPT_CHARS = 500
# What the human should do first, per reason. Static, reviewable text (never promises an outcome to the student).
NEXT_STEP = {
    "complaint": "Acknowledge the complaint, find out what happened, and reply to the student.",
    "payment_dispute": "Check the student's payments in the finance system; do not confirm a refund before verifying.",
    "concession_request": "Review against the concession policy (internal playbook); a decision needs a manager.",
    "legal_threat": "Do not argue the merits. Hand to management/legal and keep the written record.",
    "safety": "Contact the student promptly and follow the safety procedure; consider involving the centre head.",
    "human_request": "The student asked for a person: reply directly.",
    "urgent_priority": "Marked urgent by triage: read the excerpt first and respond soon.",
    "qa_security": "An automated reply was blocked by the safety checks (possible leak). Review the trail, do not release the draft.",
}
DEFAULT_NEXT_STEP = "Read the excerpt and reply to the student."


class BatchFact(BaseModel):
    batch_id: str
    course: str
    status: str
    attendance_pct: float | None = None


class EscalationBrief(BaseModel):
    ticket_id: str
    tenant_id: str
    team: str
    priority: str
    sla_hours: int                       # internal target for staff, never promised to the student
    reason: str
    category: str
    sentiment: str
    language: str
    signed_in: bool
    student_id: str | None = None
    student_name: str | None = None
    batches: list[BatchFact] = Field(default_factory=list)   # the student's OWN records only
    excerpt: str                         # last student message, PII-masked, truncated
    trail: dict                          # what the system already did: route, searches, tools, QA issues
    suggested_next_step: str
    contact_note: str


def _batch_facts(db: Session, scope: Scope) -> list[BatchFact]:
    facts = []
    for enrollment, batch, course in repo.get_my_enrollments(db, scope):
        held, present = repo.attendance_counts(db, scope, enrollment.id)
        facts.append(BatchFact(batch_id=batch.id, course=course.name, status=enrollment.status,
                               attendance_pct=round(100 * present / held, 1) if held else None))
    return facts


def build_brief(db: Session, cfg: TenantConfig, *, tenant_id: str, student_id: str | None, ticket_id: str, today: date,
                reason: str, triage: dict, text: str, trail: dict) -> EscalationBrief:
    signed_in = bool(student_id)
    name, batches = None, []
    if signed_in:
        scope = Scope(tenant_id=tenant_id, student_id=student_id, today=today)
        from app.db.models import Student

        s = db.get(Student, (tenant_id, student_id))
        name = s.name if s else None
        batches = _batch_facts(db, scope)
    category = triage.get("category", "other")
    priority = "urgent" if reason in ("urgent_priority", "qa_security") and triage.get("priority") != "urgent" else triage.get("priority", "low")
    team = cfg.escalation_teams.get(category, cfg.default_team)
    return EscalationBrief(
        ticket_id=ticket_id, tenant_id=tenant_id, team=team, priority=priority, sla_hours=cfg.sla_hours.get(priority, 48),
        reason=reason, category=category, sentiment=triage.get("sentiment", "neutral"), language=triage.get("language", "en"),
        signed_in=signed_in, student_id=student_id, student_name=name, batches=batches,
        excerpt=mask_pii(" ".join(text.split()))[:EXCERPT_CHARS], trail=trail,
        suggested_next_step=NEXT_STEP.get(reason, NEXT_STEP.get(category, DEFAULT_NEXT_STEP)),
        contact_note=("Signed-in student: reply through the student's registered contact." if signed_in else
                      "Not signed in: no contact details are on file for this chat. The student was told to contact support directly."),
    )
