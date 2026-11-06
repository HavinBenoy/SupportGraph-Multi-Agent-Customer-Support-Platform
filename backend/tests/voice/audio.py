"""Synthetic PCM16 16 kHz audio for tests: loud tone = 'speech' for the energy VAD, zeros = silence."""

import math
import struct

RATE = 16000


def tone(ms: int, amp: int = 9000, hz: int = 220) -> bytes:
    n = RATE * ms // 1000
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * hz * i / RATE))) for i in range(n))


def silence(ms: int) -> bytes:
    return b"\x00\x00" * (RATE * ms // 1000)


def utterance(speech_ms: int = 600, tail_ms: int = 900) -> bytes:
    return tone(speech_ms) + silence(tail_ms)


def chunks(pcm: bytes, size: int = 640):  # 20 ms frames like a browser worklet
    for i in range(0, len(pcm), size):
        yield pcm[i:i + size]
