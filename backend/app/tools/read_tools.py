"""Read-only tools for an enrolled student. They run freely: they cannot change anything.

Docstrings below are shown to the LLM as the tool description, so they are written for it.
All data access goes through app.db.repo, which requires a trusted Scope (tenant + student).
The LLM never supplies the student or tenant; it can only name a batch, and only the student's own batches resolve.
"""

from datetime import date

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.db import repo
from app.db.session import session_scope
from app.guardrails.validators import normalize_batch_id
from app.tools.scope import require_scope

# One message for "doesn't exist", "someone else's", "other tenant's" and "withdrawn".
# Different messages would let an attacker probe which batch ids exist elsewhere.
_NOT_FOUND = {"error": "Batch not found for this account."}
_BAD_ID = {"error": "Invalid batch id. Batch ids look like PYF-101."}


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _resolve(db, scope, batch_id: str | None):
    """The student's own enrolments, optionally narrowed to one batch. Returns (rows, error_dict_or_None)."""
    canonical = None
    if batch_id:
        canonical = normalize_batch_id(batch_id)
        if canonical is None:
            return [], _BAD_ID
    rows = repo.get_my_enrollments(db, scope, canonical)
    if canonical is not None and not rows:
        return [], _NOT_FOUND
    return rows, None


@tool
def get_my_batches(config: RunnableConfig) -> dict:
    """List the batches the current student is enrolled in, with course, mode (online/offline), centre, timing
    and enrolment status. Use this when the student has not said which batch they mean."""
    scope = require_scope(config)
    with session_scope() as db:
        rows, _ = _resolve(db, scope, None)
        return {"batches": [
            {"batch_id": b.id, "course": c.name, "brand": c.brand, "mode": b.mode, "centre": b.centre,
             "timing": b.timing, "start_date": _iso(b.start_date), "enrollment_status": e.status}
            for e, b, c in rows
        ]}


@tool
def get_my_schedule(batch_id: str, config: RunnableConfig) -> dict:
    """Show the next scheduled classes (up to 5, with date and topic) for one of the current student's batches,
    by batch id (e.g. PYF-101). Only active enrolments have an upcoming schedule."""
    scope = require_scope(config)
    canonical = normalize_batch_id(batch_id)
    if canonical is None:
        return _BAD_ID
    with session_scope() as db:
        rows, err = _resolve(db, scope, canonical)
        if err:
            return err
        e, b, c = rows[0]
        if e.status != "active":
            return {"batch_id": b.id, "enrollment_status": e.status, "upcoming": [],
                    "note": f"Your enrolment is '{e.status}', so no upcoming classes are listed. Contact the support team."}
        sessions = repo.upcoming_sessions(db, scope, b.id, limit=5)
        return {"batch_id": b.id, "course": c.name, "mode": b.mode, "centre": b.centre, "timing": b.timing,
                "enrollment_status": e.status,
                "upcoming": [{"date": _iso(s.session_date), "topic": s.topic} for s in sessions]}


@tool
def get_my_attendance(batch_id: str, config: RunnableConfig) -> dict:
    """Report the current student's attendance in one of their batches, by batch id (e.g. PYF-101):
    sessions held, sessions attended, percentage, and whether it is below the institute's published threshold.
    This only reports the figures; it does not decide consequences, approve make-ups or change records."""
    scope = require_scope(config)
    canonical = normalize_batch_id(batch_id)
    if canonical is None:
        return _BAD_ID
    with session_scope() as db:
        rows, err = _resolve(db, scope, canonical)
        tenant = repo.get_tenant(db, scope)
        if err or tenant is None:  # unknown tenant fails closed with the same message
            return err or _NOT_FOUND
        e, b, _ = rows[0]
        held, present = repo.attendance_counts(db, scope, e.id)
        if held == 0:
            return {"batch_id": b.id, "enrollment_status": e.status, "sessions_held": 0,
                    "note": "No classes have been recorded yet for this batch."}
        pct = round(100 * present / held, 1)
        threshold = tenant.attendance_threshold_pct  # the threshold comes from tenant data, never from the LLM
        below = present * 100 < threshold * held     # exact integer comparison: 75% against a 75% threshold is NOT below
        return {"batch_id": b.id, "enrollment_status": e.status, "sessions_held": held, "sessions_attended": present,
                "attendance_pct": pct, "threshold_pct": threshold, "below_threshold": below,
                "note": ("This is below the published threshold. Please contact the support team about next steps."
                         if below else "This meets the published threshold.")}


@tool
def list_my_assessments(batch_id: str, config: RunnableConfig) -> dict:
    """List assessments (quizzes, assignments, projects, mock interviews) for one of the current student's batches,
    by batch id (e.g. PYF-101): title, due date, and the student's own status and score."""
    scope = require_scope(config)
    canonical = normalize_batch_id(batch_id)
    if canonical is None:
        return _BAD_ID
    with session_scope() as db:
        rows, err = _resolve(db, scope, canonical)
        if err:
            return err
        e, b, _ = rows[0]
        items = []
        for a, r in repo.my_assessments(db, scope, e.id, b.id):
            if r is not None:
                status = r.status
            else:
                status = "upcoming" if a.due_date >= scope.today else "not_recorded"
            items.append({"assessment_id": a.id, "title": a.title, "kind": a.kind, "due_date": _iso(a.due_date),
                          "max_score": a.max_score, "status": status, "score": None if r is None else r.score})
        return {"batch_id": b.id, "assessments": items}
