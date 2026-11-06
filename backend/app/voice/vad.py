"""Voice activity detection and utterance endpointing.

`SileroVad` runs the Silero VAD ONNX model (the copy bundled with faster-whisper, so no torch) frame by frame: 512 samples = 32 ms at
16 kHz, with a 64-sample context and the LSTM state carried between frames. `EnergyVad` is a crude stand-in for tests.
`Endpointer` is pure logic on top of any VAD: it turns a stream of speech probabilities into "speech started" / "utterance finished"
events with a pre-roll, a hangover, a minimum length and a maximum length. Nothing here knows about sockets, models or the graph.
"""

from __future__ import annotations

import glob
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol


SAMPLE_RATE = 16000
FRAME = 512            # samples per VAD frame (32 ms)
FRAME_MS = FRAME * 1000 // SAMPLE_RATE
CONTEXT = 64


class FrameVad(Protocol):
    def prob(self, frame: np.ndarray) -> float: ...   # float32 samples in [-1, 1], exactly FRAME long
    def reset(self) -> None: ...


class VadUnavailable(RuntimeError):
    pass


def find_silero_model(explicit: str = "") -> str:
    if explicit:
        if not os.path.isfile(explicit):
            raise VadUnavailable(f"SG_VOICE_VAD_MODEL does not exist: {explicit}")
        return explicit
    try:
        import faster_whisper
    except ImportError as e:
        raise VadUnavailable("faster-whisper is not installed (it carries the Silero VAD model): pip install faster-whisper") from e
    found = sorted(glob.glob(os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad*.onnx")))
    if not found:
        raise VadUnavailable("This faster-whisper has no bundled Silero VAD model: upgrade it (pip install -U faster-whisper) or set SG_VOICE_VAD_MODEL")
    return found[-1]


class SileroVad:
    """One instance per call (it holds the recurrent state)."""

    def __init__(self, model_path: str = "") -> None:
        try:
            import onnxruntime
        except ImportError as e:
            raise VadUnavailable("onnxruntime is not installed: pip install onnxruntime") from e
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(find_silero_model(model_path), providers=["CPUExecutionProvider"], sess_options=opts)
        names = {i.name for i in self._session.get_inputs()}
        if names != {"input", "h", "c"}:  # a different Silero release (e.g. state/sr inputs) needs different plumbing: fail loudly
            raise VadUnavailable(f"Unsupported Silero VAD model inputs {sorted(names)}; expected input/h/c (faster-whisper >= 1.2.1)")
        self.reset()

    def reset(self) -> None:
        import numpy as np

        self._h = np.zeros((1, 1, 128), dtype="float32")
        self._c = np.zeros((1, 1, 128), dtype="float32")
        self._ctx = np.zeros(CONTEXT, dtype="float32")

    def prob(self, frame: np.ndarray) -> float:
        assert frame.shape == (FRAME,)
        import numpy as np

        x = np.concatenate([self._ctx, frame.astype("float32")])[None, :]
        out, self._h, self._c = self._session.run(None, {"input": x, "h": self._h, "c": self._c})
        self._ctx = frame[-CONTEXT:].astype("float32")
        return float(np.asarray(out).reshape(-1)[0])


class EnergyVad:
    """Test stand-in: speech = loud frame. Not for real use."""

    def __init__(self, rms_threshold: float = 0.02) -> None:
        self.rms_threshold = rms_threshold

    def reset(self) -> None:
        pass

    def prob(self, frame: np.ndarray) -> float:
        import numpy as np

        return 0.95 if float(np.sqrt(np.mean(np.square(frame)))) >= self.rms_threshold else 0.02


@dataclass
class Event:
    kind: str                       # "speech_start" | "utterance" | "too_short"
    audio: bytes = b""              # PCM16 mono 16 kHz, for "utterance"
    ms: int = 0


@dataclass
class Endpointer:
    vad: FrameVad
    threshold: float = 0.5
    silence_ms: int = 700
    min_utterance_ms: int = 300
    max_utterance_s: int = 15
    start_ms: int = 96              # consecutive speech needed before "speech_start" (rejects single noisy frames)
    pre_roll_ms: int = 320          # audio kept from before speech was confirmed, so the first word is not clipped
    _buf: bytearray = field(default_factory=bytearray)
    _pre: deque = field(default_factory=deque)
    _speech: bytearray = field(default_factory=bytearray)
    _in_speech: bool = False
    _run: int = 0                   # consecutive speech frames while idle
    _sil: int = 0                   # consecutive silence frames while in speech
    last_prob: float = 0.0

    def feed(self, pcm16: bytes) -> list[Event]:
        """Append PCM16 mono 16 kHz bytes; returns the events completed by the new audio (usually none)."""
        self._buf += pcm16[: len(pcm16) - (len(pcm16) % 2)]
        events: list[Event] = []
        step = FRAME * 2
        while len(self._buf) >= step:
            chunk = bytes(self._buf[:step])
            del self._buf[:step]
            events += self._frame(chunk)
        return events

    def reset(self) -> None:
        self._buf.clear(); self._pre.clear(); self._speech.clear()
        self._in_speech, self._run, self._sil = False, 0, 0
        self.vad.reset()

    def _frame(self, chunk: bytes) -> list[Event]:
        import numpy as np

        samples = np.frombuffer(chunk, dtype="<i2").astype("float32") / 32768.0
        p = self.last_prob = self.vad.prob(samples)
        out: list[Event] = []
        end_threshold = max(0.05, self.threshold - 0.15)  # hysteresis: stay "in speech" through brief dips
        if not self._in_speech:
            self._pre.append(chunk)
            while len(self._pre) * FRAME_MS > self.pre_roll_ms + self.start_ms:
                self._pre.popleft()
            self._run = self._run + 1 if p >= self.threshold else 0
            if self._run * FRAME_MS >= self.start_ms:
                self._in_speech, self._sil = True, 0
                self._speech = bytearray(b"".join(self._pre))
                self._pre.clear()
                out.append(Event("speech_start"))
            return out
        self._speech += chunk
        self._sil = self._sil + 1 if p < end_threshold else 0
        too_long = len(self._speech) // 2 >= self.max_utterance_s * SAMPLE_RATE
        if self._sil * FRAME_MS >= self.silence_ms or too_long:
            ms = len(self._speech) // 2 * 1000 // SAMPLE_RATE - self._sil * FRAME_MS
            audio = bytes(self._speech)
            self._speech = bytearray()
            self._in_speech, self._run, self._sil = False, 0, 0
            out.append(Event("utterance", audio, ms) if ms >= self.min_utterance_ms else Event("too_short", b"", ms))
        return out

    @property
    def in_speech(self) -> bool:
        return self._in_speech
