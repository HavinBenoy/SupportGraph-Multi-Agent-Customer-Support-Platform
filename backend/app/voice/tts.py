"""Text to speech. `PiperTts` is the real one (local ONNX voice, CPU); `FakeTts` makes a short tone per sentence for tests.
Both yield PCM16 mono chunks, so the session can start playing the first sentence while later ones are still being synthesised."""

import math
import os
import struct
import threading
import time
from collections.abc import Iterator
from typing import Protocol

from app.config import Settings


class TtsUnavailable(RuntimeError):
    pass


class Synthesizer(Protocol):
    sample_rate: int

    def synthesize(self, text: str) -> Iterator[bytes]: ...   # PCM16 little-endian mono at `sample_rate`


class PiperTts:
    _lock = threading.Lock()
    _voices: dict = {}

    def __init__(self, s: Settings) -> None:
        if not s.voice_tts_voice:
            raise TtsUnavailable("SG_VOICE_TTS_VOICE is not set. Download a voice with `make voice-setup` and put its .onnx path in your .env.")
        if not os.path.isfile(s.voice_tts_voice):
            raise TtsUnavailable(f"Piper voice file not found: {s.voice_tts_voice}. Run `make voice-setup`.")
        self.path = s.voice_tts_voice
        self.sample_rate = self._voice().config.sample_rate

    def _voice(self):
        with self._lock:
            if self.path not in self._voices:
                try:
                    from piper import PiperVoice
                except ImportError as e:
                    raise TtsUnavailable("piper-tts is not installed: pip install piper-tts") from e
                try:
                    self._voices[self.path] = PiperVoice.load(self.path)
                except Exception as e:  # noqa: BLE001
                    raise TtsUnavailable(f"could not load Piper voice ({type(e).__name__}); is the .onnx.json file beside it?") from e
            return self._voices[self.path]

    def preload(self) -> float:
        t0 = time.perf_counter()
        self._voice()
        return time.perf_counter() - t0

    def synthesize(self, text: str) -> Iterator[bytes]:
        for chunk in self._voice().synthesize(text):
            yield chunk.audio_int16_bytes


class FakeTts:
    sample_rate = 22050

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def synthesize(self, text: str) -> Iterator[bytes]:
        self.spoken.append(text)
        n = min(self.sample_rate * 2, max(2205, len(text) * 400))
        yield b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / self.sample_rate))) for i in range(n))


def get_tts(s: Settings) -> Synthesizer:
    return FakeTts() if s.voice_provider == "fake" else PiperTts(s)
