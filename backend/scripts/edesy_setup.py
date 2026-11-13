"""Connect (or disconnect) an Edesy phone number to SupportGraph's telephone endpoint. Runs on YOUR machine; nothing here runs automatically.

A public Indian number is a PAID service (number rental + per-minute charges, see docs/M8.md section 7). Only run this after you have decided to use it.

    export EDESY_API_KEY=vp_...                    # from the Edesy dashboard; never put it in the repository or in .env.example
    python scripts/edesy_setup.py numbers                                         # list your numbers and their ids
    python scripts/edesy_setup.py stream-test --url wss://<public-host>/api/telephony/edesy/stream/<token>
    python scripts/edesy_setup.py attach --number-id <id> --url wss://<public-host>/api/telephony/edesy/stream/<token> [--rate 16000]
    python scripts/edesy_setup.py detach --number-id <id>

Endpoints (Edesy Voice API docs, checked 2026-11-12): GET /v1/phone-numbers, PUT/DELETE /v1/phone-numbers/{id}/inbound, POST /v1/stream-test.
Standard library only (urllib). The API key is read from the environment and is never printed."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("EDESY_API_BASE", "https://voice-api.edesy.in/v1")


def request(method: str, path: str, body: dict | None = None, *, key: str, opener=urllib.request.urlopen) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                                                                "Accept": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise SystemExit(f"Edesy answered {e.code}: {detail}") from None


def check_url(url: str) -> str:
    if not url.startswith("wss://"):
        raise SystemExit("the stream URL must start with wss:// (Edesy connects over TLS; use a tunnel or a reverse proxy with a certificate)")
    if "/api/telephony/edesy/stream/" not in url or len(url.rsplit("/", 1)[-1]) < 32:
        raise SystemExit("the URL must end with /api/telephony/edesy/stream/<SG_TELEPHONY_STREAM_TOKEN> (at least 32 characters)")
    return url


def main(argv=None, opener=urllib.request.urlopen) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("numbers")
    t = sub.add_parser("stream-test")
    t.add_argument("--url", required=True)
    t.add_argument("--rate", type=int, choices=(8000, 16000), default=16000)
    a = sub.add_parser("attach")
    a.add_argument("--number-id", required=True)
    a.add_argument("--url", required=True)
    a.add_argument("--rate", type=int, choices=(8000, 16000), default=16000)
    d = sub.add_parser("detach")
    d.add_argument("--number-id", required=True)
    args = ap.parse_args(argv)
    key = os.environ.get("EDESY_API_KEY", "")
    if not key:
        raise SystemExit("set EDESY_API_KEY in your shell first (it is not read from .env on purpose)")
    if args.cmd == "numbers":
        out = request("GET", "/phone-numbers", key=key, opener=opener)
    elif args.cmd == "stream-test":
        out = request("POST", "/stream-test", {"stream_url": check_url(args.url), "sample_rate": args.rate, "seconds": 2}, key=key, opener=opener)
    elif args.cmd == "attach":
        out = request("PUT", f"/phone-numbers/{args.number_id}/inbound", {"target": "media_stream", "stream_url": check_url(args.url), "sample_rate": args.rate},
                      key=key, opener=opener)
        print(f"Attached. Set SG_TELEPHONY_SAMPLE_RATE={args.rate} to match.", file=sys.stderr)
    else:
        out = request("DELETE", f"/phone-numbers/{args.number_id}/inbound", key=key, opener=opener)
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    main()
