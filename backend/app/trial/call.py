"""Trial Verification Mode for ONE live call: the spoken + written disclosure, the caller's consent decisions, and starting/stopping the recorder.

The disclosure is played by the assistant's own voice as a system notice (it is not part of the conversation and never reaches the language model) and sent as
text with the consent options. Recording starts ONLY inside `_start_recording`, which only `decide("granted")` calls, which only runs for an explicit decision
message from the caller's own call connection. Nothing else (speech, silence, a staff action, an admin setting) can start it."""

import asyncio
import logging

from app.calls import service as svc
from app.calls.hub import LiveCall, _db
from app.db.models import CallRecording
from app.trial import consent as cs
from app.trial import settings as ts
from app.trial.recording import CallRecorder, RecordingProvider, RecordingUnavailable, create_row, finish_row, new_recording_id

log = logging.getLogger(__name__)


class TrialCall:
    def __init__(self, live: LiveCall, cfg: ts.TrialConfig, provider: RecordingProvider):
        self.live, self.cfg, self.provider = live, cfg, provider
        self.offered = False
        self.lock = asyncio.Lock()

    # ---- start of the call -----------------------------------------------------------------------------------------------------
    async def start(self) -> None:
        live, cfg = self.live, self.cfg
        recording = None
        if cfg.recording_offered and self.provider.healthy():
            try:
                await _db(lambda db: cs.offer(db, live.tenant_id, live.id, cfg.consent_policy_version))
                self.offered = True
                recording = {"offered": True, "policy_version": cfg.consent_policy_version, "notice": cfg.notice_text(), "retention_days": cfg.retention_recording_days}
            except Exception:  # noqa: BLE001 - never offer something we could not record in the database
                log.warning("recording consent could not be offered")
        await live._to_caller({"type": "trial_notice", "disclosure": cfg.disclosure_text, "recording": recording or {"offered": False}})
        live.hub.spawn(self._speak_disclosure())

    async def _speak_disclosure(self) -> None:
        live = self.live
        ok = False
        try:
            ok = await live.play_notice(self.cfg.disclosure_text, owner="ai", epoch=live.epoch, kind="disclosure")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            ok = False
        await live._event("trial_notice_played", "system", status="spoken" if ok else "text_only")

    # ---- the caller's decisions ----------------------------------------------------------------------------------------------------
    async def handle(self, msg: dict) -> None:
        """A `{"type":"consent","decision":...,"policy_version":N}` message from the caller's browser."""
        live = self.live
        decision, version = msg.get("decision"), msg.get("policy_version")
        if decision not in ("granted", "declined", "withdrawn") or not isinstance(version, int) or isinstance(version, bool):
            await live._to_caller({"type": "consent", "error": "That choice was not understood. Nothing was recorded."})
            return
        async with self.lock:
            if live.ended:
                return
            if not self.offered:
                await live._to_caller({"type": "consent", "error": "Recording is not available on this call."})
                return
            if decision == "granted":
                fresh = await _db(lambda db: ts.load(db, live.tenant_id))
                if not (fresh.enabled and fresh.recording_offered and self.provider.healthy()):
                    await live._to_caller({"type": "consent", "error": "Recording is not available right now."})
                    return
            try:
                status = await _db(lambda db: cs.decide(db, live.tenant_id, live.id, decision, version))
            except cs.ConsentError as e:
                await live._to_caller({"type": "consent", "error": e.detail})
                return
            except Exception:  # noqa: BLE001 - the consent could not be written: record nothing, say so
                log.warning("consent could not be recorded")
                await live._to_caller({"type": "consent", "error": "Your choice could not be saved, so nothing is being recorded. Please try again."})
                return
            if status == "granted" and live.recorder is None:
                await self._start_recording()
            elif status == "withdrawn":
                await self._stop_recording("consent_withdrawn", apply_policy=True)
            await live._to_caller({"type": "consent", "status": status, "recording": live.recorder is not None})

    async def _start_recording(self) -> None:
        live = self.live
        rec_id = new_recording_id()
        try:
            ref, handle = await asyncio.to_thread(self.provider.open, live.tenant_id, rec_id)
        except RecordingUnavailable:
            await live._event("recording_failed", "system", reason="storage_unavailable")
            await live._to_caller({"type": "recording", "state": "off", "reason": "storage_unavailable"})
            return
        try:
            await _db(lambda db: create_row(db, live.tenant_id, live.id, rec_id, self.provider.name, ref, self.cfg.consent_policy_version))
        except Exception:  # noqa: BLE001 - no metadata row means no recording: remove what the provider created
            handle.abort()
            try:
                await asyncio.to_thread(self.provider.delete, ref)
            except Exception:  # noqa: BLE001
                log.warning("could not remove a recording that never started")
            await live._to_caller({"type": "recording", "state": "off", "reason": "storage_unavailable"})
            return
        tenant, retention_of = live.tenant_id, self._retention_days

        async def finish(status: str, reason: str, size: int) -> None:
            await _db(lambda db: finish_row(db, tenant, rec_id, status, reason, size, retention_of(db)))

        rate = live.session.tts.sample_rate if live.session else 22050
        live.recorder = CallRecorder(rec_id, handle, rate, finish)          # from this line (and not before) the live call feeds audio to it
        await live._event("recording_started", "system", recording_id=rec_id, policy_version=self.cfg.consent_policy_version)
        await live._to_caller({"type": "recording", "state": "on"})

    def _retention_days(self, db) -> int:
        return ts.load(db, self.live.tenant_id).retention_recording_days

    async def _stop_recording(self, reason: str, *, apply_policy: bool) -> None:
        live = self.live
        rec, live.recorder = live.recorder, None                            # synchronous: no frame reaches it after this line
        if rec is not None:
            try:
                await rec.stop(reason)
            except Exception:  # noqa: BLE001
                log.warning("recording could not be finalised")
            await live._event("recording_stopped", "system", reason=reason, recording_id=rec.id)
        if apply_policy:
            await self._apply_withdrawal_policy()
        await live._to_caller({"type": "recording", "state": "off", "reason": reason})

    async def _apply_withdrawal_policy(self) -> None:
        """Consent withdrawn: with the `delete` policy (the default) everything recorded on this call is deleted now; otherwise it is kept only until it expires."""
        live = self.live

        def apply(db):
            from app.trial.retention import purge_recording
            cfg = ts.load(db, live.tenant_id)
            if cfg.withdrawal_policy != "delete":
                return 0
            from sqlalchemy import select
            n = 0
            for r in db.execute(select(CallRecording).where(CallRecording.tenant_id == live.tenant_id, CallRecording.call_id == live.id,
                                                            CallRecording.status.in_(("completed", "failed")))).scalars():
                n += 1 if purge_recording(db, self.provider, r) else 0
            return n
        try:
            await _db(apply)
        except Exception:  # noqa: BLE001 - the retention job retries
            log.warning("recording deletion after withdrawal failed; the retention job will retry")
            await _db(lambda db: self._expire_now(db))

    def _expire_now(self, db) -> None:
        from sqlalchemy import select
        for r in db.execute(select(CallRecording).where(CallRecording.tenant_id == self.live.tenant_id, CallRecording.call_id == self.live.id,
                                                        CallRecording.status.in_(("completed", "failed")))).scalars():
            r.expires_at = svc.now()

    async def on_settings_changed(self) -> None:
        """An administrator changed the trial settings while this call is live: if recording is no longer allowed, stop it now."""
        live = self.live
        fresh = await _db(lambda db: ts.load(db, live.tenant_id))
        if live.recorder is not None and not (fresh.enabled and fresh.recording_offered):
            async with self.lock:
                await self._stop_recording("trial_disabled", apply_policy=False)
