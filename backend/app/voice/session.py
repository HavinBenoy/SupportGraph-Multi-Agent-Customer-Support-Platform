"""One browser voice call, independent of the transport (the WebSocket route feeds it; tests feed it directly).

    audio in --> Endpointer (Silero VAD) --segment--> Turn (segments merged while the caller is still mid-sentence)
                                                        |  transcribe merged audio, wait a grace period that depends on the transcript
                                                        v
                                                  finalized turn --> ONE runner --> run_turn (the SAME support graph as the text chat)
                                                                                         |
    audio out <-- TTS, paced, framed with the turn id <-- speakable(reply) <-------------+   (only if the turn is still current)

Rules this class guarantees (M2.2; root causes in docs/M2_2.md):
  * one turn per complete user request: a pause inside a sentence does not start a second graph run;
  * one graph run at a time per call (a single runner task plus a lock);
  * one spoken response at a time: `_speak` holds a lock, every response has its own cancel token, every audio frame carries (turn id,
    sequence), and a response is dropped (not spoken) when a newer real question exists or the caller is still talking;
  * barge-in cancels exactly the response that is playing; nothing from it can resume;
  * `speaking` stays true while the audio is still audible in the browser (the server paces the stream at about real time), so barge-in,
    the echo guard and the next response all agree with what the caller hears.
Everything that decides what the caller may see or do (tenant, student, ticket, RBAC, escalation, approvals) lives in `run_turn`, which the
route builds from the text chat's own code. This class adds no powers: it converts audio to text and the reply back to audio.
"""

import asyncio
import json
import logging
import struct
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.config import Settings
from app.conversation.intents import ACK, AFFIRM, GREETING, INTERRUPTION, THANKS, detect_intent
from app.voice.stt import Transcriber
from app.voice.text import sentences, speakable
from app.voice.tts import Synthesizer
from app.voice.turns import Turn, completeness, grace_ms
from app.voice.vad import FRAME_MS, SAMPLE_RATE, Endpointer, FrameVad

log = logging.getLogger(__name__)
MAX_FRAME_BYTES = 64 * 1024         # one websocket audio message; a normal browser frame is ~1-4 KB
BURST_MS = 5000                    # audio the caller may send ahead of real time (a test, a lagging network flush)
SPEECH_CHUNK = 8192                 # bytes of PCM per outgoing websocket message
AUDIO_HEADER = struct.Struct("<II")  # every outgoing audio frame: uint32 turn id, uint32 sequence number, then PCM16
MUST_SPEAK = frozenset({"escalated", "awaiting_staff", "awaiting_confirmation"})  # outcomes the caller must hear even if a newer question arrived
LIGHT_INTENTS = frozenset({ACK, AFFIRM, THANKS, GREETING, INTERRUPTION})  # "okay", "thanks", "hello?" do not invalidate an answer that is on its way
WAIT_FOR_CALLER_S = 8.0             # a ready reply waits at most this long for a caller who is mid-sentence

SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


class SessionLimit(Exception):
    """The call hit a limit (time, turns, audio rate) and must end. The message is safe to show."""


def frame_audio(turn_id: int, seq: int, pcm: bytes) -> bytes:
    return AUDIO_HEADER.pack(turn_id, seq) + pcm


@dataclass
class _Speech:
    """The response that is being spoken. One per response, so a cancel can never leak into the next one."""

    turn_id: int
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    played: asyncio.Event = field(default_factory=asyncio.Event)   # the browser said it finished playing this turn

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()


@dataclass
class VoiceSession:
    s: Settings
    vad: FrameVad
    stt: Transcriber
    tts: Synthesizer
    run_turn: Callable[[str], dict]          # sync; returns {"reply","status","citations","escalation_id","ticket_id", ...} (see route)
    send_json: SendJson
    send_bytes: SendBytes
    on_end: Callable[[], Awaitable[None]] | None = None   # called after a goodbye has been spoken (the route closes the socket)
    stt_prompt: str = ""
    turns: int = 0
    speaking: bool = False
    events: deque = field(default_factory=lambda: deque(maxlen=1000))   # structured debug events (ids, sizes, timings; never words or audio)
    stats: dict = field(default_factory=lambda: {"segments": 0, "merged_turns": 0, "graph_runs": 0, "max_concurrent_graph": 0,
                                                 "stale_replies": 0, "barge_ins": 0, "overlap_prevented": 0, "turns_finalized": 0})
    _started: float = field(default_factory=time.monotonic)
    _audio_ms: float = 0.0
    _next_id: int = 1
    _pending: Turn | None = None             # the turn that is still collecting segments
    _timers: dict = field(default_factory=dict)   # turn id -> settle task (a sealed turn keeps settling while the next one starts)
    _queue: asyncio.Queue = field(init=False)
    _worker: asyncio.Task | None = None      # the single runner
    _speech: _Speech | None = None
    _speak_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _graph_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _graph_active: int = 0
    _latest_content: int = 0                 # id of the newest finalized turn that is a real question/request
    _barged: bool = False
    _closed: bool = False

    def __post_init__(self) -> None:
        self.ep = Endpointer(self.vad, threshold=self.s.voice_vad_threshold, silence_ms=self.s.voice_silence_ms,
                             min_utterance_ms=self.s.voice_min_utterance_ms, max_utterance_s=self.s.voice_max_utterance_s)
        self._normal_start_ms = self.ep.start_ms
        self._queue = asyncio.Queue(self.s.voice_max_pending_turns)

    # ---- observability -----------------------------------------------------------------------------------------------------
    def _ev(self, kind: str, turn_id: int | None = None, **fields) -> None:
        """Structured event for debugging. Only ids, sizes and timings: no transcripts, no audio, no student data."""
        rec = {"t_ms": round((time.monotonic() - self._started) * 1000), "event": kind, **({"turn_id": turn_id} if turn_id is not None else {}), **fields}
        self.events.append(rec)
        log.info("voice_event %s", json.dumps(rec, separators=(",", ":")))

    async def _send(self, msg: dict) -> None:
        if not self._closed:
            await self.send_json(msg)

    # ---- lifecycle ---------------------------------------------------------------------------------------------------------
    async def start(self) -> None:
        self._worker = asyncio.create_task(self._runner(), name="voice-runner")
        self._ev("session_start")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._ev("session_close", turns=self.turns, **{k: v for k, v in self.stats.items()})
        if self._speech:
            self._speech.cancel.set()
        tasks = [*self._timers.values(), *([self._worker] if self._worker else [])]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._timers.clear()
        self._speech = None
        self._pending = None

    def _check_limits(self) -> None:
        if time.monotonic() - self._started > self.s.voice_max_session_s:
            raise SessionLimit("This call has reached its time limit. Please start a new call.")
        if self.turns >= self.s.voice_max_turns:
            raise SessionLimit("This call has reached its turn limit. Please start a new call.")

    # ---- audio in ----------------------------------------------------------------------------------------------------------
    async def feed_audio(self, pcm16: bytes) -> None:
        if len(pcm16) > MAX_FRAME_BYTES:
            raise SessionLimit("Audio message too large.")
        self._check_limits()
        # Abuse guard: a real microphone delivers audio in real time. Far more audio than wall-clock time means a script, not a person.
        self._audio_ms += len(pcm16) / 2 * 1000 / SAMPLE_RATE
        elapsed_ms = (time.monotonic() - self._started) * 1000
        if self._audio_ms > 3 * elapsed_ms + BURST_MS:
            raise SessionLimit("Audio is arriving faster than real time.")
        # While the assistant is audible, speech must last `voice_barge_in_ms` to count: coughs, clicks and echo of its own voice do not.
        self.ep.start_ms = max(self._normal_start_ms, self.s.voice_barge_in_ms) if self.speaking else self._normal_start_ms
        for ev in self.ep.feed(pcm16):
            if ev.kind == "speech_start":
                await self._on_speech_start()
            elif ev.kind == "utterance":
                await self._on_segment(ev.audio, ev.ms)
            elif ev.kind == "too_short":
                await self._on_too_short()

    async def _on_speech_start(self) -> None:
        t = self._pending
        self._ev("vad_speech_start", t.id if t else None, speaking=self.speaking)
        await self._send({"type": "speech_start", **({"turn_id": t.id} if t else {})})
        if self.speaking:
            await self._interrupt("barge_in")
        if t is not None and not t.sealed:   # the caller is talking again: hold the pending turn until this segment (or noise) ends
            t.holding = True
            self._cancel_timer(t)

    async def _on_too_short(self) -> None:
        self._ev("vad_too_short")
        await self._send({"type": "notice", "detail": "too_short"})
        t = self._pending
        if t is not None and t.holding:      # it was only noise: carry on with the turn we were holding
            t.holding = False
            self._arm(t)

    async def _on_segment(self, audio: bytes, ms: int) -> None:
        t = self._pending
        if t is None or t.sealed:
            prev_timer = self._timers.get(t.id) if t is not None else None
            t = Turn(id=self._next_id, after_barge_in=self._barged, wait_for=prev_timer)
            self._next_id += 1
            self._barged = False
            self._pending = t
        t.segments.append(audio)
        t.segment_ms.append(ms)
        t.holding = False
        self.stats["segments"] += 1
        if t.merged:
            self.stats["merged_turns"] += 1 if len(t.segments) == 2 else 0
        if t.audio_ms >= self.s.voice_max_turn_s * 1000:
            t.sealed = True                  # resource limit: this turn is complete whatever follows
        self._ev("vad_endpoint", t.id, segment=len(t.segments), ms=ms, turn_audio_ms=t.audio_ms, merged=t.merged, sealed=t.sealed)
        self._arm(t)

    def _cancel_timer(self, t: Turn) -> None:
        task = self._timers.pop(t.id, None)
        if task and not task.done():
            task.cancel()

    def _arm(self, t: Turn) -> None:
        self._cancel_timer(t)
        self._timers[t.id] = asyncio.create_task(self._settle(t, time.monotonic()), name=f"voice-settle-{t.id}")

    async def submit_text(self, text: str) -> None:
        """Typed fallback inside a voice call: a finished turn at once; it interrupts whatever is being spoken."""
        self._check_limits()
        t = Turn(id=self._next_id, typed=True, text=text, after_barge_in=self._barged)
        self._next_id += 1
        self._barged = False
        await self._interrupt("typed")
        await self._finalize(t)

    # ---- settling: transcribe, wait for the caller to really be done, then commit ---------------------------------------------
    async def _settle(self, t: Turn, t0: float) -> None:
        if t._stt_for != t.audio_ms:
            await self._send({"type": "transcribing", "turn_id": t.id})
            try:
                tr = await asyncio.to_thread(self.stt.transcribe, t.audio, prompt=self.stt_prompt)
            except Exception as e:  # noqa: BLE001 - model missing/corrupt
                log.warning("stt failed (%s: %s)", type(e).__name__, e)
                self._drop_pending(t)
                await self._send({"type": "error", "detail": "Speech recognition is not available right now."})
                return
            t.stt_ms = round(tr.seconds * 1000)
            t._stt_for = t.audio_ms
            if not tr.text or tr.no_speech_prob > 0.85:  # silence/noise that slipped past the VAD
                self._ev("stt_empty", t.id)
                self._drop_pending(t)
                await self._send({"type": "transcript", "turn_id": t.id, "text": "", "ignored": True})
                return
            t.text = tr.text
            self._ev("stt_done", t.id, ms=t.stt_ms, chars=len(t.text), segments=len(t.segments))
            await self._send({"type": "transcript", "turn_id": t.id, "text": t.text, "final": False, "segments": len(t.segments)})
        kind = completeness(t.text)
        grace = 0 if t.sealed else grace_ms(self.s, t.text)
        wait = max(0.0, grace / 1000 - (time.monotonic() - t0))  # STT time already counts as waiting
        self._ev("grace", t.id, completeness=kind, grace_ms=grace, wait_ms=round(wait * 1000))
        if wait:
            await asyncio.sleep(wait)
        if self.ep.in_speech and not t.sealed:   # the caller is mid-sentence again: _on_segment / _on_too_short will re-arm this turn
            t.holding = True
            return
        if t.wait_for is not None and not t.wait_for.done():   # an earlier sealed turn is still settling: it goes first
            await asyncio.wait([t.wait_for])
        self._timers.pop(t.id, None)
        await self._finalize(t)

    def _drop_pending(self, t: Turn) -> None:
        if self._pending is t:
            self._pending = None
        self._timers.pop(t.id, None)

    async def _finalize(self, t: Turn) -> None:
        if self._pending is t:
            self._pending = None
        intent = detect_intent(t.text)
        t.supersedes = intent not in LIGHT_INTENTS
        if t.supersedes:
            self._latest_content = max(self._latest_content, t.id)
        self.stats["turns_finalized"] += 1
        self._ev("turn_final", t.id, **{k: v for k, v in t.describe().items() if k != "turn_id"}, supersedes=t.supersedes)
        await self._send({"type": "transcript", "turn_id": t.id, "text": t.text, "final": True, "segments": len(t.segments)})
        try:
            self._queue.put_nowait(t)
        except asyncio.QueueFull:
            self._ev("turn_dropped_queue_full", t.id)
            await self._send({"type": "notice", "detail": "Still working on your previous question."})

    # ---- the single runner: one turn at a time -----------------------------------------------------------------------------
    async def _runner(self) -> None:
        while not self._closed:
            t = await self._queue.get()
            try:
                if await self._process(t):   # the caller said goodbye: the closing has been spoken, end the call
                    await self._send({"type": "call_end", "reason": "goodbye"})
                    if self.on_end:
                        await self.on_end()
                    return
            except SessionLimit as e:
                await self._send({"type": "error", "detail": str(e), "fatal": True})
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - one bad turn must not kill the call
                log.exception("voice turn failed (%s)", type(e).__name__)
                await self._send({"type": "error", "detail": "Sorry, something went wrong with that turn. Please try again."})

    async def _process(self, t: Turn) -> bool:
        """Run one finalized turn through the graph and speak the reply if it is still the current one. True = end the call afterwards."""
        self._check_limits()
        self.turns += 1
        timing = {"stt_ms": t.stt_ms}
        await self._send({"type": "thinking", "turn_id": t.id})
        async with self._graph_lock:         # never two graph runs for one call
            self._graph_active += 1
            self.stats["max_concurrent_graph"] = max(self.stats["max_concurrent_graph"], self._graph_active)
            self.stats["graph_runs"] += 1
            self._ev("graph_start", t.id)
            t0 = time.perf_counter()
            try:
                out = await asyncio.to_thread(self.run_turn, t.text)
            finally:
                self._graph_active -= 1
        timing["turn_ms"] = round((time.perf_counter() - t0) * 1000)
        self._ev("graph_done", t.id, ms=timing["turn_ms"], status=out.get("status"), intent=out.get("intent") or "", context=out.get("context") or "")
        await self._wait_for_caller()
        stale = self._is_stale(t, out)
        kind = "continuation" if t.merged else "interruption" if t.after_barge_in else (out.get("context") or out.get("intent") or "new")
        info = {"turn_id": t.id, "segments": len(t.segments), "audio_ms": t.audio_ms, "merged": t.merged, "kind": kind}
        await self._send({"type": "reply", **out, "turn_id": t.id, "stale": stale, "timing": dict(timing), "turn": info})
        if stale:
            self.stats["stale_replies"] += 1
            self._ev("reply_stale", t.id, latest=self._latest_content)
            return False
        await self._speak(t, out["reply"], timing, must=out.get("status") in MUST_SPEAK)
        return bool(out.get("end_call")) and not self._closed and not self._is_stale(t, out)

    def _is_stale(self, t: Turn, out: dict) -> bool:
        """An ordinary answer is stale once a newer real question exists. Outcomes the caller must hear (a hand-off, an approval) never are."""
        return t.id < self._latest_content and out.get("status") not in MUST_SPEAK

    async def _wait_for_caller(self) -> None:
        """Do not start talking over a caller who is mid-sentence or whose next turn is still being settled."""
        deadline = time.monotonic() + WAIT_FOR_CALLER_S
        while (self.ep.in_speech or self._pending is not None) and time.monotonic() < deadline and not self._closed:
            await asyncio.sleep(0.05)

    # ---- audio out ---------------------------------------------------------------------------------------------------------
    async def _interrupt(self, reason: str) -> None:
        sp = self._speech
        if sp is None or sp.cancelled:
            return
        sp.cancel.set()
        self._barged = True
        self.stats["barge_ins"] += 1
        self._ev("barge_in" if reason == "barge_in" else "speech_cancelled", sp.turn_id, reason=reason)
        await self._send({"type": "interrupted", "turn_id": sp.turn_id, "reason": reason})

    def playback_ended(self, turn_id: int) -> None:
        """The browser finished playing this turn (its ack). Lets the server stop waiting for the estimated end of the audio."""
        if self._speech and self._speech.turn_id == turn_id:
            self._speech.played.set()

    @staticmethod
    async def _wait(ev: asyncio.Event, seconds: float) -> None:
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(ev.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    async def _speak(self, t: Turn, reply: str, timing: dict, must: bool = False) -> None:
        spoken = speakable(reply, max_chars=self.s.voice_max_reply_chars)
        if not spoken or self._closed:
            return
        if self._speak_lock.locked():            # a response is still audible: ours waits, the two can never overlap
            self.stats["overlap_prevented"] += 1
            self._ev("overlap_prevented", t.id)
        async with self._speak_lock:
            if self._closed or (self._is_stale_now(t) and not must):
                return
            sp = self._speech = _Speech(turn_id=t.id)
            self.speaking = True
            rate = self.tts.sample_rate
            t0 = time.perf_counter()
            sent_ms, seq, first = 0.0, 0, True
            pace = self.s.voice_pace_audio
            lead = self.s.voice_pace_lead_ms / 1000
            self._ev("tts_start", t.id, chars=len(spoken))
            try:
                await self._send({"type": "audio_start", "turn_id": t.id, "sample_rate": rate})
                self._ev("playback_start", t.id)
                for sent in sentences(spoken):
                    if sp.cancelled:
                        break
                    gen = self.tts.synthesize(sent)
                    while not sp.cancelled:
                        chunk = await asyncio.to_thread(next, gen, None)
                        if chunk is None:
                            break
                        if first:
                            timing["tts_first_ms"] = round((time.perf_counter() - t0) * 1000)
                            self._ev("tts_first_audio", t.id, ms=timing["tts_first_ms"])
                            first = False
                        for i in range(0, len(chunk), SPEECH_CHUNK):
                            piece = chunk[i:i + SPEECH_CHUNK]
                            if pace:             # stay at most `lead` ahead of playback, so a cancel really cuts the audio the caller hears
                                await self._wait(sp.cancel, sent_ms / 1000 - (time.perf_counter() - t0) - lead)
                            if sp.cancelled:
                                break
                            await self.send_bytes(frame_audio(t.id, seq, piece))
                            seq += 1
                            sent_ms += len(piece) / 2 / rate * 1000
                self._ev("tts_done", t.id, frames=seq, audio_ms=round(sent_ms))
                if pace and not sp.cancelled:    # keep "speaking" until the tail has been played (or the browser says it has)
                    both = asyncio.Event()
                    waiter = asyncio.ensure_future(self._first_of(sp, both))
                    await self._wait(both, sent_ms / 1000 - (time.perf_counter() - t0) + 0.3)
                    waiter.cancel()
            except asyncio.CancelledError:
                sp.cancel.set()
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("tts failed (%s: %s)", type(e).__name__, e)
                await self._send({"type": "error", "detail": "Speech output is not available; the reply is in the chat."})
            finally:
                self.speaking = False
                if self._speech is sp:
                    self._speech = None
                self._ev("playback_done", t.id, interrupted=sp.cancelled, audio_ms=round(sent_ms))
                await self._send({"type": "audio_end", "turn_id": t.id, "interrupted": sp.cancelled, "timing": dict(timing)})

    @staticmethod
    async def _first_of(sp: _Speech, out: asyncio.Event) -> None:
        done, pending = await asyncio.wait([asyncio.ensure_future(sp.cancel.wait()), asyncio.ensure_future(sp.played.wait())],
                                           return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        out.set()

    def _is_stale_now(self, t: Turn) -> bool:
        return t.id < self._latest_content


def frame_ms() -> int:
    return FRAME_MS
