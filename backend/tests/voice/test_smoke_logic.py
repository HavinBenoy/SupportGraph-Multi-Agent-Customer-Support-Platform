import wave

import pytest

from app.voice.smoke import format_rows, resample_pcm16, run_smoke, wer, write_wav
from app.voice.stt import FakeStt
from app.voice.tts import FakeTts
from app.voice.vad import EnergyVad


def test_wer():
    assert wer("what is the fee", "what is the fee") == 0
    assert wer("What is the fee?", "what is the fee") == 0           # case and punctuation ignored
    assert wer("what is the fee", "what is fee") == 0.25
    assert wer("a b", "x y z") == 1.5 and wer("", "") == 0 and wer("", "x") == 1


def test_resample_changes_length_proportionally():
    assert len(resample_pcm16(b"\x01\x00" * 22050, 22050, 16000)) // 2 == 16000
    assert resample_pcm16(b"\x01\x00" * 10, 16000) == b"\x01\x00" * 10


def test_smoke_pipeline_with_fakes_passes_and_reports(tmp_path):
    rows = run_smoke(FakeTts(), FakeStt(["What is the fee for the Python full stack course?", "Can I pay the course fee in two instalments?",
                                         "I want to talk to a person about my attendance."]), EnergyVad(), wav_dir=tmp_path)
    assert all(not r.problems for r in rows) and [r.vad_utterances for r in rows] == [1, 1, 1] and all(r.wer == 0 for r in rows)
    assert (tmp_path / "tts_1.wav").exists() and "WER" in format_rows(rows)
    with wave.open(str(tmp_path / "tts_1.wav")) as w:
        assert w.getframerate() == 22050


def test_smoke_flags_a_bad_transcript_and_missing_speech():
    rows = run_smoke(FakeTts(), FakeStt(["completely different words here"]), EnergyVad(), sentences=["What is the fee?"])
    assert any("WER" in p for p in rows[0].problems)

    class Silent(FakeTts):
        def synthesize(self, text):
            return iter(())

    rows = run_smoke(Silent(), FakeStt(["x"]), EnergyVad(), sentences=["hello there"])
    assert rows[0].problems == ["TTS produced no audio"]


def test_smoke_flags_vad_that_hears_nothing():
    class Deaf:
        def prob(self, frame):
            return 0.0

        def reset(self):
            pass

    rows = run_smoke(FakeTts(), FakeStt(["x"]), Deaf(), sentences=["hello there"])
    assert any("found 0 utterances" in p for p in rows[0].problems)
