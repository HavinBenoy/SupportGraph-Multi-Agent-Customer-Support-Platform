"""Test rig for M4: a real VoiceSession + CallHub + LiveCall, with fake caller callbacks (no sockets), fake STT/TTS/VAD and the sandbox DB.

The rig records EVERYTHING the caller would receive together with who owned the call at that instant, so the ownership invariant can be asserted
on the real timeline of a scenario: no AI frame while the staff member owns the call, no staff frame while the AI does."""

import asyncio
import struct

from app.auth.tokens import Principal
from app.calls.hub import AI, STAFF, CallHub, HubError, StaffConn
from app.config import Settings
from app.db.models import Call, CallEvent
from app.db.session import session_scope
from app.voice import session as sess
from app.voice.session import VoiceSession
from app.voice.stt import FakeStt
from app.voice.tts import FakeTts
from app.voice.vad import EnergyVad
from tests.voice.audio import chunks, tone, utterance

T = "qspiders_sample"
AI_RATE = 22050
HDR = struct.Struct("<II")


def staff(name="staff1", role="staff", tenant=T) -> Principal:
    return Principal(tenant, role, name, None)


class GatedTts(FakeTts):
    """Speaks in many small chunks with a short real delay, so a test can act while the AI is audibly mid-response."""

    def __init__(self, chunks_n=60, delay=0.01):
        super().__init__()
        self.n, self.delay = chunks_n, delay

    def synthesize(self, text):
        import time
        self.spoken.append(text)
        for _ in range(self.n):
            yield b"\x01\x00" * 1500
            time.sleep(self.delay)


class Rig:
    def __init__(self, *, tts=None, script=None, run_turn=None, ticket="s-ticketabcdef0123.tag", student="stu_asha", **settings_kw):
        self.s = Settings(_env_file=None, voice_provider="fake", voice_pace_audio=False, voice_filler_after_ms=0, **settings_kw)
        self.hub = CallHub(self.s)
        self.ticket, self.student = ticket, student
        self.tts = tts or FakeTts()
        self.stt = FakeStt(script or ["what is the fee for the python course"])
        self.turns: list[str] = []
        self.run_turn = run_turn or self._default_turn
        self.json: list[dict] = []            # everything sent to the caller as JSON
        self.frames: list[tuple[str, int, int, str, int]] = []   # (source, turn, seq, owner_at_send, epoch_at_send) for every audio frame the caller received
        self.caller_closed = False
        self.fail_caller_send = False
        self.live = None
        self.session: VoiceSession | None = None

    def _default_turn(self, text):
        self.turns.append(text)
        return {"reply": f"Answer to: {text}.", "status": "answered", "citations": [], "escalation_id": None, "ticket_id": self.ticket}

    async def _caller_json(self, m):
        if self.fail_caller_send:
            raise RuntimeError("socket gone")
        self.json.append(m)

    async def _caller_bytes(self, b):
        if self.fail_caller_send:
            raise RuntimeError("socket gone")
        turn, seq = HDR.unpack_from(b)
        live = self.live
        src = "staff" if (live is not None and live.staff_turn and turn == live.staff_turn) else "ai"   # turn ids are unique per response, staff's included
        self.frames.append((src, turn, seq, live.owner if live else "?", live.epoch if live else -1))

    async def _close_caller(self):
        self.caller_closed = True

    async def start(self):
        self.live = await self.hub.open(T, self.ticket, self.student, send_json=self._caller_json, send_bytes=self._caller_bytes, close_caller=self._close_caller)
        self.session = VoiceSession(s=self.s, vad=EnergyVad(), stt=self.stt, tts=self.tts, run_turn=lambda t: self.run_turn(t), send_json=self.live.from_ai_json,
                                    send_bytes=self.live.from_ai_bytes, tap_caller=self.live.tap_caller_audio)
        self.live.attach_session(self.session)
        await self.session.start()
        await self.live.mark_connected()
        return self

    async def stop(self):
        if self.live and not self.live.ended:
            await self.live.end("system", "test_done")
        elif self.session:
            await self.session.close()

    # ---- the caller ------------------------------------------------------------------------------------------------------
    async def caller_says(self, pcm: bytes | None = None):
        """Feed one utterance (or any audio) from the caller's microphone; does not wait for the AI."""
        for c in chunks(pcm if pcm is not None else utterance()):
            await self.session.feed_audio(c)

    async def until(self, pred, timeout=3.0):
        async def w():
            while not pred():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(w(), timeout)

    def count(self, kind):
        return sum(1 for m in self.json if m["type"] == kind)

    async def ai_turn(self, timeout=5.0):
        """The caller speaks one utterance and the AI's spoken answer is complete (audio_end of a non-staff response)."""
        before = sum(1 for m in self.json if m["type"] == "audio_end" and not m.get("staff"))
        for c in chunks(utterance()):
            await self.session.feed_audio(c)
        await self.until(lambda: sum(1 for m in self.json if m["type"] == "audio_end" and not m.get("staff")) > before, timeout)

    # ---- staff -------------------------------------------------------------------------------------------------------------
    async def attach(self, name="staff1", role="staff", maxq=None) -> StaffConn:
        c = StaffConn(staff(name, role), maxq or self.s.calls_listener_queue)
        await self.live.join(c)
        return c

    @staticmethod
    def drain(conn: StaffConn):
        out = []
        while conn.out._d:
            out.append(conn.out._d.popleft())
        return out

    def drain_audio(self, conn):
        items = self.drain(conn)
        return [i[1] for i in items if i[0] == "bytes"], [i[1] for i in items if i[0] == "json"]

    async def speak_as_staff(self, conn, n_frames=5, epoch=None):
        """Send n 40 ms microphone frames from `conn`; returns the list of results."""
        res = []
        for _ in range(n_frames):
            res.append(await self.live.staff_audio(conn, self.live.epoch if epoch is None else epoch, b"\x05\x00" * 640))
        return res


def events(call_id, tenant=T):
    with session_scope() as db:
        return [(e.kind, e.actor, dict(e.detail)) for e in db.query(CallEvent).filter_by(tenant_id=tenant, call_id=call_id).order_by(CallEvent.id)]


def row(call_id, tenant=T):
    with session_scope() as db:
        c = db.get(Call, (tenant, call_id))
        db.expunge(c)
        return c
