"""Live call hub (M4, M4.1): who owns each call's audio right now, who is listening, and the only place audio is allowed to cross between parties.

    caller ws <----> LiveCall <----> VoiceSession (the AI)            staff ws #1 (listener)
                        |                                              staff ws #2 (listener, maybe the one that took over)
                        +-- owner: "ai" | "staff" | "none"   epoch: int (bumped by every ownership change)

M4.1: ownership moves ONE way. ai -> staff (a takeover) -> the end of the call. There is no way back to the AI (the operation does not exist: no method, no route,
no socket message), and while staff own the call ONLY the staff member recorded as the intervention owner can speak, end it, or write its feedback: other
staff and administrators may keep listening but every control attempt is refused (and audited as `control_denied`).

THE INVARIANT: the caller never hears the AI and a staff member at the same time, and nothing from a previous owner is heard after the owner changed.
How it is enforced (each layer on its own is enough; they are stacked on purpose):
  1. `owner` is changed in ONE synchronous block (no `await` inside), together with silencing the AI (`VoiceSession.suspend_ai` cuts the speech, drops queued
     turns) and raising the `announcing` gate.
  2. AI audio reaches the caller only through `from_ai_bytes`, which drops frames unless owner == "ai". Staff audio reaches the caller only through
     `staff_audio`, which drops frames unless owner == "staff", the sender is the owning account's microphone connection, the frame's epoch is the current one
     and the hand-over announcement is not playing.
  3. The session itself refuses to send a frame of a response that started under an older epoch.
  4. The database moves ownership with an atomic compare-and-set (app/calls/state.py); a second takeover of the same call simply loses. The intervention owner is
     written in that same transaction and read back from the database before every control action that matters.
  5. The spoken hand-over announcement is the ONLY audio between the AI's silence and the first staff word: it waits for the assistant's last words to finish
     (the session's speech lock), the staff microphone is refused until it has finished (or failed), and it stops the moment the epoch or the call changes.
All ownership changes for ONE call run under that call's asyncio lock, so listen / takeover / end cannot interleave. Listeners get their own bounded outbox: a
slow listener loses its own old audio and never slows the call. What is sent to staff: caller audio, AI audio, staff audio of other staff, short transcript lines,
ownership and announcement changes. Nothing here is logged or written to disk by the hub itself; the only audio that is ever stored is a consented recording,
written by the recorder in app/trial (M5), which this class feeds through `self.recorder` and which fails closed."""

import asyncio
import itertools
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

from app.auth.tokens import Principal
from app.calls import service as svc
from app.calls import feedback as fb
from app.calls.state import TransitionError
from app.config import Settings
from app.db.session import session_scope
from app.db.models import Call
from app.voice.session import BURST_MS, SPEECH_CHUNK, VoiceSession, frame_audio
from app.voice.text import sentences, speakable
from app.voice.vad import SAMPLE_RATE

log = logging.getLogger(__name__)

AI, STAFF, NONE = "ai", "staff", "none"
TAG_CALLER, TAG_AI, TAG_STAFF = 1, 2, 3     # first byte of every binary message sent TO a staff socket
MAX_MIC_EVENTS = 100                        # staff_mic_* audit events kept per call (a toggling microphone cannot flood the trail)
STAFF_AUDIO_RATE = 16000                    # the staff microphone is captured at 16 kHz mono PCM16 (the same worklet as the caller's)


class HubError(Exception):
    """A refused operation. `status` is the HTTP status the API maps it to; the message is safe to show."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _run(fn):
    with session_scope() as db:
        return fn(db)


async def _db(fn):
    return await asyncio.to_thread(_run, fn)


class Outbox:
    """Bounded FIFO to one staff socket. When full, the OLDEST audio is dropped (a listener wants the present, not a backlog); control messages are kept."""

    def __init__(self, maxlen: int):
        self.maxlen, self.dropped = maxlen, 0
        self._d: deque = deque()
        self._ev = asyncio.Event()

    def put(self, item: tuple[str, object], audio: bool = False) -> None:
        if len(self._d) >= self.maxlen:
            for i, (kind, _) in enumerate(self._d):
                if kind == "bytes":
                    del self._d[i]
                    self.dropped += 1
                    break
            else:
                if audio:
                    self.dropped += 1
                    return
                self._d.popleft()
        self._d.append(item)
        self._ev.set()

    async def get(self) -> tuple[str, object]:
        while not self._d:
            self._ev.clear()
            await self._ev.wait()
        return self._d.popleft()


class StaffConn:
    """One authenticated staff WebSocket attached to one call."""

    _ids = itertools.count(1)

    def __init__(self, principal: Principal, maxq: int):
        self.id = next(self._ids)
        self.principal = principal
        self.out = Outbox(maxq)
        self.closed = False
        self.stale_frames = 0

    def send(self, msg: dict) -> None:
        if not self.closed:
            self.out.put(("json", msg))

    def audio(self, data: bytes) -> None:
        if not self.closed:
            self.out.put(("bytes", data), audio=True)

    def close(self, reason: str) -> None:
        if not self.closed:
            self.out.put(("close", reason))
            self.closed = True


def _staff_view(msg: dict) -> dict | None:
    """What of a message meant for the caller is also shown to staff: audio control and the text of the conversation, nothing else."""
    t = msg.get("type")
    if t == "audio_start":
        return {"type": "ai_audio_start", "turn_id": msg.get("turn_id"), "sample_rate": msg.get("sample_rate")}
    if t == "audio_end":
        return {"type": "ai_audio_end", "turn_id": msg.get("turn_id"), "interrupted": bool(msg.get("interrupted"))}
    if t == "transcript" and msg.get("final") and isinstance(msg.get("text"), str) and msg["text"]:
        return {"type": "transcript", "speaker": "caller", "text": msg["text"][:500]}
    if t == "reply" and not msg.get("stale") and isinstance(msg.get("reply"), str) and msg["reply"]:
        return {"type": "transcript", "speaker": "ai", "text": msg["reply"][:500]}
    return None


class LiveCall:
    def __init__(self, hub: "CallHub", s: Settings, tenant_id: str, call_id: str, ticket_id: str, student_id: str | None,
                 send_caller_json: Callable[[dict], Awaitable[None]], send_caller_bytes: Callable[[bytes], Awaitable[None]],
                 close_caller: Callable[[], Awaitable[None]]):
        self.hub, self.s = hub, s
        self.tenant_id, self.id, self.ticket_id, self.student_id = tenant_id, call_id, ticket_id, student_id
        self._send_json, self._send_bytes, self._close_caller = send_caller_json, send_caller_bytes, close_caller
        self.session: VoiceSession | None = None
        self.owner, self.epoch, self.staff = NONE, 0, None
        self.ended = False
        self.announcing = False                  # M4.1: the hand-over announcement is playing: no staff audio may reach the caller yet
        self.end_hint = ""                       # why the route is about to close the caller socket ("goodbye", ...)
        self.conns: dict[int, StaffConn] = {}
        self.mic_conn: int | None = None
        self.staff_turn = 0
        self.staff_seq = 0
        self._mic_started = 0.0
        self._mic_ms = 0.0
        self._mic_events = 0
        self.lock = asyncio.Lock()
        self._grace: asyncio.Task | None = None
        self._announce_task: asyncio.Task | None = None
        self.rejected: dict[str, int] = {}
        self.recorder = None                     # M5: app.trial.recording.CallRecorder while (and only while) the caller's consent is granted
        self.trial = None                        # M5: app.trial.call.TrialCall (disclosure + consent) when Trial Verification Mode is on

    # ---- helpers ---------------------------------------------------------------------------------------------------------
    def attach_session(self, session: VoiceSession) -> None:
        self.session = session

    def snapshot(self) -> dict:
        return {"owner": self.owner, "epoch": self.epoch, "owner_staff": self.staff, "listeners": len(self.conns),
                "listening_staff": sorted({c.principal.subject for c in self.conns.values()}), "mic_open": self.mic_conn is not None,
                "live": not self.ended, "announcing": self.announcing}

    def _reject(self, why: str) -> str:
        self.rejected[why] = self.rejected.get(why, 0) + 1
        return why

    async def _event(self, kind: str, actor: str, **detail) -> None:
        """Audit event in its own transaction. A failure to record never breaks a call."""
        try:
            await _db(lambda db: svc.add_event(db, self.tenant_id, self.id, kind, actor, **detail))
        except Exception:  # noqa: BLE001
            log.warning("call event %s could not be recorded", kind)

    async def _to_caller(self, msg: dict) -> None:
        try:
            await self._send_json(msg)
        except Exception:  # noqa: BLE001 - the caller socket is gone; the route notices and ends the call
            pass

    def _broadcast(self, msg: dict) -> None:
        for c in self.conns.values():
            c.send(msg)

    def _ownership_msg(self) -> dict:
        return {"type": "ownership", "owner": self.owner, "epoch": self.epoch, "staff": self.staff}

    def _owner_attached(self) -> bool:
        return self.staff is not None and any(c.principal.subject == self.staff and not c.closed for c in self.conns.values())

    async def _persisted_owner(self) -> str | None:
        """The intervention owner as the DATABASE has it (the in-memory value is a cache of this; control decisions re-read it)."""
        def read(db):
            row = db.get(Call, (self.tenant_id, self.id))
            return row.intervention_owner if row is not None else None
        return await _db(read)

    def _rec(self, name: str, *args) -> None:
        """Feed the recorder (M5). Fails closed: if the recorder cannot write, recording stops and the failure is audited; the call itself is never affected."""
        r = self.recorder
        if r is None:
            return
        try:
            getattr(r, name)(*args)
        except Exception as e:  # noqa: BLE001
            self.recorder = None
            reason = str(getattr(e, "reason", "storage_error"))[:32]
            log.warning("recording stopped: the recorder failed (%s)", reason)
            self.hub.spawn(self._recording_failed(r, reason))

    async def _recording_failed(self, r, reason: str) -> None:
        try:
            await r.fail(reason)
        except Exception:  # noqa: BLE001
            log.warning("recording failure could not be recorded")
        await self._event("recording_failed", "system", reason=reason)
        await self._to_caller({"type": "recording", "state": "stopped", "reason": reason})

    # ---- the AI / caller side ------------------------------------------------------------------------------------------------
    def tap_caller_audio(self, pcm: bytes) -> None:
        """Caller microphone -> every attached staff socket (synchronous, never blocks the call) and, with consent, the recorder."""
        if self.ended:
            return
        self._rec("caller", pcm)
        if not self.conns:
            return
        data = bytes([TAG_CALLER]) + pcm
        for c in self.conns.values():
            c.audio(data)

    async def from_ai_json(self, msg: dict) -> None:
        if msg.get("type") == "audio_start" and self.owner != AI:
            self._reject("ai_audio_start")           # a late start of AI speech: never reaches the caller
            return
        await self._send_json(msg)
        view = _staff_view(msg)
        if view is not None:
            self._broadcast(view)

    async def from_ai_bytes(self, frame: bytes) -> None:
        if self.owner != AI or self.ended:
            self._reject("ai_frame")                 # layer 2 of the invariant: AI audio only while the AI owns the call
            return
        await self._send_bytes(frame)
        self._rec("ai", frame)
        data = bytes([TAG_AI]) + frame
        for c in self.conns.values():
            c.audio(data)

    # ---- connected --------------------------------------------------------------------------------------------------------
    async def mark_connected(self) -> None:
        async with self.lock:
            try:
                call = await _db(lambda db: svc.move(db, self.tenant_id, self.id, "created", "active_ai", "caller", "connected"))
                self.epoch = call.epoch
            except Exception:  # noqa: BLE001 - never leave a connected caller in silence because a row could not be written
                log.exception("could not record the call as connected; the AI keeps the call")
                self.epoch = 1
            self.owner = AI
            if self.session:
                self.session.epoch = self.epoch

    # ---- staff attach / detach -----------------------------------------------------------------------------------------------
    async def join(self, conn: StaffConn) -> None:
        async with self.lock:
            if self.ended:
                raise HubError(409, "this call has ended")
            if len(self.conns) >= self.s.calls_max_listeners:
                raise HubError(429, "too many staff are already attached to this call")
            self.conns[conn.id] = conn
            if self.owner == STAFF and conn.principal.subject == self.staff:
                self._disarm_grace()                 # the intervention owner is back: the call keeps going
            conn.send({"type": "joined", "call_id": self.id, **self.snapshot(), "you_own": self.owner == STAFF and self.staff == conn.principal.subject})
            n = len(self.conns)
        await self._event("listen_started", conn.principal.subject, role=conn.principal.role, listeners=n)

    async def leave(self, conn: StaffConn) -> None:
        async with self.lock:
            if self.conns.pop(conn.id, None) is None:
                return
            conn.closed = True
            if self.mic_conn == conn.id:
                await self._close_mic_locked("disconnected")
            n, ended = len(self.conns), self.ended
            owner_gone = self.owner == STAFF and conn.principal.subject == self.staff and not self._owner_attached()
            if owner_gone and not ended:
                self._arm_grace()                    # the intervention owner has no connection left: they have the grace period to come back
        if ended:
            return
        await self._event("listen_stopped", conn.principal.subject, role=conn.principal.role, listeners=n)
        if owner_gone:
            await self._event("participant_disconnected", conn.principal.subject, role="staff", connection="audio")
            self._broadcast({"type": "owner_disconnected", "grace_s": self.s.calls_takeover_grace_s})

    # ---- staff microphone ---------------------------------------------------------------------------------------------------
    async def mic_open(self, conn: StaffConn, epoch: int) -> str:
        """The intervention owner starts sending microphone audio. Returns "ok" or the reason it was refused."""
        async with self.lock:
            if self.ended:
                return self._reject("ended")
            if self.owner != STAFF or conn.principal.subject != self.staff:
                return self._reject("not_owner")
            if epoch != self.epoch:
                return self._reject("stale_epoch")
            if conn.id not in self.conns:
                return self._reject("not_attached")
            if self.announcing:
                return self._reject("announcing")    # the caller is still hearing the hand-over notice: wait for `announcement: finished`
            if self.mic_conn == conn.id:
                return "ok"
            if conn.principal.subject != await self._persisted_owner():
                return self._reject("not_owner")     # defence in depth: the database is the authority on who holds the call
            if self.mic_conn is not None:
                old = self.conns.get(self.mic_conn)
                if old:
                    old.send({"type": "mic", "state": "closed", "reason": "replaced"})
                await self._close_mic_locked("replaced")
            assert self.session is not None
            self.mic_conn = conn.id
            self.staff_turn = self.session.reserve_turn_id()
            self.staff_seq, self._mic_started, self._mic_ms = 0, time.monotonic(), 0.0
        await self._to_caller({"type": "audio_start", "turn_id": self.staff_turn, "sample_rate": STAFF_AUDIO_RATE, "staff": True})
        self._mic_events += 1
        if self._mic_events <= MAX_MIC_EVENTS:
            await self._event("staff_mic_started", conn.principal.subject, role="staff")
        return "ok"

    async def mic_close(self, conn: StaffConn) -> None:
        async with self.lock:
            if self.mic_conn == conn.id:
                await self._close_mic_locked("closed")

    async def _close_mic_locked(self, reason: str) -> None:
        if self.mic_conn is None:
            return
        self.mic_conn = None
        await self._to_caller({"type": "audio_end", "turn_id": self.staff_turn, "interrupted": False, "staff": True})
        self._mic_events += 1
        if self._mic_events <= MAX_MIC_EVENTS + 1:
            await self._event("staff_mic_stopped", self.staff or "staff", role="staff", reason=reason)

    async def staff_audio(self, conn: StaffConn, epoch: int, pcm: bytes) -> str:
        """One microphone message from a staff socket -> the caller. Every check is synchronous, right before the send."""
        if self.ended:
            return self._reject("ended")
        if self.owner != STAFF:
            return self._reject("not_owner")
        if self.announcing:
            return self._reject("announcing")
        if conn.principal.subject != self.staff or self.mic_conn != conn.id:
            return self._reject("not_mic")
        if epoch != self.epoch:
            conn.stale_frames += 1
            return self._reject("stale_epoch")
        if len(pcm) > self.s.calls_staff_frame_max:
            return self._reject("too_large")
        pcm = pcm[: len(pcm) - (len(pcm) % 2)]
        if not pcm:
            return "ok"
        self._mic_ms += len(pcm) / 2 * 1000 / STAFF_AUDIO_RATE
        if self._mic_ms > 3 * (time.monotonic() - self._mic_started) * 1000 + BURST_MS:
            return self._reject("rate")
        seq = self.staff_seq
        self.staff_seq += 1
        await self._send_bytes(frame_audio(self.staff_turn, seq, pcm))
        self._rec("staff", pcm)
        data = bytes([TAG_STAFF]) + pcm
        for c in self.conns.values():
            if c.id != conn.id:
                c.audio(data)
        return "ok"

    # ---- grace period: the staff member who took the call must stay reachable ------------------------------------------------
    def _arm_grace(self) -> None:
        self._disarm_grace()
        self._grace = asyncio.create_task(self._grace_run(self.epoch), name=f"call-grace-{self.id}")

    def _disarm_grace(self) -> None:
        g, self._grace = self._grace, None
        if g is not None and g is not asyncio.current_task() and not g.done():
            g.cancel()

    async def _grace_run(self, epoch: int) -> None:
        """M4.1: the intervention owner has no audio connection. If they do not come back in time the call ENDS (as failed: the hand-over did not hold).
        It is never given back to the AI and never handed to another staff member."""
        try:
            await asyncio.sleep(self.s.calls_takeover_grace_s)
            if self.owner == STAFF and self.epoch == epoch and not self._owner_attached() and not self.ended:
                await self.end("system", "staff_unreachable", failed=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("grace handling failed")

    # ---- the spoken notice (hand-over announcement, trial disclosure) -------------------------------------------------------------
    async def play_notice(self, text: str, *, owner: str, epoch: int, kind: str) -> bool:
        """Speak `text` to the CALLER with the assistant's voice, as one response of its own. True only if it played to the end.
        It never overlaps the assistant's speech (it waits for the session's speech lock) and stops, mid-word if need be, the moment the call ends or
        `owner`/`epoch` is no longer current. It is a system message: it is not part of the conversation history and never reaches the language model."""
        sess = self.session
        if sess is None:
            return False

        def current() -> bool:
            return not self.ended and self.owner == owner and self.epoch == epoch

        async with sess.speech_lock:
            if not current():
                return False
            turn = sess.reserve_turn_id()
            rate = sess.tts.sample_rate
            await self._to_caller({"type": "audio_start", "turn_id": turn, "sample_rate": rate, "notice": kind})
            ok, sent_ms, seq, t0 = True, 0.0, 0, time.perf_counter()
            lead = self.s.voice_pace_lead_ms / 1000
            try:
                for sent in sentences(speakable(text, max_chars=600)):
                    gen = iter(sess.tts.synthesize(sent))
                    while ok:
                        chunk = await asyncio.to_thread(next, gen, None)
                        if chunk is None:
                            break
                        for i in range(0, len(chunk), SPEECH_CHUNK):
                            if self.s.voice_pace_audio:          # stay a little ahead of playback so a stop really stops what is heard
                                while current() and sent_ms / 1000 - (time.perf_counter() - t0) > lead:
                                    await asyncio.sleep(0.05)
                            if not current():
                                ok = False
                                break
                            piece = chunk[i:i + SPEECH_CHUNK]
                            await self._send_bytes(frame_audio(turn, seq, piece))
                            seq += 1
                            sent_ms += len(piece) / 2 / rate * 1000
                    if not ok:
                        break
                while ok and self.s.voice_pace_audio and sent_ms / 1000 - (time.perf_counter() - t0) + 0.3 > 0:
                    if not current():
                        ok = False
                        break
                    await asyncio.sleep(0.05)                    # the tail is still audible in the browser: hold the floor until it has been heard
                ok = ok and seq > 0
            except asyncio.CancelledError:
                ok = False
                raise
            except Exception:  # noqa: BLE001
                ok = False
                log.warning("notice playback failed")
            finally:
                await self._to_caller({"type": "audio_end", "turn_id": turn, "interrupted": not ok, "notice": kind})
        return ok

    async def _announce_handover(self, epoch: int) -> None:
        """After the takeover has been granted (database + switch): tell the caller, then release the gate so the staff microphone may open.
        The gate opens whether the notice played or not (a failed announcement must never block the human), and always, even if this task is cancelled."""
        ok = False
        try:
            await self._event("announcement_started", "system", variant="v1")
            self._broadcast({"type": "announcement", "state": "started"})
            try:
                ok = await asyncio.wait_for(self.play_notice(self.s.calls_handover_announcement, owner=STAFF, epoch=epoch, kind="handover"),
                                            self.s.calls_announcement_timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a timeout or a failure: the staff member may speak; it is not an ownership problem
                ok = False
            try:
                await asyncio.wait_for(self._event("announcement_finished" if ok else "announcement_failed", "system", variant="v1"), 5)
            except Exception:  # noqa: BLE001
                pass
        finally:
            if self.epoch == epoch:
                self.announcing = False
        self._broadcast({"type": "announcement", "state": "finished" if ok else "failed"})

    # ---- ownership operations -------------------------------------------------------------------------------------------------
    async def takeover(self, p: Principal) -> dict:
        async with self.lock:
            if self.ended:
                raise HubError(409, "this call has ended")
            if self.owner == STAFF and self.staff == p.subject:
                return self.snapshot()                           # idempotent: repeating your own takeover changes nothing
            await self._event("takeover_requested", p.subject, role=p.role)
            if self.owner == STAFF:
                await self._event("takeover_failed", p.subject, reason="already_taken", role=p.role)
                raise HubError(409, "another staff member has already taken over this call")
            if self.owner != AI:
                await self._event("takeover_failed", p.subject, reason="not_active", role=p.role)
                raise HubError(409, "this call cannot be taken over right now")

            def grant(db):
                call = svc.move(db, self.tenant_id, self.id, "active_ai", "staff_takeover", p.subject, "takeover_succeeded",
                                subject=p.subject, previous_owner="ai", role=p.role)
                fb.create_awaiting(db, call)                     # the intervention owner + time, in the SAME transaction as the takeover
                return call
            try:
                call = await _db(grant)
            except TransitionError:
                await self._event("takeover_failed", p.subject, reason="state_changed", role=p.role)
                raise HubError(409, "the call changed state; refresh and try again") from None
            # ---- the switch: one synchronous block (no await) ----
            self.owner, self.staff, self.epoch, self.mic_conn = STAFF, p.subject, call.epoch, None
            cut = self.session.suspend_ai(self.epoch) if self.session else None
            self.announcing = self.session is not None
            self._broadcast(self._ownership_msg())
            snap = self.snapshot()
            if not self._owner_attached():
                self._arm_grace()
            ep = self.epoch
            # ---- end of the switch ----
            if cut is not None:
                await self._to_caller({"type": "interrupted", "turn_id": cut, "reason": "takeover"})
            await self._to_caller({"type": "ownership", "owner": STAFF, "epoch": self.epoch})
            if self.announcing:
                self._announce_task = self.hub.spawn(self._announce_handover(ep))
            return snap

    async def end_by(self, p: Principal) -> None:
        """A staff member or administrator asks to end the call. While STAFF own the call only the intervention owner may; nobody else, administrators included."""
        async with self.lock:
            if self.ended:
                return
            if self.owner == STAFF:
                persisted = await self._persisted_owner()
                if p.subject != self.staff or p.subject != persisted:
                    await self._event("control_denied", p.subject, role=p.role, action="end")
                    raise HubError(403, "only the staff member who took over this call can end it")
            await self._end_locked("ended_by_staff", p.subject, failed=False, close_caller=True)

    async def end(self, actor: str, reason: str, *, failed: bool = False, close_caller: bool = True, participant: str | None = None) -> None:
        """End the call for a SYSTEM reason (caller left, limit, error, shutdown, owner unreachable). Staff go through `end_by`."""
        async with self.lock:
            await self._end_locked(reason, actor, failed=failed, close_caller=close_caller, participant=participant)

    async def _end_locked(self, reason: str, actor: str, *, failed: bool, close_caller: bool, participant: str | None = None) -> None:
        if self.ended:
            return
        self.ended = True                                        # first: from this line no audio crosses in either direction
        self.owner, self.mic_conn, self.announcing = NONE, None, False
        self._disarm_grace()
        t = self._announce_task
        if t is not None and t is not asyncio.current_task() and not t.done():
            t.cancel()
        rec, self.recorder = self.recorder, None
        if rec is not None:
            try:
                await rec.stop(reason="call_ended")
                await self._event("recording_stopped", "system", reason="call_ended", recording_id=rec.id)
            except Exception:  # noqa: BLE001
                log.warning("recording could not be finalised")
        sess = self.session
        if sess is not None and asyncio.current_task() is not getattr(sess, "_worker", None):
            await sess.close()                                   # cancels speech, timers, the runner (and with them any pending TTS / transcription task)
        if participant:
            await self._event("participant_disconnected", participant, role=participant, connection="call")
        try:
            call = await _db(lambda db: svc.finish(db, self.tenant_id, self.id, reason=reason, actor=actor, failed=failed))
            if call is not None:
                self.epoch = call.epoch
        except Exception:  # noqa: BLE001
            log.exception("could not persist the end of call")
        for c in list(self.conns.values()):
            c.send({"type": "call_ended", "reason": reason})
            c.close("call_ended")
        self.conns.clear()
        self.hub._forget(self)
        if close_caller:
            await self._to_caller({"type": "call_end", "reason": reason})
            try:
                await self._close_caller()
            except Exception:  # noqa: BLE001
                pass


class CallHub:
    """The calls that are live in this process. One per app (`app.state.call_hub`)."""

    def __init__(self, s: Settings):
        self.s = s
        self._live: dict[tuple[str, str], LiveCall] = {}
        self._bg: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        """Run `coro` as a task that survives the cancellation of whoever started it (a closing socket handler must still finish persisting the end of its call)."""
        task = asyncio.ensure_future(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    async def open(self, tenant_id: str, ticket_id: str, student_id: str | None, *, send_json, send_bytes, close_caller, trial_mode: bool = False) -> LiveCall:
        call = await _db(lambda db: svc.create_call(db, tenant_id, ticket_id, student_id, trial_mode))
        live = LiveCall(self, self.s, tenant_id, call.id, ticket_id, student_id, send_json, send_bytes, close_caller)
        self._live[(tenant_id, call.id)] = live
        return live

    def get(self, tenant_id: str, call_id: str) -> LiveCall | None:
        """Tenant is part of the key: another tenant's call id is not found."""
        return self._live.get((tenant_id, call_id))

    def for_tenant(self, tenant_id: str) -> list[LiveCall]:
        return [c for (t, _), c in self._live.items() if t == tenant_id]

    async def trial_changed(self, tenant_id: str) -> None:
        """An administrator switched trial mode (or recording) off: every live call of the tenant stops recording NOW."""
        for live in self.for_tenant(tenant_id):
            if live.trial is not None:
                try:
                    await live.trial.on_settings_changed()
                except Exception:  # noqa: BLE001
                    log.exception("could not apply a trial settings change to a live call")

    def _forget(self, live: LiveCall) -> None:
        self._live.pop((live.tenant_id, live.id), None)

    async def shutdown(self) -> None:
        for live in list(self._live.values()):
            try:
                await live.end("system", "server_shutdown")
            except Exception:  # noqa: BLE001
                log.exception("shutdown of a call failed")
        if self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
