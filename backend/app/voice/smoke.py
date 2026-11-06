"""Round-trip check of the real voice stack on THIS machine: Piper speaks sentences, Silero VAD must find the speech and end the utterance,
faster-whisper must transcribe it back. No microphone, no database, no network (after `make voice-setup`), no student data.
Pure function over injected parts so the logic itself is unit-tested with fakes; scripts/voice_smoke.py supplies the real models."""

from __future__ import annotations

import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path


from app.voice.vad import FrameVad, Endpointer, SAMPLE_RATE

SENTENCES = [
    "What is the fee for the Python full stack course?",
    "Can I pay the course fee in two instalments?",
    "I want to talk to a person about my attendance.",
]


def words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def wer(reference: str, hypothesis: str) -> float:
    r, h = words(reference), words(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / len(r)


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int = SAMPLE_RATE) -> bytes:
    if src_rate == dst_rate:
        return pcm
    import numpy as np

    x = np.frombuffer(pcm, dtype="<i2").astype("float32")
    n = int(len(x) * dst_rate / src_rate)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype("<i2").tobytes()


def write_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)


@dataclass
class Row:
    sentence: str
    tts_first_ms: float = 0.0
    tts_rtf: float = 0.0
    audio_s: float = 0.0
    vad_utterances: int = 0
    transcript: str = ""
    wer: float = 1.0
    stt_s: float = 0.0
    stt_rtf: float = 0.0
    problems: list[str] = field(default_factory=list)


def run_smoke(tts, stt, vad: FrameVad, *, sentences=SENTENCES, wav_dir: Path | None = None, max_wer: float = 0.5, **endpointer_kw) -> list[Row]:
    rows: list[Row] = []
    for i, text in enumerate(sentences):
        row = Row(text)
        t0 = time.perf_counter()
        chunks, first = [], None
        for c in tts.synthesize(text):
            first = first if first is not None else time.perf_counter() - t0
            chunks.append(c)
        total = time.perf_counter() - t0
        pcm = b"".join(chunks)
        row.audio_s = len(pcm) / 2 / tts.sample_rate
        row.tts_first_ms, row.tts_rtf = round((first or 0) * 1000), round(total / row.audio_s, 3) if row.audio_s else 0.0
        if not pcm:
            row.problems.append("TTS produced no audio")
            rows.append(row)
            continue
        if wav_dir:
            write_wav(wav_dir / f"tts_{i + 1}.wav", pcm, tts.sample_rate)
        pcm16k = resample_pcm16(pcm, tts.sample_rate)
        stream = b"\x00\x00" * (SAMPLE_RATE // 2) + pcm16k + b"\x00\x00" * int(SAMPLE_RATE * 1.4)
        vad.reset()
        ep = Endpointer(vad, **endpointer_kw)
        events = [e for k in range(0, len(stream), 640) for e in ep.feed(stream[k:k + 640])]
        utts = [e for e in events if e.kind == "utterance"]
        row.vad_utterances = len(utts)
        if len(utts) != 1:
            row.problems.append(f"VAD found {len(utts)} utterances in one spoken sentence (expected 1)")
        if utts:
            tr = stt.transcribe(b"".join(u.audio for u in utts))
            row.transcript, row.stt_s = tr.text, round(tr.seconds, 2)
            row.stt_rtf = round(tr.seconds / tr.audio_seconds, 3) if tr.audio_seconds else 0.0
            row.wer = round(wer(text, tr.text), 3)
            if row.wer > max_wer:
                row.problems.append(f"transcript differs too much (WER {row.wer:.0%})")
        rows.append(row)
    return rows


def format_rows(rows: list[Row]) -> str:
    out = ["| sentence | TTS first audio ms | TTS RTF | VAD utts | STT s | STT RTF | WER | transcript |", "|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        out.append(f"| {r.sentence[:40]} | {r.tts_first_ms:.0f} | {r.tts_rtf} | {r.vad_utterances} | {r.stt_s} | {r.stt_rtf} | {r.wer:.0%} | {r.transcript[:50]} |")
    return "\n".join(out)
