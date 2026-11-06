"""The real STT/TTS adapter code, against stand-ins for faster_whisper.WhisperModel and piper.PiperVoice whose call signatures were checked
against the installed libraries. (The models themselves cannot be downloaded in the sandbox: `make voice-smoke` is the real check.)"""

import sys
import types

import numpy as np
import pytest

from app.config import Settings
from app.voice.stt import FasterWhisperStt, SttUnavailable
from app.voice.tts import PiperTts, TtsUnavailable


def S(**kw):
    return Settings(_env_file=None, voice_provider="real", **kw)


class Seg:
    def __init__(self, text, nsp):
        self.text, self.no_speech_prob = text, nsp


class StubWhisper:
    inits, calls = [], []

    def __init__(self, name, device, compute_type, cpu_threads):
        StubWhisper.inits.append((name, device, compute_type, cpu_threads))

    def transcribe(self, audio, **kw):
        StubWhisper.calls.append((audio, kw))
        return iter([Seg(" what is the fee ", 0.1), Seg("for python", 0.3)]), types.SimpleNamespace(language="en")


@pytest.fixture(autouse=True)
def fresh():
    FasterWhisperStt._models.clear(); PiperTts._voices.clear(); StubWhisper.inits.clear(); StubWhisper.calls.clear()


def test_whisper_adapter_loads_once_and_transcribes_with_cpu_int8_greedy_decoding(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=StubWhisper))
    stt = FasterWhisperStt(S(voice_stt_model="base.en", voice_stt_threads=3))
    pcm = (np.ones(16000) * 1000).astype("<i2").tobytes()
    a = stt.transcribe(pcm, prompt="QSpiders")
    b = FasterWhisperStt(S(voice_stt_model="base.en", voice_stt_threads=3)).transcribe(pcm)
    assert a.text == "what is the fee for python" and a.audio_seconds == 1.0 and a.no_speech_prob == 0.3 and b.text == a.text
    assert StubWhisper.inits == [("base.en", "cpu", "int8", 3)]              # one load shared by every call
    audio, kw = StubWhisper.calls[0]
    assert audio.dtype == np.float32 and abs(audio.max() - 1000 / 32768) < 1e-6
    assert kw["beam_size"] == 1 and kw["vad_filter"] is False and kw["initial_prompt"] == "QSpiders" and kw["language"] == "en"
    assert kw["condition_on_previous_text"] is False and kw["temperature"] == 0.0


def test_whisper_load_failure_becomes_a_helpful_error(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise OSError("no network")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Boom))
    with pytest.raises(SttUnavailable, match="make voice-setup"):
        FasterWhisperStt(S()).transcribe(b"\x00\x00" * 100)


def test_whisper_missing_package_is_explained(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # makes `import faster_whisper` raise ImportError
    with pytest.raises(SttUnavailable, match="pip install faster-whisper"):
        FasterWhisperStt(S()).transcribe(b"\x00\x00" * 100)


class StubVoice:
    config = types.SimpleNamespace(sample_rate=22050)

    @classmethod
    def load(cls, path):
        cls.loaded = path
        return cls()

    def synthesize(self, text):
        for i in range(2):
            yield types.SimpleNamespace(audio_int16_bytes=bytes([i]) * 100)


def test_piper_adapter_streams_chunks_and_reports_the_voice_sample_rate(monkeypatch, tmp_path):
    voice = tmp_path / "v.onnx"; voice.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(PiperVoice=StubVoice))
    tts = PiperTts(S(voice_tts_voice=str(voice)))
    assert tts.sample_rate == 22050 and StubVoice.loaded == str(voice)
    assert list(tts.synthesize("hello")) == [b"\x00" * 100, b"\x01" * 100]


def test_piper_needs_a_configured_existing_voice(tmp_path):
    with pytest.raises(TtsUnavailable, match="SG_VOICE_TTS_VOICE"):
        PiperTts(S())
    with pytest.raises(TtsUnavailable, match="not found"):
        PiperTts(S(voice_tts_voice=str(tmp_path / "nope.onnx")))


def test_piper_load_failure_is_explained(monkeypatch, tmp_path):
    class Bad:
        @classmethod
        def load(cls, path):
            raise FileNotFoundError("config")

    voice = tmp_path / "v.onnx"; voice.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(PiperVoice=Bad))
    with pytest.raises(TtsUnavailable, match="onnx.json"):
        PiperTts(S(voice_tts_voice=str(voice)))
