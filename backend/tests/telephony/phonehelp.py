"""Helpers for the M8 telephone tests: a fake Edesy media stream over the real FastAPI app (TestClient WebSocket), with the offline voice fakes."""

import json
import struct

from pydantic import SecretStr

from app.db.models import Student
from app.db.session import session_scope
from app.telephony import identity as idn
from app.telephony.media import resample
from tests.voice.audio import chunks, utterance

TOKEN = "k" * 40
PATH = f"/api/telephony/edesy/stream/{TOKEN}"
NUMBER = "+91 98450 12345"
E164 = "+919845012345"


def enable(api, rate=16000, **kw):
    s = api.app.state.settings
    s.telephony_provider, s.telephony_stream_token, s.telephony_sample_rate = "edesy", SecretStr(TOKEN), rate
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def meta(frm=NUMBER, to="917969002802", rate=16000, sid="CA_test1"):
    return {"callSid": sid, "call_sid": sid, "from": frm, "to": to, "direction": "inbound", "sampleRate": rate, "mixType": "mono", "callStatus": "in-progress"}


def set_student_phone(student_id="stu_asha", phone=NUMBER):
    with session_scope() as db:
        db.get(Student, ("qspiders_sample", student_id)).phone = phone


def set_pin(username="asha", pin="482913"):
    with session_scope() as db:
        idn.set_pin(db, "qspiders_sample", username, pin, 6)


def speak(ws, rate=16000):
    pcm = utterance()
    if rate != 16000:
        pcm = resample(pcm, 16000, rate)
    for c in chunks(pcm, size=rate * 20 // 1000 * 2):
        ws.send_bytes(c)


def keys(ws, digits: str):
    for d in digits:
        ws.send_text(json.dumps({"event": "dtmf", "dtmf": d, "duration": 160}))


HDR = struct.Struct("<II")
