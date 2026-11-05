"""Speech benchmarks, ONLY for the optional voice milestone. Both need extra packages and files the repo does not ship:

  stt: pip install faster-whisper   + 16-bit WAV files of someone speaking (your own voice is best: record 5-10 short
       questions in your normal accent; NOT committed, NOT uploaded; the report keeps only timings and the transcript length)
  tts: Piper installed (pip install piper-tts, or the piper binary) + a voice .onnx file

Real-time factor (RTF) = processing time / audio length. Below 1.0 is faster than real time; for a live call you want
well below 1.0 on STT and a fast time-to-first-audio on TTS.
"""

import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from app.bench.stats import summarise

TTS_SENTENCES = [
    "Hello, this is the QSpiders support assistant.",
    "Your batch starts on Monday at nine thirty in the morning.",
    "I can help with fees, courses, attendance and make-up classes.",
    "Let me check that for you.",
    "Sorry, I could not find that information, so I will pass you to a staff member.",
]


class OptionalDependencyMissing(RuntimeError):
    pass


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def bench_stt(model_size: str, audio_dir: Path, *, compute_type: str = "int8", threads: int = 4, transcribe=None) -> dict:
    """`transcribe(path) -> text` is injectable for tests; by default faster-whisper on CPU."""
    files = sorted(audio_dir.glob("*.wav"))
    if not files:
        raise OptionalDependencyMissing(f"No .wav files in {audio_dir}. Record a few short questions (16-bit WAV) and retry.")
    load_s = 0.0
    if transcribe is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise OptionalDependencyMissing("faster-whisper is not installed: pip install faster-whisper") from e
        t0 = time.perf_counter()
        model = WhisperModel(model_size, device="cpu", compute_type=compute_type, cpu_threads=threads)
        load_s = time.perf_counter() - t0

        def transcribe(p: Path) -> str:
            segs, _ = model.transcribe(str(p), language="en", beam_size=1, vad_filter=False)
            return " ".join(s.text.strip() for s in segs)

    rows = []
    for f in files:
        dur = wav_seconds(f)
        t0 = time.perf_counter()
        text = transcribe(f)
        dt = time.perf_counter() - t0
        rows.append({"file": f.name, "audio_s": round(dur, 2), "process_s": round(dt, 3), "rtf": round(dt / dur, 3) if dur else None,
                     "transcript_words": len(text.split())})  # words only: no transcript text is stored
    return {"suite": "stt", "model": f"faster-whisper:{model_size}:{compute_type}", "model_load_s": round(load_s, 2),
            "files": len(rows), "rtf": summarise([r["rtf"] for r in rows if r["rtf"] is not None]),
            "process_s": summarise([r["process_s"] for r in rows]), "rows": rows}


def bench_tts(voice_path: Path, *, piper_bin: str | None = None, sentences: list[str] | None = None, runner=None) -> dict:
    """Times Piper synthesising each sentence to a temp WAV. `runner(text, out_path)` is injectable for tests."""
    sentences = sentences or TTS_SENTENCES
    if runner is None:
        exe = piper_bin or shutil.which("piper")
        if not exe:
            raise OptionalDependencyMissing("piper not found on PATH: pip install piper-tts (or pass --piper-bin)")
        if not voice_path.exists():
            raise OptionalDependencyMissing(f"voice file not found: {voice_path}")

        def runner(text: str, out: Path) -> None:
            subprocess.run([exe, "--model", str(voice_path), "--output_file", str(out)], input=text.encode(), check=True,
                           capture_output=True, timeout=120)

    rows = []
    with tempfile.TemporaryDirectory() as td:
        for i, text in enumerate(sentences):
            out = Path(td) / f"s{i}.wav"
            t0 = time.perf_counter()
            runner(text, out)
            dt = time.perf_counter() - t0
            dur = wav_seconds(out)
            rows.append({"chars": len(text), "audio_s": round(dur, 2), "process_s": round(dt, 3), "rtf": round(dt / dur, 3) if dur else None})
    # Each CLI call includes process start + voice load, so these are a pessimistic bound; a resident TTS service is faster (milestone 2).
    return {"suite": "tts", "voice": voice_path.name, "note": "per-call CLI timing includes model load: pessimistic upper bound",
            "rtf": summarise([r["rtf"] for r in rows if r["rtf"] is not None]), "process_s": summarise([r["process_s"] for r in rows]), "rows": rows}
