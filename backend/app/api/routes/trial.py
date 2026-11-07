"""Trial Verification Mode API (M5).

Administrators only (tenant from the signed token; another tenant's data is never reachable):
  GET/PUT /api/admin/trial                 settings (PUT needs `expected_version`; a stale one gets 409: no lost update)     GET .../events    audit trail
  GET     /api/admin/trial/metrics         ?from=YYYY-MM-DD&to=YYYY-MM-DD (UTC, inclusive; default: the last 30 days)
  GET     /api/admin/trial/feedback        intervention feedback awaiting/finished review      POST .../feedback/{call_id}/review
  GET     /api/admin/trial/retention       what is eligible, policy, provider, last run         POST .../retention/purge   (dry_run supported)
  GET     /api/admin/trial/recordings      metadata only (never a storage reference)           POST .../recordings/{id}/hold      legal hold on/off
  GET     /api/admin/trial/recordings/{id}/audio/{track}     an authorised download (audited)
Staff and administrators:
  GET     /api/staff/trial                 is trial mode on (a read-only flag, nothing else)
Nothing here writes into the knowledge base: feedback review only changes the record's status."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import select

from app.api.deps import require_admin, require_staff
from app.auth.tokens import Principal
from app.calls import feedback as fb
from app.db.models import CallRecording
from app.db.session import session_scope
from app.trial import metrics as mx
from app.trial import retention as rt
from app.trial import settings as ts
from app.trial.recording import TRACKS

admin_router = APIRouter(prefix="/api/admin/trial", tags=["trial"])
staff_router = APIRouter(prefix="/api/staff/trial", tags=["trial"])


def _provider(request: Request):
    return request.app.state.recording_provider


def _guard(fn):
    """Run in one transaction; map the modules' refusals to HTTP."""
    with session_scope() as db:
        try:
            return fn(db)
        except (ts.SettingsError, fb.FeedbackError) as e:
            raise HTTPException(e.status, e.detail) from None


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return (dt.replace(tzinfo=None).isoformat() + "Z") if dt.tzinfo is None else dt.isoformat()


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt = Field(ge=0)
    enabled: StrictBool | None = None                 # strict: the string "yes" must never switch the trial on
    recording_offered: StrictBool | None = None
    disclosure_text: str | None = Field(default=None, max_length=1000)
    recording_notice: str | None = Field(default=None, max_length=2000)
    retention_recording_days: StrictInt | None = None
    retention_consent_days: StrictInt | None = None
    retention_feedback_days: StrictInt | None = None
    withdrawal_policy: str | None = Field(default=None, max_length=20)


def _view(db, request: Request, tenant_id: str) -> dict:
    cfg = ts.load(db, tenant_id)
    prov = _provider(request)
    return {"settings": cfg.public(), "recording": {"provider": prov.name, "operational": bool(prov.healthy()),
                                                    "note": "Recording is only possible when a provider is configured on the server AND recording is offered below."
                                                    if prov.name != "none" else "No recording provider is configured on this server: calls are never recorded."}}


@admin_router.get("")
async def get_settings(request: Request, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: _view(db, request, p.tenant_id))


@admin_router.put("")
async def put_settings(body: SettingsIn, request: Request, p: Principal = Depends(require_admin)) -> dict:
    patch = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    patch = {k: v for k, v in patch.items() if v is not None}
    prov = _provider(request)

    def work(db):
        cfg = ts.update_settings(db, p, patch, body.expected_version, provider_available=bool(prov.healthy()))
        return {**_view(db, request, p.tenant_id), "changed_to": cfg.version}

    out = await asyncio.to_thread(_guard, work)
    if "enabled" in patch or "recording_offered" in patch:
        await request.app.state.call_hub.trial_changed(p.tenant_id)           # live calls stop recording at once if it is no longer allowed
    return out


@admin_router.get("/events")
async def events(limit: Annotated[int, Query(ge=1, le=500)] = 100, p: Principal = Depends(require_admin)) -> list[dict]:
    return await asyncio.to_thread(_guard, lambda db: ts.list_events(db, p.tenant_id, limit))


def _period(frm: str | None, to: str | None) -> tuple[datetime, datetime]:
    try:
        end_d = date.fromisoformat(to) if to else datetime.now(timezone.utc).date()
        start_d = date.fromisoformat(frm) if frm else end_d - timedelta(days=29)
    except ValueError:
        raise HTTPException(422, "dates must look like 2026-11-07") from None
    if start_d > end_d:
        raise HTTPException(422, "'from' must not be after 'to'")
    if (end_d - start_d).days > 366:
        raise HTTPException(422, "the period can be at most one year")
    utc = timezone.utc
    return datetime(start_d.year, start_d.month, start_d.day, tzinfo=utc), datetime(end_d.year, end_d.month, end_d.day, tzinfo=utc) + timedelta(days=1)


@admin_router.get("/metrics")
async def metrics(request: Request, from_: Annotated[str | None, Query(alias="from")] = None, to: str | None = None,
                  p: Principal = Depends(require_admin)) -> dict:
    since, until = _period(from_, to)
    mx_rows = request.app.state.settings.trial_metrics_max_rows
    return await asyncio.to_thread(_guard, lambda db: mx.compute(db, p.tenant_id, since, until, max_rows=mx_rows))


@admin_router.get("/feedback")
async def feedback_list(status: Annotated[str | None, Query(max_length=24)] = None, p: Principal = Depends(require_admin)) -> dict:
    if status is not None and status not in fb.REVIEW_FLOW:
        raise HTTPException(422, "unknown review status")

    def work(db):
        return {"items": fb.list_for_admin(db, p.tenant_id, review_status=status), "counts": fb.counts(db, p.tenant_id), "categories": fb.CATEGORY_LABELS}
    return await asyncio.to_thread(_guard, work)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(max_length=24)
    note: str | None = Field(default=None, max_length=500)


@admin_router.post("/feedback/{call_id}/review")
async def feedback_review(call_id: str, body: ReviewIn, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: fb.review(db, p, call_id, body.status, body.note))


@admin_router.get("/retention")
async def retention_status(request: Request, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: rt.status(db, p.tenant_id, _provider(request)))


class PurgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: StrictBool = False


@admin_router.post("/retention/purge")
async def retention_purge(request: Request, body: PurgeIn | None = None, p: Principal = Depends(require_admin)) -> dict:
    dry = bool(body and body.dry_run)
    prov = _provider(request)
    return await asyncio.to_thread(_guard, lambda db: rt.run_purge(db, prov, p.tenant_id, p.subject, dry_run=dry))


def _rec_view(r: CallRecording) -> dict:
    return {"id": r.id, "call_id": r.call_id, "status": r.status, "started_at": _iso(r.started_at), "stopped_at": _iso(r.stopped_at), "stop_reason": r.stop_reason,
            "size_bytes": r.size_bytes, "expires_at": _iso(r.expires_at), "legal_hold": bool(r.legal_hold), "hold_reason": r.hold_reason, "purged_at": _iso(r.purged_at),
            "purge_attempts": r.purge_attempts, "purge_error": r.purge_error, "consent_policy_version": r.consent_policy_version, "provider": r.provider,
            "tracks": list(TRACKS) if r.status == "completed" else []}              # NOTE: the storage reference is deliberately not part of any response


@admin_router.get("/recordings")
async def recordings(limit: Annotated[int, Query(ge=1, le=200)] = 100, p: Principal = Depends(require_admin)) -> list[dict]:
    def work(db):
        rows = db.execute(select(CallRecording).where(CallRecording.tenant_id == p.tenant_id).order_by(CallRecording.started_at.desc()).limit(limit)).scalars().all()
        return [_rec_view(r) for r in rows]
    return await asyncio.to_thread(_guard, work)


class HoldIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hold: StrictBool
    reason: str | None = Field(default=None, max_length=120)


@admin_router.post("/recordings/{recording_id}/hold")
async def recording_hold(recording_id: str, body: HoldIn, p: Principal = Depends(require_admin)) -> dict:
    def work(db):
        try:
            return _rec_view(rt.set_hold(db, p.tenant_id, recording_id, body.hold, body.reason, p.subject))
        except LookupError:
            raise HTTPException(404, "recording not found") from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
    return await asyncio.to_thread(_guard, work)


@admin_router.get("/recordings/{recording_id}/audio/{track}")
async def recording_audio(recording_id: str, track: str, request: Request, p: Principal = Depends(require_admin)):
    if track not in TRACKS:
        raise HTTPException(404, "no such track")
    prov = _provider(request)

    def work(db):
        r = db.get(CallRecording, (p.tenant_id, recording_id))
        if r is None or r.status != "completed" or not r.storage_ref:
            raise HTTPException(404, "recording not available")
        path = prov.track_path(r.storage_ref, track)
        if path is None:
            raise HTTPException(404, "recording not available")
        ts.audit(db, p.tenant_id, "recording_accessed", p.subject, recording_id=recording_id, track=track)
        return str(path)

    path = await asyncio.to_thread(_guard, work)
    return FileResponse(path, media_type="audio/wav", filename=f"{recording_id}-{track}.wav", headers={"Cache-Control": "no-store"})


@staff_router.get("")
async def staff_status(p: Principal = Depends(require_staff)) -> dict:
    cfg = await asyncio.to_thread(_guard, lambda db: ts.load(db, p.tenant_id))
    return {"enabled": cfg.enabled, "recording_offered": cfg.enabled and cfg.recording_offered}
