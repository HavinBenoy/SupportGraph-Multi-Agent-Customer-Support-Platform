"""Speech to text. `FasterWhisperStt` (CPU, int8) is the real one; `FakeStt` replays a script so the whole voice path is testable without models.
The model is loaded once, lazily, and shared by all calls (loading takes seconds and ~200 MB for base.en)."""

from __future__ import annotations

import threading
from collections.abc import Callable
import time
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings

SAMPLE_RATE = 16000

class SttUnavailable(RuntimeError):
    pass

@dataclass
class Transcript:
    text: str
    seconds: float            # processing time
    audio_seconds: float
    language: str = "en"
    no_speech_prob: float = 0.0

class Transcriber(Protocol):
    def transcribe(self, pcm16: bytes, *, prompt: str = "") -> Transcript: ...

class FasterWhisperStt:
    _lock = threading.Lock()
    _models: dict = {}

    def __init__(self, s: Settings) -> None:
        self.s = s

    def _model(self):
        key = (self.s.voice_stt_model, self.s.voice_stt_compute_type, self.s.voice_stt_threads)
        with self._lock:
            if key not in self._models:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as e:
                    raise SttUnavailable("faster-whisper is not installed: pip install faster-whisper") from e
                try:
                    self._models[key] = WhisperModel(self.s.voice_stt_model, device="cpu", compute_type=self.s.voice_stt_compute_type,
                                                     cpu_threads=self.s.voice_stt_threads)
                except Exception as e:  # noqa: BLE001 - download blocked, bad name, corrupt cache
                    raise SttUnavailable(f"could not load speech model '{self.s.voice_stt_model}' ({type(e).__name__}). "
                                         "Run `make voice-setup` once with internet access.") from e
            return self._models[key]

    def preload(self) -> float:
        t0 = time.perf_counter()
        self._model()
        return time.perf_counter() - t0

    def transcribe(self, pcm16: bytes, *, prompt: str = "") -> Transcript:
        import numpy as np

        audio = np.frombuffer(pcm16, dtype="<i2").astype("float32") / 32768.0
        t0 = time.perf_counter()
        segments, info = self._model().transcribe(
            audio, language=self.s.voice_stt_language, beam_size=1, vad_filter=False, condition_on_previous_text=False,
            initial_prompt=prompt or None, without_timestamps=True, temperature=0.0)
        segs = list(segments)
        text = " ".join(x.text.strip() for x in segs).strip()
        nsp = max((x.no_speech_prob for x in segs), default=1.0)
        return Transcript(text=text, seconds=time.perf_counter() - t0, audio_seconds=len(audio) / SAMPLE_RATE,
                          language=getattr(info, "language", "en"), no_speech_prob=float(nsp))

class FakeStt:
    """Returns the scripted texts in order (the last one repeats). `calls` records the audio sizes it was given."""

    def __init__(self, script: "list[str] | Callable[[bytes], str] | None" = None) -> None:
        """`script`: texts in order, or a function of the audio (so a test can answer differently for one segment and for merged segments)."""
        self.fn = script if callable(script) else None
        self.script, self.calls = ([] if self.fn else list(script or ["what is the fee for the python course"])), []

    def transcribe(self, pcm16: bytes, *, prompt: str = "") -> Transcript:
        self.calls.append(len(pcm16))
        text = self.fn(pcm16) if self.fn else self.script[min(len(self.calls), len(self.script)) - 1]
        return Transcript(text=text, seconds=0.0, audio_seconds=len(pcm16) / 2 / SAMPLE_RATE)

def get_stt(s: Settings) -> Transcriber:
    return FakeStt() if s.voice_provider == "fake" else FasterWhisperStt(s)
