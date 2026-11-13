"""M8 over the real FastAPI app, the real support graph (offline fakes) and a fake Edesy media stream (the documented protocol): the endpoint's refusal rules,
8/16 kHz audio both ways, the phone call in Live Calls, caller identification (a claim, never proof), PIN verification with lockout, a shared number,
staff takeover on a phone line (queued AI audio flushed, staff audio delivered, hang-up), and the keypad recording consent in Trial Verification Mode."""

import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.routes import telephony as tel
from app.tools.scope import ANONYMOUS
from app.db.models import Call, CallEvent, PhonePin
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.calls.test_calls_api import Pump, open_stream, staff_stream, tok
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401
from tests.telephony.phonehelp import E164, NUMBER, PATH, TOKEN, enable, keys, meta, set_pin, set_student_phone, speak
from tests.voice.test_voice_ws import setup_parts

QUESTION = "Can I pay the course fee in instalments?"


def calls(api):  # noqa: F811
    return api.get("/api/staff/calls", headers=auth(api, "staff1")).json()


def events_of(cid):
    with session_scope() as db:
        return [(e.kind, e.detail) for e in db.query(CallEvent).filter(CallEvent.tenant_id == T, CallEvent.call_id == cid).order_by(CallEvent.id)]


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def response(p: Pump, timeout=8.0):
    """One spoken response on the line: (turn, [frame sizes]) between the provider marks s<turn> and e<turn>."""
    k, m = p.until(lambda k, x: k == "json" and x.get("type") == "mark" and x["data"]["name"].startswith("s"), timeout)
    turn = m["data"]["name"][1:]
    sizes = []
    while True:
        k, x = p.until(lambda k, x: True, timeout)
        if k == "bytes":
            sizes.append(len(x))
        elif k == "json" and x.get("type") == "mark" and x["data"]["name"] == f"e{turn}":
            return int(turn), sizes
        elif k == "json" and x.get("type") == "killAudio":
            return int(turn), sizes


@pytest.fixture
def turns(api, monkeypatch):  # noqa: F811
    """Records the identity every LangGraph turn of a phone call runs with (the real graph still answers)."""
    seen = []
    real = tel.run_turn

    def spy(graph, *, tenant_id, student_id, ticket_id, text, today=None):
        seen.append((student_id, ticket_id))
        return real(graph, tenant_id=tenant_id, student_id=student_id, ticket_id=ticket_id, text=text, today=today)
    monkeypatch.setattr(tel, "run_turn", spy)
    return seen


# ---------------------------------------------------------------------------------------------------------------------------- refusal
@pytest.mark.parametrize("case", ["off", "bad_token", "short_token", "ip"])
def test_the_stream_endpoint_refuses_unless_enabled_and_the_secret_path_matches(api, case):  # noqa: F811
    setup_parts(api, [QUESTION])
    if case != "off":
        enable(api)
    path = PATH
    if case == "bad_token":
        path = PATH[:-1] + "x"
    if case == "short_token":
        from pydantic import SecretStr
        api.app.state.settings.telephony_stream_token = SecretStr("short")
        path = "/api/telephony/edesy/stream/short"
    if case == "ip":
        api.app.state.settings.telephony_allowed_ips = "203.0.113.9"
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect(path) as ws:
            ws.receive_text()
    assert calls(api) == []                                                              # nothing was registered


def test_a_malformed_first_frame_or_an_unsupported_rate_is_refused(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    enable(api)
    for first in ("not json", json.dumps(meta(rate=44100))):
        with api.websocket_connect(PATH) as ws:
            ws.send_text(first)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
    assert calls(api) == []


def test_the_admin_status_never_reveals_the_token_and_says_the_line_is_off_by_default(api):  # noqa: F811
    r = api.get("/api/admin/telephony/status", headers=auth(api, "admin1")).json()
    assert r["enabled"] is False and r["provider"] == "none" and r["paid_service"] is True and "switched off" in r["problems"][0]
    enable(api)
    r = api.get("/api/admin/telephony/status", headers=auth(api, "admin1")).json()
    assert r["ready"] is True and r["token_configured"] is True and TOKEN not in json.dumps(r)
    assert api.get("/api/admin/telephony/status", headers=auth(api, "staff1")).status_code == 403
    assert api.get("/api/admin/telephony/status").status_code == 401


# ---------------------------------------------------------------------------------------------------------------------------- a call
@pytest.mark.parametrize("rate", [16000, 8000])
def test_a_phone_call_greets_answers_and_appears_in_live_calls_at_the_line_rate(api, rate, turns):  # noqa: F811
    setup_parts(api, [QUESTION])
    enable(api, rate=rate)
    frame = rate * 20 // 1000 * 2
    with api.websocket_connect(PATH) as ws:
        ws.send_text(json.dumps(meta(frm="+1 415 555 0100", rate=rate)))
        p = Pump(ws)
        _, sizes = response(p)                                                          # the greeting
        assert sizes and set(sizes) == {frame}                                           # 20 ms frames at the line rate (TTS was 22.05 kHz)
        [c] = calls(api)
        assert (c["channel"], c["caller_status"], c["caller_masked"], c["caller"], c["status"]) == ("phone", "unknown", "+1 ******0100", "anonymous", "active_ai")
        speak(ws, rate)
        _, sizes = response(p)                                                          # the answer to the question
        assert sizes and set(sizes) == {frame}
        assert turns and turns[-1][0] == ANONYMOUS                                          # an unknown caller is anonymous
    assert wait_for(lambda: api.get(f"/api/staff/calls/{c['id']}", headers=auth(api, "staff1")).json()["status"] in ("ended", "failed"))
    kinds = [k for k, _ in events_of(c["id"])]
    assert kinds[0] == "created" and "caller_identified" in kinds
    with session_scope() as db:
        row = db.get(Call, (T, c["id"]))
        assert row.caller_hash and E164 not in str((row.caller_hash, row.caller_masked)) and row.provider == "edesy" and row.provider_call_ref == "CA_test1"


# ---------------------------------------------------------------------------------------------------------------------------- identity
def test_a_matching_number_is_only_a_claim_until_the_pin_proves_it(api, turns):  # noqa: F811
    setup_parts(api, [QUESTION, QUESTION])
    enable(api)
    set_student_phone()
    set_pin()
    with api.websocket_connect(PATH) as ws:
        ws.send_text(json.dumps(meta()))
        p = Pump(ws)
        response(p)
        [c] = calls(api)
        assert (c["caller_status"], c["caller"], c["caller_masked"]) == ("claimed", "anonymous", "+91 ******2345")
        speak(ws)
        response(p)
        assert turns[-1][0] == ANONYMOUS                                                   # the number matched asha, yet no record is reachable
        keys(ws, "*111111#")
        response(p)                                                                      # "That PIN is not correct"
        assert calls(api)[0]["caller_status"] == "claimed"
        keys(ws, "*482913#")
        response(p)                                                                      # "Thank you, you are verified"
        v = calls(api)[0]
        assert (v["caller_status"], v["caller"]) == ("verified", "student")
        speak(ws)
        response(p)
        assert turns[-1][0] == "stu_asha" and turns[-1][1] != turns[0][1]               # now her own records, in a ticket bound to her
    kinds = [(k, d) for k, d in events_of(c["id"]) if k.startswith("caller_")]
    assert ("caller_verification_failed", {"result": "wrong"}) in kinds and any(k == "caller_verified" and d == {"role": "student"} for k, d in kinds)
    assert "482913" not in str(events_of(c["id"])) and "111111" not in str(events_of(c["id"]))


def test_wrong_pins_lock_phone_verification_across_calls_and_the_right_pin_is_then_refused(api, turns):  # noqa: F811
    setup_parts(api, [QUESTION])
    enable(api, phone_pin_max_attempts=3, phone_pin_attempts_per_call=3)
    set_student_phone()
    set_pin()
    for digits in ("*000001#*000002#", "*000003#*482913#"):
        with api.websocket_connect(PATH) as ws:
            ws.send_text(json.dumps(meta()))
            p = Pump(ws)
            response(p)
            keys(ws, digits)
            response(p)
            response(p)
    with session_scope() as db:
        assert db.get(PhonePin, (T, "asha")).locked_until is not None
    assert all(c["caller_status"] == "claimed" for c in calls(api))                     # the correct PIN after the lock did NOT verify
    r = api.get("/api/admin/telephony/accounts", headers=auth(api, "admin1")).json()["items"]
    assert next(a for a in r if a["username"] == "asha")["locked_until"]
    assert api.delete("/api/admin/telephony/pins/asha", headers=auth(api, "admin1")).json() == {"reset": True}
    acts = [a["action"] for a in api.get("/api/admin/telephony/audit", headers=auth(api, "admin1")).json()["items"]]
    assert "pin_locked" in acts and "pin_reset_by_admin" in acts


def test_a_number_shared_by_two_accounts_cannot_be_verified_by_pin(api, turns):  # noqa: F811
    setup_parts(api, [QUESTION])
    enable(api)
    set_student_phone("stu_asha")
    set_student_phone("stu_meera")
    set_pin("asha")
    with api.websocket_connect(PATH) as ws:
        ws.send_text(json.dumps(meta()))
        p = Pump(ws)
        response(p)
        keys(ws, "*482913#")
        response(p)
        c = calls(api)[0]
        assert c["caller_status"] == "ambiguous" and c["caller"] == "anonymous"
    assert ("caller_verification_failed", {"result": "ambiguous"}) in events_of(c["id"])


def test_a_staff_number_is_recognised_but_gives_no_student_records(api, turns):  # noqa: F811
    setup_parts(api, [QUESTION])
    enable(api)
    adm = auth(api, "admin1")
    assert api.put("/api/admin/telephony/phones/staff1", json={"phone": NUMBER}, headers=adm).json()["phone_masked"] == "+91 ******2345"
    set_pin("staff1")
    with api.websocket_connect(PATH) as ws:
        ws.send_text(json.dumps(meta()))
        p = Pump(ws)
        response(p)
        assert calls(api)[0]["caller_role"] == "staff"
        keys(ws, "*482913#")
        response(p)
        speak(ws)
        response(p)
        c = calls(api)[0]
        assert (c["caller_status"], c["caller"]) == ("verified", "anonymous") and turns[-1][0] == ANONYMOUS


# ---------------------------------------------------------------------------------------------------------------------------- takeover
def test_staff_take_over_a_phone_call_queued_ai_audio_is_flushed_staff_audio_reaches_the_line_and_the_end_hangs_up(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    enable(api, rate=8000)
    with api.websocket_connect(PATH) as line:
        line.send_text(json.dumps(meta(rate=8000)))
        p = Pump(line)
        response(p)
        cid = calls(api)[0]["id"]
        with staff_stream(api, cid, None) as sws:
            sp = open_stream(api, sws, tok(api, "staff1"))
            assert api.post(f"/api/staff/calls/{cid}/takeover", headers=auth(api, "staff1")).status_code == 200
            p.until(lambda k, x: k == "json" and x.get("type") == "killAudio")         # whatever the provider still had queued from the AI is dropped
            p.until(lambda k, x: k == "json" and x.get("type") == "mark" and x["data"]["name"].startswith("e"), 8)   # the hand-over announcement played
            sp.json_of("announcement", state="finished", timeout=8)
            own = next(x for k, x in sp.log if k == "json" and x.get("type") == "ownership" and x.get("owner") == "staff")
            sws.send_json({"type": "mic_open", "epoch": own["epoch"]})
            assert sp.json_of("mic")["state"] == "open"
            for _ in range(5):                                                           # 5 x 40 ms of 16 kHz staff speech ...
                sws.send_bytes(own["epoch"].to_bytes(4, "little") + b"\x10\x00" * 640)
            p.until(lambda k, x: k == "json" and x.get("type") == "mark" and x["data"]["name"].startswith("s"))
            got = [len(x) for k, x in p.quiet(0.6) if k == "bytes"]
            assert got and set(got) == {320} and len(got) >= 9                          # ... reach the 8 kHz line as 20 ms frames (resampled, not dropped)
            assert api.post(f"/api/staff/calls/{cid}/end", headers=auth(api, "staff1")).json()["status"] == "ended"
            p.until(lambda k, x: k == "json" and x.get("type") == "disconnect", 6)       # the provider is told to hang up
    v = api.get(f"/api/staff/calls/{cid}", headers=auth(api, "staff1")).json()
    assert (v["channel"], v["end_reason"], v["intervention_by"]) == ("phone", "ended_by_staff", "staff1")


# ---------------------------------------------------------------------------------------------------------------------------- trial consent by keypad
def test_trial_mode_on_the_phone_speaks_the_disclosure_and_takes_recording_consent_from_the_keypad(api, tmp_path):  # noqa: F811
    from app.trial.recording import LocalProvider
    api.app.state.recording_provider = LocalProvider(str(tmp_path / "rec"))
    adm = auth(api, "admin1")
    v = api.get("/api/admin/trial", headers=adm).json()["settings"]["version"]
    assert api.put("/api/admin/trial", headers=adm, json={"expected_version": v, "enabled": True, "recording_offered": True}).status_code == 200
    setup_parts(api, [QUESTION])
    enable(api)
    with api.websocket_connect(PATH) as ws:
        ws.send_text(json.dumps(meta()))
        p = Pump(ws)
        response(p, 10)                                                                  # the disclosure
        response(p, 10)                                                                  # the keypad consent options
        cid = calls(api)[0]["id"]
        assert calls(api)[0]["trial_mode"] is True
        keys(ws, "1")
        assert wait_for(lambda: "recording_started" in [k for k, _ in events_of(cid)], 8)
        keys(ws, "3")
        assert wait_for(lambda: "consent_withdrawn" in [k for k, _ in events_of(cid)], 8)
    kinds = [k for k, _ in events_of(cid)]
    assert kinds.index("consent_offered") < kinds.index("consent_granted") < kinds.index("recording_started")
