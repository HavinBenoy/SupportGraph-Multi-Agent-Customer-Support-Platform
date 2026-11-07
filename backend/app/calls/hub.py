"""Live call hub (M4): who owns each call's audio right now, who is listening, and the only place audio is allowed to cross between parties.

    caller ws <----> LiveCall <----> VoiceSession (the AI)            staff ws #1 (listener)
                        |                                              staff ws #2 (listener, maybe the one that took over)
                        +-- owner: "ai" | "staff" | "none"   epoch: int (bumped by every ownership change)

THE INVARIANT: the caller never hears the AI and a staff member at the same time, and nothing from a previous owner is heard after the owner changed.
How it is enforced (each layer on its own is enough; they are stacked on purpose):
  1. `owner` is changed in ONE synchronous block (no `await` inside), together with silencing the old owner (`VoiceSession.suspend_ai` cuts the
     speech, drops queued turns; a staff microphone is un-routed).
  2. AI audio reaches the caller only through `from_ai_bytes`, which drops frames unless owner == "ai". Staff audio reaches the caller only through
     `staff_audio`, which drops frames unless owner == "staff", the sender is the owning account's microphone connection and the frame's epoch
     is the current one. Between the two owners there is always a moment with owner == "none" (a return passes through it, behind a database write).
  3. The session itself refuses to send a frame of a response that started under an older epoch.
  4. The database moves ownership with an atomic compare-and-set (app/calls/state.py); a second takeover of the same call simply loses.
All ownership changes for ONE call run under that call's asyncio lock, so listen / takeover / return / end cannot interleave. Listeners get their own
bounded outbox: a slow listener loses its own old audio and never slows the call. What is sent to staff: caller audio, AI audio, staff audio of other
staff, short transcript lines of the call, ownership changes. Nothing here is logged, stored or written to disk (no recording)."""

import asyncio
import itertools
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

from app.auth.tokens import Principal
from app.calls import service as svc
from app.calls.state import TransitionError, transition
from app.config import Settings
from app.db.session import session_scope
from app.voice.session import BURST_MS, VoiceSession, frame_audio
from app.voice.vad import SAMPLE_RATE

log = logging.getLogger(__name__)

AI, STAFF, NONE = "ai", "staff", "none"
TAG_CALLER, TAG_AI, TAG_STAFF = 1, 2, 3     # first byte of every binary message sent TO a staff socket
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
        self.end_hint = ""                       # why the route is about to close the caller socket ("goodbye", ...)
        self.conns: dict[int, StaffConn] = {}
        self.mic_conn: int | None = None
        self.staff_turn = 0
        self.staff_seq = 0
        self._mic_started = 0.0
        self._mic_ms = 0.0
        self.lock = asyncio.Lock()
        self._grace: asyncio.Task | None = None
        self.rejected: dict[str, int] = {}

    # ---- helpers ---------------------------------------------------------------------------------------------------------
    def attach_session(self, session: VoiceSession) -> None:
        self.session = session

    def snapshot(self) -> dict:
        return {"owner": self.owner, "epoch": self.epoch, "owner_staff": self.staff, "listeners": len(self.conns),
                "listening_staff": sorted({c.principal.subject for c in self.conns.values()}), "mic_open": self.mic_conn is not None,
                "live": not self.ended}

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

    # ---- the AI / caller side ------------------------------------------------------------------------------------------------
    def tap_caller_audio(self, pcm: bytes) -> None:
        """Caller microphone -> every attached staff socket (synchronous, never blocks the call)."""
        if self.ended or not self.conns:
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
            conn.send({"type": "joined", "call_id": self.id, **self.snapshot(), "you_own": self.owner == STAFF and self.staff == conn.principal.subject})
            n = len(self.conns)
        await self._event("listen_started", conn.principal.subject, role=conn.principal.role, listeners=n)

    async def leave(self, conn: StaffConn) -> None:
        async with self.lock:
            if self.conns.pop(conn.id, None) is None:
                return
            conn.closed = True
            was_owner_conn = self.owner == STAFF and conn.principal.subject == self.staff
            if self.mic_conn == conn.id:
                await self._close_mic_locked("disconnected")
            n, ended = len(self.conns), self.ended
        if ended:
            return
        await self._event("listen_stopped", conn.principal.subject, role=conn.principal.role, listeners=n)
        if was_owner_conn and self.owner == STAFF:
            await self._event("participant_disconnected", conn.principal.subject, role="staff", connection="audio")

    # ---- staff microphone ---------------------------------------------------------------------------------------------------
    async def mic_open(self, conn: StaffConn, epoch: int) -> str:
        """The owning staff member starts sending microphone audio. Returns "ok" or the reason it was refused."""
        async with self.lock:
            if self.ended:
                return self._reject("ended")
            if self.owner != STAFF or conn.principal.subject != self.staff:
                return self._reject("not_owner")
            if epoch != self.epoch:
                return self._reject("stale_epoch")
            if conn.id not in self.conns:
                return self._reject("not_attached")
            if self.mic_conn == conn.id:
                return "ok"
            if self.mic_conn is not None:
                old = self.conns.get(self.mic_conn)
                if old:
                    old.send({"type": "mic", "state": "closed", "reason": "replaced"})
                await self._close_mic_locked("replaced", rearm=False)
            assert self.session is not None
            self.mic_conn = conn.id
            self.staff_turn = self.session.reserve_turn_id()
            self.staff_seq, self._mic_started, self._mic_ms = 0, time.monotonic(), 0.0
            self._disarm_grace()
        await self._to_caller({"type": "audio_start", "turn_id": self.staff_turn, "sample_rate": STAFF_AUDIO_RATE, "staff": True})
        return "ok"

    async def mic_close(self, conn: StaffConn) -> None:
        async with self.lock:
            if self.mic_conn == conn.id:
                await self._close_mic_locked("closed")

    async def _close_mic_locked(self, reason: str, rearm: bool = True) -> None:
        if self.mic_conn is None:
            return
        self.mic_conn = None
        await self._to_caller({"type": "audio_end", "turn_id": self.staff_turn, "interrupted": False, "staff": True})
        if rearm and self.owner == STAFF and not self.ended:
            self._arm_grace()        # staff still owns the call but cannot be heard: they have the grace period to come back

    async def staff_audio(self, conn: StaffConn, epoch: int, pcm: bytes) -> str:
        """One microphone message from a staff socket -> the caller. Every check is synchronous, right before the send."""
        if self.ended:
            return self._reject("ended")
        if self.owner != STAFF:
            return self._reject("not_owner")
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
        data = bytes([TAG_STAFF]) + pcm
        for c in self.conns.values():
            if c.id != conn.id:
                c.audio(data)
        return "ok"

    # ---- grace period: a staff member who took the call must stay reachable ----------------------------------------------------
    def _arm_grace(self) -> None:
        self._disarm_grace()
        self._grace = asyncio.create_task(self._grace_run(self.epoch), name=f"call-grace-{self.id}")

    def _disarm_grace(self) -> None:
        g, self._grace = self._grace, None
        if g is not None and g is not asyncio.current_task() and not g.done():
            g.cancel()

    async def _grace_run(self, epoch: int) -> None:
        try:
            await asyncio.sleep(self.s.calls_takeover_grace_s)
            if self.owner == STAFF and self.epoch == epoch and self.mic_conn is None and not self.ended:
                await self.return_to_ai(None, reason="staff_unreachable", actor="system")
        except asyncio.CancelledError:
            raise
        except HubError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("grace handling failed")

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
            try:
                call = await _db(lambda db: svc.move(db, self.tenant_id, self.id, "active_ai", "staff_takeover", p.subject, "takeover_succeeded",
                                                     subject=p.subject, previous_owner="ai", role=p.role))
            except TransitionError:
                await self._event("takeover_failed", p.subject, reason="state_changed", role=p.role)
                raise HubError(409, "the call changed state; refresh and try again") from None
            # ---- the switch: one synchronous block (no await) ----
            self.owner, self.staff, self.epoch, self.mic_conn = STAFF, p.subject, call.epoch, None
            cut = self.session.suspend_ai(self.epoch) if self.session else None
            self._broadcast(self._ownership_msg())
            snap = self.snapshot()
            self._arm_grace()
            # ---- end of the switch ----
            if cut is not None:
                await self._to_caller({"type": "interrupted", "turn_id": cut, "reason": "takeover"})
            await self._to_caller({"type": "ownership", "owner": STAFF, "epoch": self.epoch})
            return snap

    async def return_to_ai(self, p: Principal | None, *, reason: str = "returned_by_staff", actor: str | None = None) -> dict:
        """Give the call back to the AI. `p` None = the system (staff unreachable). Only the staff member holding the call, or an admin, may."""
        actor = actor or (p.subject if p else "system")
        async with self.lock:
            if self.ended:
                raise HubError(409, "this call has ended")
            if self.owner == AI:
                return self.snapshot()                           # idempotent
            if self.owner != STAFF:
                raise HubError(409, "this call is not held by staff")
            if p is not None and p.role != "admin" and p.subject != self.staff:
                raise HubError(403, "only the staff member who took over this call (or an admin) can return it")
            try:
                call = await _db(lambda db: transition(db, self.tenant_id, self.id, "staff_takeover", "returning_to_ai"))
            except TransitionError:
                raise HubError(409, "the call changed state; refresh and try again") from None
            # ---- stop staff routing (synchronous): from here no staff frame is accepted and the AI is not yet allowed ----
            self.owner, self.epoch, self.staff = NONE, call.epoch, None
            had_mic, self.mic_conn = self.mic_conn is not None, None
            self._disarm_grace()
            self._broadcast(self._ownership_msg())
            # ----
            if had_mic:
                await self._to_caller({"type": "audio_end", "turn_id": self.staff_turn, "interrupted": True, "staff": True})
            try:
                call = await _db(lambda db: svc.move(db, self.tenant_id, self.id, "returning_to_ai", "active_ai", actor, "returned_to_ai",
                                                     reason=reason, previous_owner="staff"))
            except Exception:  # noqa: BLE001 - fail safe: a call that cannot be handed back is ended, never left half-owned
                log.exception("return to AI failed; ending the call")
                await self._end_locked("return_failed", "system", failed=True, close_caller=True)
                raise HubError(500, "the call could not be returned to the AI and was ended") from None
            self.owner, self.epoch = AI, call.epoch
            if self.session:
                self.session.resume_ai(self.epoch)
            self._broadcast(self._ownership_msg())
            await self._to_caller({"type": "ownership", "owner": AI, "epoch": self.epoch})
            return self.snapshot()

    async def end(self, actor: str, reason: str, *, failed: bool = False, close_caller: bool = True, participant: str | None = None) -> None:
        async with self.lock:
            await self._end_locked(reason, actor, failed=failed, close_caller=close_caller, participant=participant)

    async def _end_locked(self, reason: str, actor: str, *, failed: bool, close_caller: bool, participant: str | None = None) -> None:
        if self.ended:
            return
        self.ended = True                                        # first: from this line no audio crosses in either direction
        self.owner, self.mic_conn = NONE, None
        self._disarm_grace()
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

    async def open(self, tenant_id: str, ticket_id: str, student_id: str | None, *, send_json, send_bytes, close_caller) -> LiveCall:
        call = await _db(lambda db: svc.create_call(db, tenant_id, ticket_id, student_id))
        live = LiveCall(self, self.s, tenant_id, call.id, ticket_id, student_id, send_json, send_bytes, close_caller)
        self._live[(tenant_id, call.id)] = live
        return live

    def get(self, tenant_id: str, call_id: str) -> LiveCall | None:
        """Tenant is part of the key: another tenant's call id is not found."""
        return self._live.get((tenant_id, call_id))

    def for_tenant(self, tenant_id: str) -> list[LiveCall]:
        return [c for (t, _), c in self._live.items() if t == tenant_id]

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
