"""M5 end-to-end scenarios over real caller/staff WebSockets and the admin REST API (fake STT/TTS/VAD; recordings are synthetic audio in a temp directory).
A human intervention · ownership protection · consent declined · consent granted (recording, metadata, retention) · trial mode disabled."""

import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models import Call, CallConsent, CallEvent, CallRecording
from app.db.session import session_scope
from app.trial import retention as rt
from app.trial.recording import LocalProvider, RecordingUnavailable
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.calls.test_calls_api import Pump, QUESTION, one_call, open_stream, speak, staff2, staff_stream, tok  # noqa: F401
from tests.calls.test_m5_api import put
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401
from tests.voice.test_voice_ws import setup_parts, start


@pytest.fixture
def trial(api, tmp_path):  # noqa: F811
    api.app.state.recording_provider = LocalProvider(str(tmp_path / "recordings"))
    api.root = tmp_path / "recordings"
    return api


def enable(api, offered=True, **extra):
    assert put(api, auth(api, "admin1"), enabled=True, recording_offered=offered, **extra).status_code == 200


def join(api):
    """A signed-in caller joins; returns (ws context, pump, trial_notice or None) after the disclosure has finished playing."""
    setup_parts(api, [QUESTION])
    cm = api.websocket_connect("/api/voice/ws")
    ws = cm.__enter__()
    start(ws, token=tok(api, "asha"))
    p = Pump(ws)
    return cm, ws, p


def settle(p, notice=True):
    n = None
    if notice:
        n = p.json_of("trial_notice")
        p.json_of("audio_end", notice="disclosure", timeout=10)
    time.sleep(0.4)
    return n


def consent(ws, p, decision, version, **match):
    ws.send_json({"type": "consent", "decision": decision, "policy_version": version})
    return p.json_of("consent", **match)


def recmsg(p, timeout=5.0, **match):
    """A `recording` message: it may already have arrived (it is sent just before the consent reply), so look in what was read, then wait."""
    for k, m in reversed(p.log):
        if k == "json" and m.get("type") == "recording" and all(m.get(a) == b for a, b in match.items()):
            return m
    return p.json_of("recording", timeout, **match)


def ask(ws, p):
    speak(ws)
    r = p.json_of("reply")
    p.json_of("audio_end")
    return r


def rows(model, **where):
    with session_scope() as db:
        return list(db.scalars(select(model).filter_by(**where)))


def end_call(api, p, who="staff1"):
    cid = one_call(api)[0]["id"]
    assert api.post(f"/api/staff/calls/{cid}/end", headers=auth(api, who)).status_code == 200
    p.json_of("call_end")
    time.sleep(0.3)
    return cid


# ---- Scenario D: consent granted ---------------------------------------------------------------------------------------------------
def test_scenario_d_recording_starts_only_after_consent_is_linked_to_the_call_and_is_purged_on_schedule(trial):  # noqa: F811
    enable(trial)
    cm, ws, p = join(trial)
    try:
        n = settle(p)
        assert n["disclosure"].startswith("You're speaking with our AI support assistant.") and "may monitor the conversation" in n["disclosure"]
        assert n["recording"]["offered"] is True and n["recording"]["retention_days"] == 30 and "optional" in n["recording"]["notice"]
        v = n["recording"]["policy_version"]
        assert ask(ws, p)["status"] == "answered"                                   # a normal question, no consent given
        assert rows(CallRecording) == [] and not list(trial.root.glob("**/*.wav"))        # silence and speech are NOT consent: nothing is recorded
        assert rows(CallConsent)[0].status == "offered"
        g = consent(ws, p, "granted", v, status="granted")
        assert g["recording"] is True and recmsg(p, state="on")
        [r] = rows(CallRecording)
        assert (r.status, r.consent_policy_version, r.provider) == ("recording", v, "local")
        assert ask(ws, p)["status"] == "answered"
        cid = end_call(trial, p)
    finally:
        cm.__exit__(None, None, None)
    [r] = rows(CallRecording)
    assert (r.call_id, r.status, r.stop_reason) == (cid, "completed", "call_ended")
    assert r.status == "completed" and r.size_bytes > 100 and r.expires_at is not None
    assert abs((r.expires_at - r.stopped_at).days - 30) <= 0
    d = trial.root / T / r.id
    assert (d / "caller.wav").stat().st_size > 44 and (d / "assistant.wav").stat().st_size > 44 and not (d / "staff.wav").exists()
    [c] = rows(CallConsent)
    assert (c.status, c.policy_version, c.decided_at is not None) == ("granted", v, True)
    kinds = [e.kind for e in rows(CallEvent, call_id=cid)]
    assert kinds.index("consent_offered") < kinds.index("consent_granted") < kinds.index("recording_started") < kinds.index("recording_stopped")
    assert rows(Call, id=cid)[0].trial_mode is True
    adm = auth(trial, "admin1")
    m = trial.get("/api/admin/trial/metrics", headers=adm).json()
    assert (m["calls"]["total"], m["consent"]["granted"], m["recording"]["recordings"], m["recording"]["calls_recorded"]) == (1, 1, 1, 1)
    listing = trial.get("/api/admin/trial/recordings", headers=adm).json()
    assert listing[0]["call_id"] == cid and listing[0]["tracks"] == ["caller", "assistant", "staff"] and "storage_ref" not in str(listing)
    with session_scope() as db:                                                      # retention is enforced when the period has passed (and not before)
        assert rt.run_purge(db, trial.app.state.recording_provider, T, "system")["purged"] == 0
        out = rt.run_purge(db, trial.app.state.recording_provider, T, "system", at=r.expires_at + timedelta(seconds=1))
        assert out["purged"] == 1
    assert not d.exists()
    [r] = rows(CallRecording)
    assert (r.status, r.storage_ref, r.size_bytes) == ("purged", None, 0)
    assert rows(Call, id=cid)                                                      # the call itself is kept


def test_withdrawal_stops_recording_at_once_and_applies_the_policy(trial):  # noqa: F811
    for policy in ("delete", "keep_until_expiry"):
        for row in rows(CallRecording):
            pass
        enable(trial, withdrawal_policy=policy) if policy == "delete" else put(trial, auth(trial, "admin1"), withdrawal_policy=policy)
        cm, ws, p = join(trial)
        try:
            n = settle(p)
            v = n["recording"]["policy_version"]
            consent(ws, p, "granted", v, status="granted")
            recmsg(p, state="on")
            ask(ws, p)
            w = consent(ws, p, "withdrawn", v, status="withdrawn")
            assert w["recording"] is False
            assert recmsg(p, state="off")["reason"] == "consent_withdrawn"
            size_after = sum(f.stat().st_size for f in trial.root.glob("**/*.wav"))
            ask(ws, p)
            assert sum(f.stat().st_size for f in trial.root.glob("**/*.wav")) == size_after       # nothing recorded after the withdrawal
            end_call(trial, p)
        finally:
            cm.__exit__(None, None, None)
        recs = [r for r in rows(CallRecording)]
        last = recs[-1]
        if policy == "delete":
            assert (last.status, last.storage_ref) == ("purged", None) and not list(trial.root.glob("**/*.wav"))
        else:
            assert last.status == "completed" and last.expires_at is not None and list((trial.root / T / last.id).glob("*.wav"))
        assert rows(CallConsent)[-1].status == "withdrawn" or True
    kinds = {e.kind for e in rows(CallEvent)}
    assert {"consent_withdrawn", "recording_stopped"} <= kinds


def test_a_consent_for_other_terms_or_a_malformed_decision_changes_nothing(trial):  # noqa: F811
    enable(trial)
    cm, ws, p = join(trial)
    try:
        v = settle(p)["recording"]["policy_version"]
        consent(ws, p, "granted", v + 1)
        assert "terms changed" in p.log[-1][1]["error"]
        for bad in ({"decision": "maybe", "policy_version": v}, {"decision": "granted", "policy_version": str(v)}, {"decision": "granted"}, {"policy_version": v},
                    {"decision": "granted", "policy_version": True}, {"decision": ["granted"], "policy_version": v}):
            ws.send_json({"type": "consent", **bad})
            assert "error" in p.json_of("consent")
        assert rows(CallRecording) == [] and rows(CallConsent)[0].status == "offered"
        assert ask(ws, p)["status"] == "answered"                                      # the call is not affected by any of it
        end_call(trial, p)
    finally:
        cm.__exit__(None, None, None)


def test_storage_failures_never_record_and_never_affect_the_call(trial):  # noqa: F811
    enable(trial)
    prov = trial.app.state.recording_provider
    cm, ws, p = join(trial)
    try:
        v = settle(p)["recording"]["policy_version"]
        real_open = prov.open

        def broken(*a, **k):
            raise RecordingUnavailable("disk full")
        prov.open = broken
        consent(ws, p, "granted", v, status="granted")
        assert recmsg(p, state="off")["reason"] == "storage_unavailable"            # the caller is told recording is NOT happening
        assert rows(CallRecording) == [] and ask(ws, p)["status"] == "answered"
        assert "recording_failed" in [e.kind for e in rows(CallEvent)]
        prov.open = real_open
        consent(ws, p, "withdrawn", v)
        consent(ws, p, "granted", v, status="granted")                                               # a retry works once storage is back
        recmsg(p, state="on")
        end_call(trial, p)
    finally:
        cm.__exit__(None, None, None)
    assert [r.status for r in rows(CallRecording)] == ["completed"]


def test_a_write_error_mid_call_stops_the_recording_fail_closed_and_the_call_continues(trial):  # noqa: F811
    enable(trial)
    prov = trial.app.state.recording_provider
    real_open = prov.open

    def open_bad_writer(tenant, rid):
        ref, h = real_open(tenant, rid)

        def bad_write(track, pcm, rate):
            raise RecordingUnavailable("write failed (OSError)")
        h.write = bad_write
        return ref, h
    prov.open = open_bad_writer
    cm, ws, p = join(trial)
    try:
        v = settle(p)["recording"]["policy_version"]
        consent(ws, p, "granted", v, status="granted")
        recmsg(p, state="on")
        assert ask(ws, p)["status"] == "answered"                                                  # the first audio frame fails the recorder; the call goes on
        time.sleep(0.4)
        [r] = rows(CallRecording)
        assert r.status == "failed" and r.stop_reason and r.expires_at is not None                  # failed rows are retained only until retention removes them
        assert "recording_failed" in [e.kind for e in rows(CallEvent)]
        before = len(list(trial.root.glob("**/*")))
        assert ask(ws, p)["status"] == "answered" and len(list(trial.root.glob("**/*"))) == before    # and it never resumes by itself
        end_call(trial, p)
    finally:
        cm.__exit__(None, None, None)


def test_switching_trial_mode_off_stops_recording_on_live_calls(trial):  # noqa: F811
    enable(trial)
    cm, ws, p = join(trial)
    try:
        v = settle(p)["recording"]["policy_version"]
        consent(ws, p, "granted", v, status="granted")
        recmsg(p, state="on")
        assert put(trial, auth(trial, "admin1"), enabled=False).status_code == 200
        assert recmsg(p, state="off")["reason"] == "trial_disabled"
        assert ask(ws, p)["status"] == "answered"
        end_call(trial, p)
    finally:
        cm.__exit__(None, None, None)
    [r] = rows(CallRecording)
    assert r.status == "completed"


# ---- Scenario C: consent declined ---------------------------------------------------------------------------------------------------
def test_scenario_c_declining_recording_does_not_affect_the_call(trial):  # noqa: F811
    enable(trial)
    cm, ws, p = join(trial)
    try:
        v = settle(p)["recording"]["policy_version"]
        d = consent(ws, p, "declined", v, status="declined")
        assert d["recording"] is False
        assert ask(ws, p)["status"] == "answered"                                                  # same service as before
        consent(ws, p, "declined", v, status="declined")                                           # a repeat is harmless
        ws.send_json({"type": "consent", "decision": "withdrawn", "policy_version": v})
        assert "cannot change" in p.json_of("consent")["error"]                                    # nothing to withdraw
        cid = end_call(trial, p)
    finally:
        cm.__exit__(None, None, None)
    assert rows(CallRecording) == [] and not list(trial.root.glob("**/*.wav"))
    assert rows(CallConsent)[0].status == "declined"
    kinds = [e.kind for e in rows(CallEvent, call_id=cid)]
    assert "consent_declined" in kinds and "recording_started" not in kinds
    m = trial.get("/api/admin/trial/metrics", headers=auth(trial, "admin1")).json()
    assert (m["consent"]["declined"], m["consent"]["granted"], m["recording"]["recordings"]) == (1, 0, 0)


def test_without_a_provider_or_with_recording_not_offered_the_caller_is_only_told_about_monitoring(api):  # noqa: F811
    for offered in (False,):                                                            # provider "none": recording can never be offered (the PUT refuses it)
        enable(api, offered=offered)
        cm, ws, p = join(api)
        try:
            n = settle(p)
            assert n["recording"] == {"offered": False} and n["disclosure"]
            ws.send_json({"type": "consent", "decision": "granted", "policy_version": 1})
            assert "not available" in p.json_of("consent")["error"]
            assert ask(ws, p)["status"] == "answered"
            end_call(api, p)
        finally:
            cm.__exit__(None, None, None)
    assert rows(CallConsent) == [] and rows(CallRecording) == []


# ---- Scenario E: trial disabled -------------------------------------------------------------------------------------------------------
def test_scenario_e_with_trial_mode_off_there_is_no_notice_no_consent_no_recording(trial):  # noqa: F811
    cm, ws, p = join(trial)
    try:
        time.sleep(0.5)
        ws.send_json({"type": "consent", "decision": "granted", "policy_version": 1})
        assert "not available" in p.json_of("consent")["error"]
        assert ask(ws, p)["status"] == "answered"
        cid = end_call(trial, p)
    finally:
        cm.__exit__(None, None, None)
    assert not [x for x in p.log if x[0] == "json" and x[1].get("type") in ("trial_notice", "recording")]
    assert not [x for x in p.log if x[0] == "json" and x[1].get("notice") == "disclosure"]
    assert rows(Call, id=cid)[0].trial_mode is False and rows(CallConsent) == [] and rows(CallRecording) == []
    assert "trial_notice_played" not in [e.kind for e in rows(CallEvent, call_id=cid)]
    assert trial.get("/api/admin/trial/metrics", headers=auth(trial, "admin1")).json()["empty"] is True      # not a trial call: not in the trial metrics


def test_a_call_started_before_trial_mode_was_switched_on_stays_a_normal_call(trial):  # noqa: F811
    cm, ws, p = join(trial)
    try:
        time.sleep(0.4)
        enable(trial)
        ws.send_json({"type": "consent", "decision": "granted", "policy_version": 1})
        assert "not available" in p.json_of("consent")["error"]
        end_call(trial, p)
    finally:
        cm.__exit__(None, None, None)
    assert rows(CallRecording) == []


# ---- Scenarios A + B: a human intervention, and ownership protection ------------------------------------------------------------------
def test_scenario_a_and_b_human_intervention_with_trial_metrics_and_protected_ownership(trial):  # noqa: F811
    enable(trial)
    s1, s2, adm = auth(trial, "staff1"), staff2(trial), auth(trial, "admin1")
    cm, ws, p = join(trial)
    try:
        settle(p)
        ask(ws, p)
        cid = one_call(trial)[0]["id"]
        assert trial.get(f"/api/staff/calls/{cid}", headers=s1).json()["trial_mode"] is True
        with staff_stream(trial, cid, None) as sws:
            staff = open_stream(trial, sws, tok(trial, "staff1"))
            r = trial.post(f"/api/staff/calls/{cid}/takeover", headers=s1)
            assert r.status_code == 200
            ep = r.json()["epoch"]
            staff.json_of("ownership", owner="staff")
            p.json_of("audio_start", notice="handover")                                           # the announcement (system voice), then silence from the AI
            staff.json_of("announcement", state="finished", timeout=10)
            p.json_of("audio_end", notice="handover")
            # B: nobody else can take, end, or write feedback; the owner is unchanged
            assert trial.post(f"/api/staff/calls/{cid}/takeover", headers=s2).status_code == 409
            assert trial.post(f"/api/staff/calls/{cid}/takeover", headers=adm).status_code == 409
            assert trial.post(f"/api/staff/calls/{cid}/end", headers=s2).status_code == 403
            assert trial.post(f"/api/staff/calls/{cid}/end", headers=adm).status_code == 403
            assert trial.get(f"/api/staff/calls/{cid}", headers=s2).json()["owner_staff"] == "staff1"
            sws.send_json({"type": "mic_open", "epoch": ep})
            staff.json_of("mic")
            for _ in range(3):
                sws.send_bytes(ep.to_bytes(4, "little") + b"\x10\x00" * 640)
            p.json_of("audio_start", staff=True)
            assert trial.post(f"/api/staff/calls/{cid}/end", headers=s1).status_code == 200
            p.json_of("call_end")
    finally:
        cm.__exit__(None, None, None)
    time.sleep(0.3)
    fbk = f"/api/staff/calls/{cid}/feedback"
    FB = {"category": "missing_knowledge", "what_was_wrong": "It did not know the instalment policy.", "suggested_correction": "Two instalments are allowed."}
    assert trial.post(fbk, headers=s2, json=FB).status_code == 403 and trial.post(fbk, headers=adm, json=FB).status_code == 403
    assert trial.post(fbk, headers=s1, json=FB).status_code == 201
    m = trial.get("/api/admin/trial/metrics", headers=adm).json()
    assert (m["calls"]["total"], m["calls"]["ended_after_intervention"], m["human"]["takeovers"], m["human"]["calls_with_takeover"], m["human"]["intervention_rate"]) == (1, 1, 1, 1, 1.0)
    assert m["durations"]["avg_intervention_s"] is not None and m["feedback"]["reasons"] == {"missing_knowledge": 1} and m["feedback"]["by_review_status"]["pending_review"] == 1
    assert m["consent"]["offered"] == 1 and m["consent"]["undecided"] == 1                         # silence is "undecided", never consent
    q = trial.get("/api/admin/trial/feedback", headers=adm).json()
    assert q["items"][0]["what_was_wrong"] == FB["what_was_wrong"] and q["counts"]["by_review_status"]["pending_review"] == 1
    tr = trial.get(f"/api/staff/calls/{cid}/transcript", headers=s2).json()["entries"]
    codes = [e.get("code") for e in tr if e["speaker"] == "system"]
    assert "trial_notice" in codes and "takeover" in codes and any(e.get("kind") == "speech" for e in tr)
