"""The REAL Silero VAD model (onnxruntime + faster-whisper's bundled copy). Skipped when those packages are not installed; speech comes from
espeak-ng when it is installed (otherwise only the silence/noise test runs)."""

import shutil
import subprocess
import wave

import numpy as np
import pytest

from app.voice.vad import FRAME, Endpointer
from tests.voice.audio import RATE, chunks, silence

pytest.importorskip("onnxruntime")


def real_vad():
    from app.voice.vad import SileroVad, VadUnavailable

    try:
        return SileroVad()
    except VadUnavailable as e:
        pytest.skip(str(e))


def test_real_silero_says_silence_and_noise_are_not_speech():
    vad = real_vad()
    rng = np.random.default_rng(0)
    probs = [vad.prob(np.zeros(FRAME, dtype="float32")) for _ in range(30)]
    assert max(probs) < 0.2
    vad.reset()
    probs = [vad.prob((rng.standard_normal(FRAME) * 0.01).astype("float32")) for _ in range(30)]
    assert max(probs) < 0.5


@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="espeak-ng not installed")
def test_real_silero_detects_real_speech_and_endpoints_two_utterances(tmp_path):
    def say(text):
        wav = tmp_path / f"{abs(hash(text))}.wav"
        subprocess.run(["espeak-ng", "-w", str(wav), "-s", "150", text], check=True)
        with wave.open(str(wav)) as w:
            data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32")
            src = w.getframerate()
        n = int(len(data) * RATE / src)
        return np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype("<i2").tobytes()

    pcm = silence(400) + say("What is the fee for the python course?") + silence(1200) + say("Can I pay in instalments?") + silence(1200)
    ep = Endpointer(real_vad())
    ev = [e for c in chunks(pcm) for e in ep.feed(c)]
    kinds = [e.kind for e in ev]
    assert kinds.count("utterance") == 2 and kinds[0] == "speech_start", kinds
    assert all(e.ms > 800 for e in ev if e.kind == "utterance")
