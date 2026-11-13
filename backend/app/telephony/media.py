"""Audio conversion and buffering between a telephone line and the voice session.

Into the session: the provider's L16 at 8 or 16 kHz is converted to the session's 16 kHz (VAD/STT rate).
Out to the caller: the call hub and VoiceSession emit browser-protocol messages (audio_start {sample_rate}, binary frames `uint32 turn | uint32 seq | PCM16`,
audio_end, interrupted, ownership, call_end). `PhoneBridge` turns them into the provider's protocol:
  * strips the 8-byte header, resamples each response from its own rate (TTS voice, or 16 kHz staff microphone) to the line rate, sends ~20 ms frames
  * `interrupted`, an interrupted `audio_end`, and every `ownership` change => the provider's flush (`killAudio`), and the local buffer is dropped: after a
    barge-in or a takeover the caller never hears queued audio of the previous speaker (the hub's no-overlap invariant, carried onto the phone line)
  * `call_end` => the provider's hang-up
  * a playback marker after each response: when the provider reports it played out, the session gets its playback acknowledgement (as from a browser)
Browser-only messages (ready, transcript, reply text, metrics, trial_notice, consent) are not sent down a telephone line."""

import asyncio
import logging
import struct
from collections.abc import Awaitable, Callable

import numpy as np

log = logging.getLogger(__name__)

HEADER = struct.Struct("<II")
SESSION_RATE = 16000
FRAME_MS = 20


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    """Linear-interpolation resampling of PCM16 mono. Telephone bandwidth (8 kHz) does not need a better filter; identity when the rates match."""
    if src == dst or not pcm:
        return pcm
    x = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype="<i2").astype(np.float32)
    if x.size == 0:
        return b""
    n = max(1, int(round(x.size * dst / src)))
    pos = np.linspace(0, x.size - 1, n, dtype=np.float64)
    y = np.interp(pos, np.arange(x.size), x)
    return np.clip(np.round(y), -32768, 32767).astype("<i2").tobytes()


def strip_header(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < HEADER.size:
        raise ValueError("short audio frame")
    turn, seq = HEADER.unpack_from(frame)
    return turn, seq, frame[HEADER.size:]


class PhoneBridge:
    """One per call. `send_text` / `send_audio` write to the provider's WebSocket (JSON text, raw binary)."""

    def __init__(self, adapter, line_rate: int, send_text: Callable[[dict], Awaitable[None]], send_audio: Callable[[bytes], Awaitable[None]],
                 close: Callable[[], Awaitable[None]]):
        self.adapter, self.line_rate = adapter, line_rate
        self._send_text, self._send_audio, self._close = send_text, send_audio, close
        self._rates: dict[int, int] = {}          # turn id -> that response's sample rate
        self._pending = b""                      # line-rate PCM not yet a full frame
        self._turn: int | None = None
        self._lock = asyncio.Lock()
        self.frame_bytes = line_rate * FRAME_MS // 1000 * 2
        self.closed = False
        self.on_played: Callable[[int], None] | None = None     # turn id: the caller has heard all of it
        self.on_started: Callable[[int], None] | None = None
        self.stats = {"frames_out": 0, "kills": 0, "marks": 0}

    # ---- into the session ----------------------------------------------------------------------------------------------------------
    def caller_audio(self, pcm: bytes) -> bytes:
        return resample(pcm, self.line_rate, SESSION_RATE)

    # ---- out to the caller ---------------------------------------------------------------------------------------------------------
    async def on_json(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "audio_start" and isinstance(msg.get("turn_id"), int):
            self._rates[msg["turn_id"]] = int(msg.get("sample_rate") or SESSION_RATE)
            async with self._lock:
                self._pending, self._turn = b"", msg["turn_id"]
                await self._text(self.adapter.mark(f"s{msg['turn_id']}"))
        elif kind == "audio_end" and isinstance(msg.get("turn_id"), int):
            if msg.get("interrupted"):
                await self.kill("interrupted")
            else:
                async with self._lock:
                    await self._flush(pad=True)
                    await self._text(self.adapter.mark(f"e{msg['turn_id']}"))
            self._rates.pop(msg["turn_id"], None)
        elif kind == "interrupted":
            await self.kill("barge_in")
        elif kind == "ownership":
            await self.kill("ownership")
        elif kind == "call_end":
            await self.hang_up()

    async def on_bytes(self, frame: bytes) -> None:
        if self.closed:
            return
        try:
            turn, _seq, pcm = strip_header(frame)
        except ValueError:
            return
        rate = self._rates.get(turn, SESSION_RATE)
        async with self._lock:
            if self._turn is not None and turn != self._turn:
                self._pending, self._turn = b"", turn
            self._pending += resample(pcm, rate, self.line_rate)
            await self._flush(pad=False)

    async def _flush(self, *, pad: bool) -> None:
        fb = self.frame_bytes
        while len(self._pending) >= fb:
            out, self._pending = self._pending[:fb], self._pending[fb:]
            await self._audio(out)
        if pad and self._pending:
            await self._audio(self._pending + b"\x00" * (fb - len(self._pending)))
            self._pending = b""

    async def kill(self, reason: str) -> None:
        async with self._lock:
            self._pending = b""
            self.stats["kills"] += 1
            await self._text(self.adapter.kill_audio())

    async def hang_up(self) -> None:
        if self.closed:
            return
        await self._text(self.adapter.hang_up())
        self.closed = True
        try:
            await self._close()
        except Exception:  # noqa: BLE001 - already closed by the provider
            pass

    def on_mark(self, name: str | None) -> None:
        """The provider played the audio up to this marker."""
        if not name or len(name) < 2 or not name[1:].isdigit():
            return
        turn = int(name[1:])
        cb = self.on_started if name[0] == "s" else self.on_played if name[0] == "e" else None
        if cb is not None:
            try:
                cb(turn)
            except Exception:  # noqa: BLE001
                log.debug("playback callback failed", exc_info=True)

    async def _text(self, msg: dict) -> None:
        if self.closed:
            return
        if msg.get("type") == "mark":
            self.stats["marks"] += 1
        try:
            await self._send_text(msg)
        except Exception:  # noqa: BLE001 - the line is gone
            self.closed = True

    async def _audio(self, b: bytes) -> None:
        if self.closed:
            return
        try:
            await self._send_audio(b)
            self.stats["frames_out"] += 1
        except Exception:  # noqa: BLE001
            self.closed = True
