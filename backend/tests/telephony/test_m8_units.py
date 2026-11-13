"""M8 building blocks: number normalisation and masking, PIN rules and lockout, the Edesy protocol adapter, and the media bridge (resampling, framing,
flush on barge-in / ownership change, hang-up, playback markers)."""

import asyncio
import json
import struct

import pytest

from app.telephony import identity as idn
from app.telephony.base import ProtocolError
from app.telephony.edesy import EdesyAdapter
from app.telephony.media import PhoneBridge, resample


@pytest.mark.parametrize("raw,want", [("+91 98450 12345", "+919845012345"), ("098450-12345", "+919845012345"), ("9845012345", "+919845012345"),
                                      ("919845012345", "+919845012345"), ("0091 9845012345", "+919845012345"), ("+44 20 7946 0958", "+442079460958"),
                                      ("", None), (None, None), ("anonymous", None), ("12345", None), ("+1234567890123456", None)])
def test_normalise(raw, want):
    assert idn.normalise(raw) == want


def test_mask_and_hash_never_carry_the_number():
    assert idn.mask("+919845012345") == "+91 ******2345" and idn.mask(None) is None
    h1, h2 = idn.number_hash("s", "t", "+919845012345"), idn.number_hash("s", "u", "+919845012345")
    assert h1 != h2 and "9845" not in h1 and len(h1) == 32                                  # per tenant, keyed


def test_pin_rules():
    for bad in ("12345", "1234567", "111111", "123456", "987654", "12a456", ""):
        with pytest.raises(idn.PinError):
            idn.validate_pin(bad, 6)
    assert idn.validate_pin("482913", 6) == "482913"


def test_edesy_start_frame_and_events():
    a = EdesyAdapter(16000)
    st = a.parse_start(json.dumps({"callSid": "CA1", "from": "919845012345", "to": "917969002802", "sampleRate": 8000, "direction": "inbound"}))
    assert (st.call_ref, st.from_number, st.to_number, st.sample_rate, st.provider) == ("CA1", "919845012345", "917969002802", 8000, "edesy")
    assert a.parse_start(json.dumps({"call_sid": "CA2", "from": "x", "to": "y"})).sample_rate == 16000
    for bad in ("{", "[]", json.dumps({"sampleRate": 44100}), json.dumps({"sampleRate": "x"})):
        with pytest.raises(ProtocolError):
            a.parse_start(bad)
    assert a.parse_event(json.dumps({"event": "dtmf", "dtmf": "5", "duration": 160})).digit == "5"
    assert a.parse_event(json.dumps({"event": "dtmf", "dtmf": "<script>"})).kind == "ignored"
    assert a.parse_event(json.dumps({"type": "mark", "data": {"name": "e3", "event": "playout"}})).name == "e3"
    assert a.parse_event("garbage").kind == "ignored"
    assert a.kill_audio() == {"type": "killAudio"} and a.hang_up() == {"type": "disconnect"} and a.mark("x") == {"type": "mark", "data": {"name": "x"}}


def test_resample_lengths_and_identity():
    pcm = struct.pack("<4h", 0, 1000, -1000, 32767) * 400                                    # 1600 samples
    assert resample(pcm, 16000, 16000) is pcm
    assert len(resample(pcm, 16000, 8000)) == 1600 and len(resample(pcm, 8000, 16000)) == 6400
    assert len(resample(pcm, 22050, 8000)) == round(1600 * 8000 / 22050) * 2
    assert resample(b"", 8000, 16000) == b"" and resample(b"\x01", 8000, 16000) == b""


class Line:
    def __init__(self):
        self.text, self.audio, self.closed = [], [], False

    async def t(self, m):
        self.text.append(m)

    async def a(self, b):
        self.audio.append(b)

    async def c(self):
        self.closed = True


def frame(turn, seq, pcm):
    return struct.pack("<II", turn, seq) + pcm


def run(coro):
    return asyncio.run(coro)


def test_bridge_frames_resamples_flushes_and_hangs_up():
    async def go():
        line = Line()
        b = PhoneBridge(EdesyAdapter(8000), 8000, line.t, line.a, line.c)
        await b.on_json({"type": "audio_start", "turn_id": 4, "sample_rate": 16000})
        await b.on_bytes(frame(4, 0, b"\x10\x00" * 500))                                     # 500 samples @16k -> 250 @8k = 1 frame of 160 + 90 pending
        assert [len(x) for x in line.audio] == [320]
        await b.on_json({"type": "audio_end", "turn_id": 4, "interrupted": False})
        assert [len(x) for x in line.audio] == [320, 320] and line.text[-1] == {"type": "mark", "data": {"name": "e4"}}   # the tail is padded, then a marker
        await b.on_json({"type": "audio_start", "turn_id": 5, "sample_rate": 16000})
        await b.on_bytes(frame(5, 0, b"\x10\x00" * 100))
        await b.on_json({"type": "interrupted", "turn_id": 5})
        assert line.text[-1] == {"type": "killAudio"} and b._pending == b""                   # barge-in: the provider's queue and ours are dropped
        await b.on_json({"type": "ownership", "owner": "staff", "epoch": 2})
        assert line.text[-1] == {"type": "killAudio"}                                         # a takeover flushes too
        played = []
        b.on_played = played.append
        b.on_mark("e4"); b.on_mark("zz"); b.on_mark(None)
        assert played == [4]
        await b.on_json({"type": "call_end", "reason": "ended_by_staff"})
        assert line.text[-1] == {"type": "disconnect"} and line.closed
        n = len(line.audio)
        await b.on_bytes(frame(6, 0, b"\x10\x00" * 400))
        assert len(line.audio) == n                                                            # nothing after the hang-up
        assert b.caller_audio(b"\x00\x00" * 160) == b"\x00\x00" * 320                         # caller 8 kHz -> session 16 kHz
    run(go())


def test_browser_only_messages_are_not_sent_down_the_line():
    async def go():
        line = Line()
        b = PhoneBridge(EdesyAdapter(16000), 16000, line.t, line.a, line.c)
        for m in ({"type": "reply", "reply": "secret record"}, {"type": "transcript", "text": "x"}, {"type": "trial_notice"}, {"type": "metrics"}):
            await b.on_json(m)
        assert line.text == [] and line.audio == []
    run(go())


def test_the_line_rate_is_read_from_the_environment_and_checked(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings
    monkeypatch.setenv("SG_TELEPHONY_SAMPLE_RATE", "8000")                                    # environment values are strings
    assert Settings(_env_file=None).telephony_sample_rate == 8000
    monkeypatch.setenv("SG_TELEPHONY_SAMPLE_RATE", "44100")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.delenv("SG_TELEPHONY_SAMPLE_RATE")
    s = Settings(_env_file=None)
    assert (s.telephony_provider, s.telephony_sample_rate, s.telephony_stream_token) == ("none", 16000, None)   # off by default
