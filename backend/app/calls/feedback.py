"""Intervention feedback (M4.1): what the staff member who interrupted a call says the AI got wrong.

The record is a PROPOSED correction. Nothing in this module (or anywhere in M4.1/M5) writes it into the knowledge base: an administrator reviews it
(`pending_review -> reviewed | approved_for_update | rejected`) and a later milestone (M6/M7) reads only `approved_for_update` rows.

Who may do what (always checked against the database row, never against anything the browser sends):
  * create       the takeover transaction (state `awaiting`), so the owner and the time are persisted even if nobody ever fills the form
  * submit/edit/skip   ONLY the account stored as the intervention owner, and only after the call has ended. Administrators are not exempt.
  * review       administrators only, never on their own feedback, never on a record that is not submitted
Free text is stored here and nowhere else: events carry the category only, logs carry nothing."""

from datetime import datetime

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from app.auth.tokens import Principal
from app.calls import service as svc
from app.calls.state import TERMINAL, now
from app.db.models import FEEDBACK_CATEGORIES, Call, FeedbackReview, InterventionFeedback

MAX_TEXT = 2000
MIN_WRONG = 5
CATEGORY_LABELS = {
    "incorrect_information": "Incorrect factual information", "outdated_knowledge": "Outdated knowledge", "misleading_response": "Misleading response",
    "policy_compliance": "Policy or compliance issue", "customer_dissatisfaction": "Customer dissatisfaction", "missing_knowledge": "Missing knowledge", "other": "Other",
}
REVIEW_FLOW = {
    "pending_review": frozenset({"reviewed", "approved_for_update", "rejected"}),
    "reviewed": frozenset({"approved_for_update", "rejected"}),
    "approved_for_update": frozenset({"rejected"}),
    "rejected": frozenset(),
}
REVIEW_TARGETS = ("reviewed", "approved_for_update", "rejected")


class FeedbackError(Exception):
    """A refused operation. `status` is the HTTP status; `detail` is safe to show."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def clean(text: str | None, limit: int = MAX_TEXT) -> str | None:
    """Trim, drop control characters (keeping newlines), cap the length. None/blank -> None."""
    if text is None:
        return None
    t = "".join(ch for ch in text if ch == "\n" or ch >= " ").strip()
    return t[:limit] or None


def create_awaiting(db: Session, call: Call) -> None:
    """In the takeover transaction: record the intervention (owner + time). Idempotent."""
    if db.get(InterventionFeedback, (call.tenant_id, call.id)) is None:
        db.add(InterventionFeedback(tenant_id=call.tenant_id, call_id=call.id, staff_subject=call.intervention_owner or "", state="awaiting",
                                    intervened_at=call.takeover_at or now()))


def _row(db: Session, tenant_id: str, call_id: str) -> tuple[Call, InterventionFeedback]:
    call = db.get(Call, (tenant_id, call_id))
    fb = db.get(InterventionFeedback, (tenant_id, call_id)) if call else None
    if call is None or fb is None:
        raise FeedbackError(404, "no intervention was recorded for this call")      # same answer for "no such call" and "other tenant"
    return call, fb


def _owner_only(call: Call, fb: InterventionFeedback, p: Principal) -> None:
    if not p.subject or p.subject != fb.staff_subject or p.subject != call.intervention_owner:
        raise FeedbackError(403, "only the staff member who took over this call can write its feedback")


def get_for_owner_or_admin(db: Session, p: Principal, call_id: str) -> dict:
    call, fb = _row(db, p.tenant_id, call_id)
    if p.role != "admin" and p.subject != fb.staff_subject:
        raise FeedbackError(403, "only the staff member who took over this call can see its feedback")
    return view(call, fb, with_text=True)


def _validate(category: str, what: str | None, suggested: str | None) -> tuple[str, str, str | None]:
    if category not in FEEDBACK_CATEGORIES:
        raise FeedbackError(422, "choose one of the listed categories")
    w = clean(what)
    if not w or len(w) < MIN_WRONG:
        raise FeedbackError(422, f"describe what was wrong (at least {MIN_WRONG} characters)")
    return category, w, clean(suggested)


def submit(db: Session, p: Principal, call_id: str, *, category: str, what_was_wrong: str, suggested_correction: str | None = None,
           related_message_id: str | None = None, related_index: int | None = None, edit: bool = False) -> dict:
    call, fb = _row(db, p.tenant_id, call_id)
    _owner_only(call, fb, p)
    if call.status not in TERMINAL:
        raise FeedbackError(409, "the feedback form opens when the call has ended")
    cat, wrong, sugg = _validate(category, what_was_wrong, suggested_correction)
    rel_id = clean(related_message_id, 80)
    rel_ix = related_index if isinstance(related_index, int) and 0 <= related_index <= 100000 else None
    stamp = now()
    if edit:
        if fb.state != "submitted":
            raise FeedbackError(409, "there is no submitted feedback to edit")
        if fb.review_status != "pending_review":
            raise FeedbackError(409, "this feedback has been reviewed and can no longer be edited")
        res = db.execute(update(InterventionFeedback).where(InterventionFeedback.tenant_id == p.tenant_id, InterventionFeedback.call_id == call_id,
                                                            InterventionFeedback.state == "submitted", InterventionFeedback.review_status == "pending_review")
                         .values(category=cat, what_was_wrong=wrong, suggested_correction=sugg, related_message_id=rel_id, related_index=rel_ix, updated_at=stamp)
                         .execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise FeedbackError(409, "this feedback was just reviewed and can no longer be edited")
        svc.add_event(db, p.tenant_id, call_id, "feedback_edited", p.subject, role=p.role, category=cat)
    else:
        # The compare-and-set on `state` is what makes a double click (or two tabs) produce ONE submission.
        res = db.execute(update(InterventionFeedback).where(InterventionFeedback.tenant_id == p.tenant_id, InterventionFeedback.call_id == call_id,
                                                            InterventionFeedback.state.in_(("awaiting", "skipped")))
                         .values(state="submitted", category=cat, what_was_wrong=wrong, suggested_correction=sugg, related_message_id=rel_id,
                                 related_index=rel_ix, submitted_at=stamp, updated_at=stamp, review_status="pending_review")
                         .execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise FeedbackError(409, "feedback has already been submitted for this call (you can edit it until it is reviewed)")
        svc.add_event(db, p.tenant_id, call_id, "feedback_submitted", p.subject, role=p.role, category=cat)
    db.expire_all()
    return view(call, db.get(InterventionFeedback, (p.tenant_id, call_id)), with_text=True)


def skip(db: Session, p: Principal, call_id: str) -> dict:
    call, fb = _row(db, p.tenant_id, call_id)
    _owner_only(call, fb, p)
    if call.status not in TERMINAL:
        raise FeedbackError(409, "the feedback form opens when the call has ended")
    if fb.state == "submitted":
        raise FeedbackError(409, "feedback has already been submitted for this call")
    if fb.state == "awaiting":
        res = db.execute(update(InterventionFeedback).where(InterventionFeedback.tenant_id == p.tenant_id, InterventionFeedback.call_id == call_id,
                                                            InterventionFeedback.state == "awaiting").values(state="skipped", skipped_at=now())
                         .execution_options(synchronize_session=False))
        if res.rowcount == 1:
            svc.add_event(db, p.tenant_id, call_id, "feedback_skipped", p.subject, role=p.role)
        db.expire_all()
    return view(call, db.get(InterventionFeedback, (p.tenant_id, call_id)), with_text=True)


def review(db: Session, p: Principal, call_id: str, to_status: str, note: str | None) -> dict:
    """An administrator moves a SUBMITTED record through the review flow. This only changes the record's status: the knowledge base is never touched."""
    if p.role != "admin":
        raise FeedbackError(403, "admin access required")
    call, fb = _row(db, p.tenant_id, call_id)
    if to_status not in REVIEW_TARGETS:
        raise FeedbackError(422, "choose reviewed, approved_for_update or rejected")
    if fb.state != "submitted" or fb.review_status is None:
        raise FeedbackError(409, "only submitted feedback can be reviewed")
    if p.subject == fb.staff_subject:
        raise FeedbackError(403, "feedback you wrote yourself must be reviewed by another administrator")
    cur = fb.review_status
    if to_status not in REVIEW_FLOW.get(cur, frozenset()):
        raise FeedbackError(409, f"a {cur.replace('_', ' ')} record cannot move to {to_status.replace('_', ' ')}")
    stamp = now()
    res = db.execute(update(InterventionFeedback).where(InterventionFeedback.tenant_id == p.tenant_id, InterventionFeedback.call_id == call_id,
                                                        InterventionFeedback.review_status == cur)
                     .values(review_status=to_status, reviewed_by=p.subject, reviewed_at=stamp, review_note=clean(note, 500))
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise FeedbackError(409, "another administrator just reviewed this record; refresh")
    db.add(FeedbackReview(tenant_id=p.tenant_id, call_id=call_id, reviewer=p.subject, from_status=cur, to_status=to_status, note=clean(note, 500)))
    db.expire_all()
    return view(call, db.get(InterventionFeedback, (p.tenant_id, call_id)), with_text=True, admin=True)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt.replace(tzinfo=None).isoformat() + "Z") if dt.tzinfo is None else dt.isoformat()


def view(call: Call, fb: InterventionFeedback, *, with_text: bool, admin: bool = False) -> dict:
    ended = call.status in TERMINAL
    out = {
        "call_id": call.id, "short_id": call.id[2:10], "call_status": call.status, "call_ended": ended, "state": fb.state,
        "form_open": ended and fb.state in ("awaiting", "skipped"),
        "category": fb.category, "category_label": CATEGORY_LABELS.get(fb.category or ""), "review_status": fb.review_status,
        "intervened_at": _iso(fb.intervened_at), "submitted_at": _iso(fb.submitted_at), "updated_at": _iso(fb.updated_at), "skipped_at": _iso(fb.skipped_at),
        "reviewed_at": _iso(fb.reviewed_at), "related_index": fb.related_index, "related_message_id": fb.related_message_id,
        "editable": fb.state == "submitted" and fb.review_status == "pending_review" and ended,
    }
    if with_text:
        out.update({"what_was_wrong": fb.what_was_wrong, "suggested_correction": fb.suggested_correction, "review_note": fb.review_note,
                    "staff": fb.staff_subject, "reviewed_by": fb.reviewed_by})
    return out


def list_for_staff(db: Session, p: Principal, limit: int = 100) -> list[dict]:
    """The caller's OWN intervention records (newest first): forms waiting after an ended call, submitted ones with their review status, skipped ones."""
    rows = db.execute(select(Call, InterventionFeedback).join(InterventionFeedback, and_(InterventionFeedback.tenant_id == Call.tenant_id,
                      InterventionFeedback.call_id == Call.id)).where(InterventionFeedback.tenant_id == p.tenant_id, InterventionFeedback.staff_subject == p.subject)
                      .order_by(InterventionFeedback.intervened_at.desc()).limit(limit)).all()
    return [view(c, f, with_text=True) for c, f in rows]


def list_for_admin(db: Session, tenant_id: str, *, review_status: str | None = None, limit: int = 200) -> list[dict]:
    q = select(Call, InterventionFeedback).join(InterventionFeedback, and_(InterventionFeedback.tenant_id == Call.tenant_id, InterventionFeedback.call_id == Call.id)
                                                ).where(InterventionFeedback.tenant_id == tenant_id, InterventionFeedback.state == "submitted")
    if review_status:
        q = q.where(InterventionFeedback.review_status == review_status)
    rows = db.execute(q.order_by(InterventionFeedback.submitted_at.desc()).limit(limit)).all()
    return [view(c, f, with_text=True, admin=True) for c, f in rows]


def counts(db: Session, tenant_id: str, since: datetime | None = None, until: datetime | None = None) -> dict:
    """Counts by state and review status over interventions in a period (by intervention time). Numbers only."""
    q = select(InterventionFeedback.state, InterventionFeedback.review_status, func.count()).where(InterventionFeedback.tenant_id == tenant_id)
    if since is not None:
        q = q.where(InterventionFeedback.intervened_at >= since)
    if until is not None:
        q = q.where(InterventionFeedback.intervened_at < until)
    by_state: dict[str, int] = {"awaiting": 0, "submitted": 0, "skipped": 0}
    by_review: dict[str, int] = {"pending_review": 0, "reviewed": 0, "approved_for_update": 0, "rejected": 0}
    for state, rs, n in db.execute(q.group_by(InterventionFeedback.state, InterventionFeedback.review_status)).all():
        by_state[state] = by_state.get(state, 0) + n
        if rs:
            by_review[rs] = by_review.get(rs, 0) + n
    return {"by_state": by_state, "by_review_status": by_review}
