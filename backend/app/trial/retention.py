"""Retention and purge (M5): what is eligible to be deleted, the idempotent purge job, and the status the admin screen shows.

What is purged, per tenant, by that tenant's own retention settings:
  * RECORDINGS   audio files (through the provider) and the reference to them, once `expires_at` has passed. The metadata row stays as a minimal tombstone
                 (id, call id, times, size 0, status `purged`): no audio, no storage reference. A recording on LEGAL HOLD is never touched.
  * CONSENTS     consent rows older than `retention_consent_days`, unless the call still has an unpurged or held recording.
  * FEEDBACK     intervention feedback that has been reviewed (any final review status) or skipped, older than `retention_feedback_days`. A record still
                 `pending_review` is never purged: it is waiting for a person. (Its review history goes with it.)
NOT purged: calls, events, tickets, knowledge documents, the conversation history. They hold no audio; `call_events` and `trial_events` are the audit trail.

Properties: idempotent (a second run finds nothing to do); a failure on one item (file busy, directory gone read-only) is counted, remembered on that row
(`purge_attempts`, a short `purge_error` code) and retried by the next run without blocking the others; nothing outside the recording provider's own
directory is ever deleted; the audit record of a run holds counts only."""

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.calls.state import now
from app.db.models import CallConsent, CallRecording, FeedbackReview, InterventionFeedback, TrialEvent, TrialSettings
from app.trial import settings as ts
from app.trial.recording import RecordingProvider

log = logging.getLogger(__name__)
FINAL_REVIEW = ("reviewed", "approved_for_update", "rejected")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _recordings_due(db: Session, tenant_id: str, at: datetime) -> list[CallRecording]:
    rows = db.execute(select(CallRecording).where(CallRecording.tenant_id == tenant_id, CallRecording.status.in_(("completed", "failed")),
                                                  CallRecording.expires_at.is_not(None), CallRecording.legal_hold.is_(False))).scalars().all()
    return [r for r in rows if _aware(r.expires_at) <= at]


def _consents_due(db: Session, cfg: ts.TrialConfig, at: datetime) -> list[CallConsent]:
    cutoff = at - timedelta(days=cfg.retention_consent_days)
    rows = db.execute(select(CallConsent).where(CallConsent.tenant_id == cfg.tenant_id)).scalars().all()
    keep = {r.call_id for r in db.execute(select(CallRecording).where(CallRecording.tenant_id == cfg.tenant_id, CallRecording.status != "purged")).scalars()}
    return [c for c in rows if c.call_id not in keep and _aware(c.updated_at or c.offered_at) < cutoff]


def _feedback_due(db: Session, cfg: ts.TrialConfig, at: datetime) -> list[InterventionFeedback]:
    cutoff = at - timedelta(days=cfg.retention_feedback_days)
    rows = db.execute(select(InterventionFeedback).where(InterventionFeedback.tenant_id == cfg.tenant_id)).scalars().all()
    out = []
    for f in rows:
        if f.state == "submitted" and f.review_status in FINAL_REVIEW and f.reviewed_at and _aware(f.reviewed_at) < cutoff:
            out.append(f)
        elif f.state == "skipped" and f.skipped_at and _aware(f.skipped_at) < cutoff:
            out.append(f)
    return out


def status(db: Session, tenant_id: str, provider: RecordingProvider, at: datetime | None = None) -> dict:
    """Everything the admin's retention screen shows. Counts and times only: no storage references, no content."""
    at = _aware(at) or now()
    cfg = ts.load(db, tenant_id)
    recs = db.execute(select(CallRecording).where(CallRecording.tenant_id == tenant_id)).scalars().all()
    by_status: dict[str, int] = {"recording": 0, "completed": 0, "failed": 0, "purged": 0}
    for r in recs:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    live = [r for r in recs if r.status in ("completed", "failed") and r.expires_at is not None]
    nxt = min((_aware(r.expires_at) for r in live if not r.legal_hold), default=None)
    last = db.execute(select(TrialEvent).where(TrialEvent.tenant_id == tenant_id, TrialEvent.kind == "purge_run").order_by(TrialEvent.id.desc()).limit(1)).scalars().first()
    return {
        "policy": {"recording_days": cfg.retention_recording_days, "consent_days": cfg.retention_consent_days, "feedback_days": cfg.retention_feedback_days,
                   "withdrawal_policy": cfg.withdrawal_policy},
        "provider": {"name": provider.name, "operational": bool(provider.healthy()), "recording_offered": cfg.recording_offered},
        "recordings": {"by_status": by_status, "held": sum(1 for r in recs if r.legal_hold and r.status != "purged"),
                       "eligible_now": len(_recordings_due(db, tenant_id, at)), "purge_failing": sum(1 for r in recs if r.status != "purged" and (r.purge_attempts or 0) > 0),
                       "next_expiry": nxt.isoformat() if nxt else None},
        "consents": {"eligible_now": len(_consents_due(db, cfg, at))},
        "feedback": {"eligible_now": len(_feedback_due(db, cfg, at))},
        "last_run": ({"at": last.created_at.isoformat() if last.created_at else None, "actor": last.actor, **(last.detail or {})} if last else None),
    }


def purge_recording(db: Session, provider: RecordingProvider, rec: CallRecording) -> bool:
    """Delete one recording's audio and clear its reference. True if it is now purged. Never raises: a failure is remembered on the row and retried later."""
    if rec.legal_hold or rec.status == "purged":
        return rec.status == "purged"
    try:
        if rec.storage_ref:
            provider.delete(rec.storage_ref)                      # idempotent: already gone is fine
    except Exception as e:  # noqa: BLE001
        rec.purge_attempts = (rec.purge_attempts or 0) + 1
        rec.purge_error = type(e).__name__[:80]
        log.warning("recording purge failed (%s); it will be retried", type(e).__name__)
        return False
    rec.status, rec.storage_ref, rec.purged_at, rec.size_bytes = "purged", None, now(), 0
    rec.purge_error = None
    return True


def run_purge(db: Session, provider: RecordingProvider, tenant_id: str, actor: str = "system", *, at: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """One pass of the retention job for one tenant. Safe to run any time, any number of times."""
    at = _aware(at) or now()
    cfg = ts.load(db, tenant_id)
    out = {"purged": 0, "failed": 0, "held": 0, "consents": 0, "feedback": 0, "dry_run": dry_run}
    out["held"] = db.execute(select(func.count()).select_from(CallRecording).where(CallRecording.tenant_id == tenant_id, CallRecording.status.in_(("completed", "failed")),
                                                                                   CallRecording.legal_hold.is_(True), CallRecording.expires_at.is_not(None),
                                                                                   CallRecording.expires_at <= at)).scalar_one()
    recs = _recordings_due(db, tenant_id, at)
    if dry_run:
        out["purged"], out["consents"], out["feedback"] = len(recs), len(_consents_due(db, cfg, at)), len(_feedback_due(db, cfg, at))
        return out
    for r in recs:
        if purge_recording(db, provider, r):
            out["purged"] += 1
        else:
            out["failed"] += 1
    db.flush()
    cons = _consents_due(db, cfg, at)
    for c in cons:
        db.delete(c)
    out["consents"] = len(cons)
    fbs = _feedback_due(db, cfg, at)
    for f in fbs:
        db.execute(delete(FeedbackReview).where(FeedbackReview.tenant_id == f.tenant_id, FeedbackReview.call_id == f.call_id))
        db.delete(f)
    out["feedback"] = len(fbs)
    if out["purged"] or out["failed"] or out["consents"] or out["feedback"]:      # a run that found nothing leaves no trace (an hourly job would drown the audit trail)
        ts.audit(db, tenant_id, "purge_run", actor, **{k: v for k, v in out.items() if k != "dry_run"})
    return out


def run_all(provider: RecordingProvider, actor: str = "system") -> dict[str, dict]:
    """The scheduled job: every tenant that has trial settings, each in its own transaction (one tenant's failure cannot stop another's)."""
    from app.db.session import session_scope
    with session_scope() as db:
        tenants = list(db.execute(select(TrialSettings.tenant_id)).scalars())
    results: dict[str, dict] = {}
    for t in tenants:
        try:
            with session_scope() as db:
                results[t] = run_purge(db, provider, t, actor)
        except Exception as e:  # noqa: BLE001
            log.warning("retention job failed for a tenant (%s)", type(e).__name__)
            results[t] = {"error": type(e).__name__}
    return results


def sweep_recordings(db: Session) -> int:
    """At startup no recording can really be in progress (the audio lives in this process): mark any left `recording` as failed so retention removes it."""
    rows = db.execute(select(CallRecording).where(CallRecording.status == "recording")).scalars().all()
    for r in rows:
        cfg = ts.load(db, r.tenant_id)
        stamp = now()
        r.status, r.stop_reason, r.stopped_at, r.expires_at = "failed", "server_restart", stamp, stamp + timedelta(days=cfg.retention_recording_days)
    return len(rows)


def set_hold(db: Session, tenant_id: str, recording_id: str, hold: bool, reason: str | None, actor: str) -> CallRecording:
    rec = db.get(CallRecording, (tenant_id, recording_id))
    if rec is None:
        raise LookupError(recording_id)
    if rec.status == "purged":
        raise ValueError("this recording has already been purged")
    if hold and not (reason and reason.strip()):
        raise ValueError("a legal hold needs a reason")
    rec.legal_hold = hold
    rec.hold_reason = (reason or "").strip()[:120] if hold else None
    ts.audit(db, tenant_id, "legal_hold_set" if hold else "legal_hold_cleared", actor, recording_id=recording_id)
    return rec


