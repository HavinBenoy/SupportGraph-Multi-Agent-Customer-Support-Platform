"""One browser voice call, independent of the transport (the WebSocket route feeds it; tests feed it directly).

    audio in --> Endpointer (Silero VAD) --utterance--> STT --text--> run_turn (the SAME support graph as the text chat)
                                                                              |
    audio out <-- TTS (sentence by sentence) <-- speakable(reply) <-----------+

Everything that decides what the caller may see or do (tenant, student, ticket, RBAC, escalation, approvals) lives in `run_turn`, which the
route builds from the text chat's own code. This class adds no powers: it converts audio to text and the reply back to audio.
Blocking work (STT, the graph, TTS) runs in worker threads so the event loop keeps receiving audio, which is what makes barge-in possible.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.config import Settings
from app.voice.stt import Transcriber
from app.voice.text import sentences, speakable
from app.voice.tts import Synthesizer
from app.voice.vad import FRAME_MS, SAMPLE_RATE, Endpointer, FrameVad

log = logging.getLogger(__name__)
MAX_FRAME_BYTES = 64 * 1024         # one websocket audio message; a normal browser frame is ~1-4 KB
BURST_MS = 5000                    # audio the caller may send ahead of real time (a test, a lagging network flush)
MAX_QUEUED_UTTERANCES = 2
SPEECH_CHUNK = 8192                 # bytes of PCM per outgoing websocket message

SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


class SessionLimit(Exception):
    """The call hit a limit (time, turns, audio rate) and must end. The message is safe to show."""


@dataclass
class VoiceSession:
    s: Settings
    vad: FrameVad
    stt: Transcriber
    tts: Synthesizer
    run_turn: Callable[[str], dict]          # sync; returns {"reply","status","citations","escalation_id","ticket_id"} (see route)
    send_json: SendJson
    send_bytes: SendBytes
    stt_prompt: str = ""
    turns: int = 0
    speaking: bool = False
    _started: float = field(default_factory=time.monotonic)
    _audio_ms: float = 0.0
    _cancel: asyncio.Event = field(default_factory=asyncio.Event)
    _queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(MAX_QUEUED_UTTERANCES))
    _worker: asyncio.Task | None = None
    _closed: bool = False

    def __post_init__(self) -> None:
        self.ep = Endpointer(self.vad, threshold=self.s.voice_vad_threshold, silence_ms=self.s.voice_silence_ms,
                             min_utterance_ms=self.s.voice_min_utterance_ms, max_utterance_s=self.s.voice_max_utterance_s)
        self._normal_start_ms = self.ep.start_ms

    # ---- lifecycle -------------------------------------------------------------------------------------------------------
    async def start(self) -> None:
        self._worker = asyncio.create_task(self._work(), name="voice-worker")

    async def close(self) -> None:
        self._closed = True
        self._cancel.set()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def _check_limits(self) -> None:
        if time.monotonic() - self._started > self.s.voice_max_session_s:
            raise SessionLimit("This call has reached its time limit. Please start a new call.")
        if self.turns >= self.s.voice_max_turns:
            raise SessionLimit("This call has reached its turn limit. Please start a new call.")

    # ---- audio in --------------------------------------------------------------------------------------------------------
    async def feed_audio(self, pcm16: bytes) -> None:
        if len(pcm16) > MAX_FRAME_BYTES:
            raise SessionLimit("Audio message too large.")
        self._check_limits()
        # Abuse guard: a real microphone delivers audio in real time. Far more audio than wall-clock time means a script, not a person.
        self._audio_ms += len(pcm16) / 2 * 1000 / SAMPLE_RATE
        elapsed_ms = (time.monotonic() - self._started) * 1000
        if self._audio_ms > 3 * elapsed_ms + BURST_MS:
            raise SessionLimit("Audio is arriving faster than real time.")
        self.ep.start_ms = max(self._normal_start_ms, self.s.voice_barge_in_ms) if self.speaking else self._normal_start_ms
        for ev in self.ep.feed(pcm16):
            if ev.kind == "speech_start":
                await self.send_json({"type": "speech_start"})
                if self.speaking:  # barge-in: the caller talks over the bot
                    self._cancel.set()
                    await self.send_json({"type": "interrupted"})
            elif ev.kind == "utterance":
                if self._queue.full():
                    await self.send_json({"type": "notice", "detail": "Still working on your previous question."})
                else:
                    self._queue.put_nowait(ev.audio)
            elif ev.kind == "too_short":
                await self.send_json({"type": "notice", "detail": "too_short"})

    async def submit_text(self, text: str) -> None:
        """Typed fallback inside a voice call (same path after transcription)."""
        self._check_limits()
        if not self._queue.full():
            self._queue.put_nowait(text)

    # ---- the worker: one utterance at a time ------------------------------------------------------------------------------
    async def _work(self) -> None:
        while not self._closed:
            item = await self._queue.get()
            try:
                await self._handle(item)
            except SessionLimit as e:
                await self.send_json({"type": "error", "detail": str(e), "fatal": True})
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - one bad turn must not kill the call
                log.exception("voice turn failed (%s)", type(e).__name__)
                await self.send_json({"type": "error", "detail": "Sorry, something went wrong with that turn. Please try again."})

    async def _handle(self, item: bytes | str) -> None:
        timing: dict[str, float] = {}
        if isinstance(item, bytes):
            await self.send_json({"type": "transcribing"})
            try:
                tr = await asyncio.to_thread(self.stt.transcribe, item, prompt=self.stt_prompt)
            except Exception as e:  # noqa: BLE001 - model missing/corrupt
                log.warning("stt failed (%s: %s)", type(e).__name__, e)
                await self.send_json({"type": "error", "detail": "Speech recognition is not available right now."})
                return
            timing["stt_ms"] = round(tr.seconds * 1000)
            text = tr.text
            if not text or tr.no_speech_prob > 0.85:  # silence/noise that slipped past the VAD
                await self.send_json({"type": "transcript", "text": "", "ignored": True})
                return
        else:
            text = item
        await self.send_json({"type": "transcript", "text": text, "final": True})
        self._check_limits()
        self.turns += 1
        await self.send_json({"type": "thinking"})
        t0 = time.perf_counter()
        out = await asyncio.to_thread(self.run_turn, text)
        timing["turn_ms"] = round((time.perf_counter() - t0) * 1000)
        await self.send_json({"type": "reply", **out, "timing": dict(timing)})
        await self._speak(out["reply"], timing)

    # ---- audio out -------------------------------------------------------------------------------------------------------
    async def _speak(self, reply: str, timing: dict) -> None:
        spoken = speakable(reply, max_chars=self.s.voice_max_reply_chars)
        if not spoken:
            return
        self._cancel.clear()
        self.speaking = True
        t0 = time.perf_counter()
        first = True
        try:
            await self.send_json({"type": "audio_start", "sample_rate": self.tts.sample_rate})
            for sent in sentences(spoken):
                if self._cancel.is_set():
                    break
                gen = self.tts.synthesize(sent)
                while not self._cancel.is_set():
                    chunk = await asyncio.to_thread(next, gen, None)
                    if chunk is None:
                        break
                    if first:
                        timing["tts_first_ms"] = round((time.perf_counter() - t0) * 1000)
                        first = False
                    for i in range(0, len(chunk), SPEECH_CHUNK):
                        if self._cancel.is_set():
                            break
                        await self.send_bytes(chunk[i:i + SPEECH_CHUNK])
        except Exception as e:  # noqa: BLE001
            log.warning("tts failed (%s: %s)", type(e).__name__, e)
            await self.send_json({"type": "error", "detail": "Speech output is not available; the reply is in the chat."})
        finally:
            self.speaking = False
            await self.send_json({"type": "audio_end", "interrupted": self._cancel.is_set(), "timing": dict(timing)})
            self._cancel.clear()


def frame_ms() -> int:
    return FRAME_MS
