"""Edesy Voice API media streams (docs: edesy.in/docs/voice-api/streaming/media-streams, .../connect/websocket-streaming; checked 2026-11-12).

Wire protocol, as documented:
  * one WebSocket per call, opened BY Edesy to the stream_url attached to the number (PUT /v1/phone-numbers/{id}/inbound, target media_stream)
  * first frame: text JSON metadata, e.g. {"callSid","call_sid","from","to","direction","sampleRate","mixType":"mono","call_status",...}
  * then binary frames both ways: raw L16 (signed 16-bit little-endian) mono PCM at the stream's sample rate (16000 default, or 8000), ~20 ms each
  * bot -> Edesy text: {"type":"killAudio"} flush playback (barge-in) | {"type":"disconnect"} hang up | {"type":"mark","data":{"name":...}}
  * Edesy -> bot text: {"event":"dtmf","dtmf":"5","duration":...} (DTMF passthrough) | {"type":"mark","data":{"name":...,"event":"playout"}}
  * no documented WebSocket authentication: SupportGraph puts a long secret in the URL path and can restrict source IPs (docs/M8.md section 6)."""

import json

from app.telephony.base import CallStart, LineEvent, ProtocolError

_DTMF = set("0123456789*#ABCD")
MAX_FIELD = 80


def _s(v) -> str:
    return str(v)[:MAX_FIELD] if isinstance(v, (str, int)) else ""


class EdesyAdapter:
    name = "edesy"

    def __init__(self, expected_rate: int):
        self.expected_rate = expected_rate

    def parse_start(self, text: str) -> CallStart:
        try:
            m = json.loads(text)
        except ValueError:
            raise ProtocolError("the first frame is not JSON") from None
        if not isinstance(m, dict):
            raise ProtocolError("the first frame is not a JSON object")
        rate = m.get("sampleRate", m.get("sample_rate", self.expected_rate))
        try:
            rate = int(rate)
        except (TypeError, ValueError):
            raise ProtocolError("sampleRate is not a number") from None
        if rate not in (8000, 16000):
            raise ProtocolError(f"unsupported sample rate {rate}")
        ref = _s(m.get("callSid") or m.get("call_sid") or m.get("call_id"))
        cd = m.get("customerData") if isinstance(m.get("customerData"), dict) else {}
        return CallStart(provider=self.name, call_ref=ref, from_number=_s(m.get("from")), to_number=_s(m.get("to")), sample_rate=rate,
                         direction=_s(m.get("direction")) or "inbound", extra={"call_sid": _s(cd.get("call_sid"))} if cd.get("call_sid") else {})

    def parse_event(self, text: str) -> LineEvent:
        try:
            m = json.loads(text)
        except ValueError:
            return LineEvent("ignored")
        if not isinstance(m, dict):
            return LineEvent("ignored")
        if m.get("event") == "dtmf" or m.get("type") == "dtmf":
            d = str(m.get("dtmf", m.get("digit", "")))[:1]
            return LineEvent("dtmf", digit=d) if d in _DTMF else LineEvent("ignored")
        if m.get("type") == "mark" and isinstance(m.get("data"), dict) and m["data"].get("event") == "playout":
            return LineEvent("mark", name=_s(m["data"].get("name")))
        return LineEvent("ignored")

    def kill_audio(self) -> dict:
        return {"type": "killAudio"}

    def hang_up(self) -> dict:
        return {"type": "disconnect"}

    def mark(self, name: str) -> dict:
        return {"type": "mark", "data": {"name": name}}


def get_adapter(provider: str, rate: int):
    if provider == "edesy":
        return EdesyAdapter(rate)
    raise ProtocolError(f"no telephony provider '{provider}'")
