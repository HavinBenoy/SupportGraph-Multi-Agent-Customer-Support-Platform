"""Check the real voice stack on your machine (no microphone, no database):  make voice-smoke

Loads Silero VAD, faster-whisper and your Piper voice, has Piper say three sentences, checks that VAD finds each as ONE utterance and that
Whisper transcribes it back. Prints model load times, text-to-speech latency, speech-to-text real-time factor and word error rate, and
writes the spoken sentences to evals/voice/*.wav so you can listen. Exit 0 = every sentence passed. Run `make voice-setup` first."""

import sys
import time
from pathlib import Path

from app.config import get_settings
from app.voice.smoke import format_rows, run_smoke
from app.voice.stt import FasterWhisperStt, SttUnavailable
from app.voice.tts import PiperTts, TtsUnavailable
from app.voice.vad import SileroVad, VadUnavailable

OUT = Path(__file__).resolve().parents[2] / "evals" / "voice"


def main() -> int:
    s = get_settings()
    print(f"voice: stt={s.voice_stt_model} ({s.voice_stt_compute_type}, {s.voice_stt_threads} threads)  tts voice={s.voice_tts_voice or '(not set)'}  vad={s.voice_vad_model or 'bundled Silero'}")
    try:
        t0 = time.perf_counter(); vad = SileroVad(s.voice_vad_model); print(f"  Silero VAD loaded in {time.perf_counter() - t0:.2f}s")
        stt = FasterWhisperStt(s); print(f"  Whisper '{s.voice_stt_model}' loaded in {stt.preload():.2f}s")
        tts = PiperTts(s); print(f"  Piper voice loaded in {tts.preload():.2f}s ({tts.sample_rate} Hz)")
    except (VadUnavailable, SttUnavailable, TtsUnavailable) as e:
        print(f"NOT READY: {e}")
        return 1
    stt.transcribe(b"\x00\x00" * 16000)  # first call warms the model; keep it out of the timings
    rows = run_smoke(tts, stt, vad, wav_dir=OUT, threshold=s.voice_vad_threshold, silence_ms=s.voice_silence_ms, min_utterance_ms=s.voice_min_utterance_ms)
    print("\n" + format_rows(rows))
    bad = [r for r in rows if r.problems]
    for r in bad:
        print(f"PROBLEM: {r.sentence!r}: {'; '.join(r.problems)}")
    (OUT / "REPORT.md").write_text("# Voice smoke\n\n" + format_rows(rows) + "\n")
    print(f"\nspoken sentences: {OUT}/tts_*.wav   report: {OUT / 'REPORT.md'}")
    print("RESULT:", "all sentences round-tripped" if not bad else f"{len(bad)} of {len(rows)} sentences had problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
