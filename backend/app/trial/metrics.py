"""Trial metrics (M5), computed only from rows and events that were actually persisted. Nothing is estimated and a value that cannot be known is None.

What is counted (trial calls = calls made while Trial Verification Mode was on, `calls.trial_mode`):
  call counts            total, AI-handled (never taken over), ongoing, ended normally, failed, ended after a human intervention
  human involvement      calls with human LISTENERS (one or more staff listened), human TAKEOVERS (an intervention), intervention rate = takeover calls / trial calls
  who                    unique signed-in callers (anonymous callers cannot be told apart, so they are only counted as calls) and anonymous calls
  time                   average duration of calls that ended; average length of a human intervention (takeover -> end)
  escalations            calls whose ticket was escalated to the human team during or after the call
  consent                offered / granted / declined / withdrawn / undecided and the rates over `offered` (silence is "undecided", never consent)
  recording              recordings by status; calls with a recording
  intervention feedback  by state and review status; the reasons staff gave (category counts only: no text)
Aggregates only: no names, no ids, no text. Listeners, interventions and recorded calls are distinct measures and are never mixed."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calls.state import LIVE
from app.db.models import Call, CallConsent, CallEvent, CallRecording, Escalation, InterventionFeedback

CHUNK = 400


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 1) if xs else None


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _chunks(ids: list[str]):
    for i in range(0, len(ids), CHUNK):
        yield ids[i:i + CHUNK]


def compute(db: Session, tenant_id: str, since: datetime, until: datetime, *, max_rows: int = 5000) -> dict[str, Any]:
    """`since` inclusive, `until` exclusive (timezone-aware UTC)."""
    base = [Call.tenant_id == tenant_id, Call.created_at >= since, Call.created_at < until]
    calls = list(db.execute(select(Call).where(*base, Call.trial_mode.is_(True)).order_by(Call.created_at).limit(max_rows + 1)).scalars())
    truncated = len(calls) > max_rows
    calls = calls[:max_rows]
    outside = db.execute(select(func.count()).select_from(Call).where(*base, Call.trial_mode.is_(False))).scalar_one()
    ids = [c.id for c in calls]
    tickets = {c.ticket_id for c in calls}

    kinds: dict[str, set[str]] = {}
    takeover_events = 0
    listener_sessions = 0
    for part in _chunks(ids):
        for call_id, kind in db.execute(select(CallEvent.call_id, CallEvent.kind).where(CallEvent.tenant_id == tenant_id, CallEvent.call_id.in_(part),
                                        CallEvent.kind.in_(("listen_started", "takeover_succeeded")))).all():
            kinds.setdefault(kind, set()).add(call_id)
            if kind == "takeover_succeeded":
                takeover_events += 1
            else:
                listener_sessions += 1
    consents = [r for part in _chunks(ids) for r in db.execute(select(CallConsent).where(CallConsent.tenant_id == tenant_id, CallConsent.call_id.in_(part))).scalars()]
    recs = [r for part in _chunks(ids) for r in db.execute(select(CallRecording).where(CallRecording.tenant_id == tenant_id, CallRecording.call_id.in_(part))).scalars()]
    fbs = [r for part in _chunks(ids) for r in db.execute(select(InterventionFeedback).where(InterventionFeedback.tenant_id == tenant_id,
                                                                                             InterventionFeedback.call_id.in_(part))).scalars()]
    esc: dict[str, datetime] = {}
    for part in _chunks(sorted(tickets)):
        for tid, created in db.execute(select(Escalation.ticket_id, Escalation.created_at).where(Escalation.tenant_id == tenant_id, Escalation.ticket_id.in_(part))).all():
            esc[tid] = _aware(created)

    total = len(calls)
    intervened = [c for c in calls if c.intervention_owner is not None or c.takeover_at is not None]
    terminal = [c for c in calls if c.status in ("ended", "failed")]
    ended = [c for c in calls if c.status == "ended"]
    durations = [(_aware(c.ended_at) - _aware(c.created_at)).total_seconds() for c in ended if c.ended_at and c.created_at]
    int_durations = [(_aware(c.ended_at) - _aware(c.takeover_at)).total_seconds() for c in intervened if c.status in ("ended", "failed") and c.ended_at and c.takeover_at]
    escalated = sum(1 for c in calls if c.ticket_id in esc and esc[c.ticket_id] is not None and esc[c.ticket_id] >= _aware(c.created_at))
    signed = {c.student_id for c in calls if c.student_id}

    by_c = {s: sum(1 for r in consents if r.status == s) for s in ("offered", "granted", "declined", "withdrawn")}
    offered = len(consents)
    ever_granted = by_c["granted"] + by_c["withdrawn"]
    rec_by = {s: sum(1 for r in recs if r.status == s) for s in ("recording", "completed", "failed", "purged")}
    reasons: dict[str, int] = {}
    for f in fbs:
        if f.state == "submitted" and f.category:
            reasons[f.category] = reasons.get(f.category, 0) + 1
    fb_state = {s: sum(1 for f in fbs if f.state == s) for s in ("awaiting", "submitted", "skipped")}
    fb_review = {s: sum(1 for f in fbs if f.review_status == s) for s in ("pending_review", "reviewed", "approved_for_update", "rejected")}

    return {
        "period": {"from": since.isoformat(), "to": until.isoformat()},
        "empty": total == 0, "truncated": truncated, "calls_outside_trial": outside,
        "calls": {
            "total": total, "ai_handled": total - len(intervened), "ongoing": sum(1 for c in calls if c.status in LIVE),
            "ended_normally": sum(1 for c in ended if c not in intervened), "failed": sum(1 for c in calls if c.status == "failed"),
            "ended_after_intervention": sum(1 for c in terminal if c in intervened), "requiring_escalation": escalated,
        },
        "human": {"calls_with_listeners": len(kinds.get("listen_started", set())), "listener_sessions": listener_sessions,
                  "takeovers": takeover_events, "calls_with_takeover": len(intervened), "intervention_rate": _rate(len(intervened), total)},
        "customers": {"unique_signed_in": len(signed), "anonymous_calls": sum(1 for c in calls if not c.student_id)},
        "durations": {"avg_call_s": _avg(durations), "calls_measured": len(durations), "avg_intervention_s": _avg(int_durations), "interventions_measured": len(int_durations)},
        "consent": {"offered": offered, "granted": by_c["granted"], "ever_granted": ever_granted, "declined": by_c["declined"], "withdrawn": by_c["withdrawn"],
                    "undecided": by_c["offered"], "not_offered": total - offered,
                    "granted_rate": _rate(ever_granted, offered), "declined_rate": _rate(by_c["declined"], offered), "withdrawn_rate": _rate(by_c["withdrawn"], ever_granted)},
        "recording": {"recordings": len(recs), "by_status": rec_by, "calls_recorded": len({r.call_id for r in recs if r.status in ("recording", "completed", "purged")})},
        "feedback": {"by_state": fb_state, "by_review_status": fb_review, "reasons": reasons,
                     "no_reason_given": fb_state["awaiting"] + fb_state["skipped"]},
        "notes": ["Counts cover calls made while Trial Verification Mode was on.", "Nothing is estimated: a figure that cannot be known is shown as n/a.",
                  "Staff feedback is a proposed correction. Only records an administrator approved are candidates for a knowledge update."],
    }
