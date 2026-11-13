"""A local telephone-call simulator that speaks the Edesy media-stream protocol to YOUR running API, so the whole phone path can be tried for free
(no number, no provider): metadata frame, L16 audio both ways at 8 or 16 kHz, DTMF, playback markers, killAudio, disconnect.

    make phone-sim                                    # uses SG_TELEPHONY_STREAM_TOKEN from the environment / .env and ws://localhost:8000
    python scripts/phone_sim.py --from "+91 98450 12345" --rate 8000 --wav question.wav --keys "*482913#" --out reply.wav

It needs SG_TELEPHONY_PROVIDER=edesy and SG_TELEPHONY_STREAM_TOKEN set for the API (docs/M8.md section 8). It plays a WAV file (any mono 16-bit PCM;
resampled to the line rate) as the caller, presses keys, records what the line sends back to --out, and prints every control message it receives."""

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.telephony.media import resample  # noqa: E402


def load_wav(path: str, rate: int) -> bytes:
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit("the WAV must be mono 16-bit PCM")
        return resample(w.readframes(w.getnframes()), w.getframerate(), rate)


async def run(a) -> int:
    import websockets

    token = a.token or os.environ.get("SG_TELEPHONY_STREAM_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("set SG_TELEPHONY_STREAM_TOKEN (>= 32 characters) or pass --token")
    url = f"{a.api.rstrip('/')}/api/telephony/edesy/stream/{token}"
    frame = a.rate * 20 // 1000 * 2
    got = bytearray()
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"callSid": "SIM" + str(int(time.time())), "from": a.from_number, "to": a.to, "direction": "inbound", "sampleRate": a.rate,
                                  "mixType": "mono", "callStatus": "in-progress"}))

        async def reader():
            async for m in ws:
                if isinstance(m, bytes):
                    got.extend(m)
                else:
                    msg = json.loads(m)
                    print("<-", msg)
                    if msg.get("type") == "mark":                    # behave like the provider: echo the marker once "played"
                        await ws.send(json.dumps({"type": "mark", "data": {"name": msg["data"]["name"], "event": "playout"}}))
        rd = asyncio.create_task(reader())
        await asyncio.sleep(a.wait_greeting)
        if a.wav:
            pcm = load_wav(a.wav, a.rate) + b"\x00\x00" * (a.rate * 1)   # one second of silence ends the utterance
            for i in range(0, len(pcm), frame):
                await ws.send(pcm[i:i + frame])
                await asyncio.sleep(0.02)
        for k in a.keys:
            await ws.send(json.dumps({"event": "dtmf", "dtmf": k, "duration": 160}))
            await asyncio.sleep(0.15)
        await asyncio.sleep(a.listen)
        rd.cancel()
    if a.out:
        with wave.open(a.out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(a.rate)
            w.writeframes(bytes(got))
    print(f"received {len(got) / 2 / a.rate:.1f} s of audio" + (f" -> {a.out}" if a.out else ""))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SupportGraph local phone-call simulator (Edesy protocol)")
    ap.add_argument("--api", default="ws://localhost:8000")
    ap.add_argument("--token", default="")
    ap.add_argument("--from", dest="from_number", default="+91 90000 00000")
    ap.add_argument("--to", default="917969002802")
    ap.add_argument("--rate", type=int, choices=(8000, 16000), default=16000)
    ap.add_argument("--wav", default="")
    ap.add_argument("--keys", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--wait-greeting", type=float, default=6.0)
    ap.add_argument("--listen", type=float, default=15.0)
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
