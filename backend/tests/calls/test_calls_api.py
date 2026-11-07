"""M4 over the real FastAPI app, the real support graph (offline fakes) and real WebSockets: the staff API, authorization, tenant isolation, the staff audio
socket, and the full call sequence. Fake STT/TTS/VAD and synthetic audio: this proves the plumbing and the rules, not microphones or speakers."""

import json
import logging
import queue
import threading
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from app.auth.tokens import Principal, issue_token
from app.db.models import Account, Call
from app.db.session import session_scope
from tests.api.conftest import PW, T, api, auth  # noqa: F401
from tests.api.test_staff_admin import OTHER, other_headers
from tests.calls.apidb import seeded_db  # noqa: F401  (autouse: a file-backed SQLite database for tests that use sockets and REST together)
from tests.calls.rig import events
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401
from tests.voice.audio import chunks, tone, utterance
from tests.voice.test_voice_ws import setup_parts, start

QUESTION = "Can I pay the course fee in instalments?"
ALL = [("get", ""), ("post", "/listen"), ("post", "/takeover"), ("post", "/end"), ("get", "/events"), ("get", "/transcript"), ("get", "/feedback"), ("post", "/feedback/skip")]
BODY_ROUTES = [("post", "/feedback"), ("put", "/feedback")]      # need a JSON body to get past validation; auth is checked first


def staff2(api):  # noqa: F811
    """A second staff account (the shared fixture has only staff1 and admin1)."""
    from app.auth.accounts import create_account
    with session_scope() as db:
        if db.get(Account, (T, "staff2")) is None:
            create_account(db, T, "staff2", PW, "staff")
    return auth(api, "staff2")


class Pump:
    """Reads one TestClient websocket on its own thread so a test can wait for a message, or assert that none arrives, without ever blocking forever."""

    def __init__(self, ws):
        self.ws, self.q, self.log = ws, queue.Queue(), []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            while True:
                m = self.ws.receive()
                if m["type"] in ("websocket.close", "websocket.disconnect"):
                    self.q.put(("closed", m.get("code")))
                    return
                self.q.put(("bytes", m["bytes"]) if m.get("bytes") is not None else ("json", json.loads(m["text"])))
        except Exception as e:  # noqa: BLE001
            self.q.put(("closed", repr(e)))

    def until(self, pred, timeout=5.0):
        end = time.monotonic() + timeout
        while True:
            try:
                item = self.q.get(timeout=max(0.01, end - time.monotonic()))
            except queue.Empty:
                raise AssertionError(f"timed out; saw {[(k, (p if k != 'bytes' else len(p))) for k, p in self.log[-12:]]}") from None
            self.log.append(item)
            if pred(*item):
                return item

    def json_of(self, kind, timeout=5.0, **match):
        return self.until(lambda k, p: k == "json" and p.get("type") == kind and all(p.get(a) == b for a, b in match.items()), timeout)[1]

    def quiet(self, secs):
        """Everything that arrives within `secs`."""
        got, end = [], time.monotonic() + secs
        while time.monotonic() < end:
            try:
                item = self.q.get(timeout=max(0.01, end - time.monotonic()))
            except queue.Empty:
                break
            self.log.append(item)
            got.append(item)
        return got


def speak(ws, pcm=None):
    for c in chunks(pcm if pcm is not None else utterance()):
        ws.send_bytes(c)


def tok(api, user):  # noqa: F811
    return auth(api, user)["Authorization"].split()[1]


def staff_stream(api, call_id, token, **kw):  # noqa: F811
    return api.websocket_connect(f"/api/staff/calls/{call_id}/stream", **kw)


def open_stream(api, ws, token):  # noqa: F811
    ws.send_json({"type": "auth", "token": token})
    p = Pump(ws)
    p.joined = p.json_of("joined")
    return p


def one_call(api, user_token=None):  # noqa: F811
    return api.get("/api/staff/calls", headers=auth(api, "staff1")).json()


def is_ai_audio(k, p):
    return k == "bytes"


# ---- authorization ----------------------------------------------------------------------------------------------------------
def test_every_live_call_route_requires_staff_or_admin(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        cid = one_call(api)[0]["id"]
        for method, suffix in [("get", "")] + ALL + BODY_ROUTES:
            url = f"/api/staff/calls/{cid}{suffix}"
            assert getattr(api, method)(url).status_code == 401, url                                        # no token
            assert getattr(api, method)(url, headers=auth(api, "asha")).status_code == 403, url             # a student
        assert api.get("/api/staff/interventions").status_code == 401 and api.get("/api/staff/interventions", headers=auth(api, "asha")).status_code == 403
        assert api.post(f"/api/staff/calls/{cid}/return", headers=auth(api, "staff1")).status_code in (404, 405)       # the operation does not exist (M4.1)
        assert api.get("/api/staff/calls").status_code == 401 and api.get("/api/staff/calls", headers=auth(api, "asha")).status_code == 403
        assert api.get("/api/staff/calls", headers=auth(api, "staff1")).status_code == 200
        assert api.get("/api/staff/calls", headers=auth(api, "admin1")).status_code == 200


def test_a_call_of_another_tenant_is_not_found_on_every_operation_and_not_listed(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    other = other_headers(api, "otherstaff", "staff")
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        cid = one_call(api)[0]["id"]
        assert api.get("/api/staff/calls", headers=other).json() == []
        for method, suffix in ALL:
            assert getattr(api, method)(f"/api/staff/calls/{cid}{suffix}", headers=other).status_code == 404, suffix
        h = auth(api, "staff1")
        assert api.get(f"/api/staff/calls/{cid}", headers=h).json()["status"] == "active_ai"      # untouched by the other tenant's attempts
        assert api.get(f"/api/staff/calls/{cid}/events", headers=h).json()[-1]["kind"] == "connected"
        otok = other[("Authorization")].split()[1]
        with staff_stream(api, cid, otok) as sws:                                                   # the socket says the same: not found
            sws.send_json({"type": "auth", "token": otok})
            m = sws.receive_json()
            assert m["type"] == "error" and "not found" in m["detail"]


def test_the_api_exposes_a_reference_not_the_ticket_id_and_student_ids_only_to_admins(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    with api.websocket_connect("/api/voice/ws") as ws:
        ticket = start(ws, token=tok(api, "asha"))["ticket_id"]
        c_staff = api.get("/api/staff/calls", headers=auth(api, "staff1")).json()[0]
        c_admin = api.get("/api/staff/calls", headers=auth(api, "admin1")).json()[0]
        blob = json.dumps([c_staff, c_admin])
        assert ticket not in blob and "ticket_id" not in c_staff and c_staff["ticket_ref"] == ticket.split(".")[0][:10]
        assert "student_id" not in c_staff and c_admin["student_id"] == "stu_asha" and c_staff["caller"] == "student"


# ---- REST flow ---------------------------------------------------------------------------------------------------------------
def test_rest_lifecycle_listen_takeover_end_and_events_with_no_return_and_owner_only_end(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    s1, adm, s2 = auth(api, "staff1"), auth(api, "admin1"), staff2(api)
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        pump = Pump(ws)
        [c] = api.get("/api/staff/calls", headers=s1).json()
        cid = c["id"]
        assert (c["status"], c["owner"], c["live"], c["listeners"], c["short_id"]) == ("active_ai", "ai", True, 0, cid[2:10]) and c["duration_s"] >= 0
        assert (c["category"], c["can_takeover"], c["can_end"], c["intervened"]) == ("ai_handling", True, True, False)
        assert c["created_at"].endswith("Z") or "+" in c["created_at"]
        r = api.post(f"/api/staff/calls/{cid}/listen", headers=s1).json()
        assert r["ws_path"] == f"/api/staff/calls/{cid}/stream" and r["status"] == "active_ai"
        t1 = api.post(f"/api/staff/calls/{cid}/takeover", headers=s1)
        assert t1.status_code == 200 and (t1.json()["status"], t1.json()["owner"], t1.json()["owner_staff"], t1.json()["is_mine"]) == ("staff_takeover", "staff", "staff1", True)
        assert (t1.json()["category"], t1.json()["intervention_by"], t1.json()["can_end"], t1.json()["can_takeover"]) == ("human_handling", "staff1", True, False)
        pump.until(lambda k, p: k == "json" and p.get("type") == "audio_end" and p.get("notice") == "handover")      # the announcement has been played
        time.sleep(0.4)                                                          # ... and its audit rows written (the SQLite test DB is one shared connection)
        assert api.post(f"/api/staff/calls/{cid}/takeover", headers=s1).json()["epoch"] == t1.json()["epoch"]      # idempotent
        t2 = api.post(f"/api/staff/calls/{cid}/takeover", headers=adm)
        assert t2.status_code == 409 and "already" in t2.json()["detail"]
        for h in (adm, s2):                                                      # what other staff and the admin see: listen yes, control no
            v = api.get(f"/api/staff/calls/{cid}", headers=h).json()
            assert (v["is_mine"], v["can_end"], v["can_takeover"], v["can_listen"], v["category"]) == (False, False, False, True, "human_handling")
            assert api.post(f"/api/staff/calls/{cid}/listen", headers=h).status_code == 200
            e403 = api.post(f"/api/staff/calls/{cid}/end", headers=h)
            assert e403.status_code == 403 and "took over" in e403.json()["detail"]
        assert api.get(f"/api/staff/calls/{cid}", headers=s1).json()["status"] == "staff_takeover"      # still running after both refused attempts
        assert api.post(f"/api/staff/calls/{cid}/return", headers=s1).status_code in (404, 405)         # there is no such operation
        e = api.post(f"/api/staff/calls/{cid}/end", headers=s1).json()
        assert (e["status"], e["end_reason"], e["live"], e["category"], e["intervened"]) == ("ended", "ended_by_staff", False, "ended_after_intervention", True)
        assert e["feedback"] == {"state": "awaiting", "form_open": True, "review_status": None}      # only the owner is told about the form
        assert "feedback" not in api.get(f"/api/staff/calls/{cid}", headers=adm).json()
        assert api.post(f"/api/staff/calls/{cid}/end", headers=s1).json()["status"] == "ended"                  # idempotent
        for op in ("takeover", "listen"):
            r = api.post(f"/api/staff/calls/{cid}/{op}", headers=s1)
            assert r.status_code == 409 and "ended" in r.json()["detail"], op
        pump.until(lambda k, p: k == "json" and p.get("type") == "call_end" and p["reason"] == "ended_by_staff")
        pump.until(lambda k, p: k == "closed")
    ev = api.get(f"/api/staff/calls/{cid}/events", headers=s1).json()
    core = [x for x in ev if not x["kind"].startswith("announcement")]
    assert [x["kind"] for x in core] == ["created", "connected", "takeover_requested", "takeover_succeeded", "takeover_requested", "takeover_failed", "control_denied",
                                         "control_denied", "ended"]
    assert [x["actor"] for x in core][2:] == ["staff1", "staff1", "admin1", "admin1", "admin1", "staff2", "staff1"]
    assert "returned_to_ai" not in [x["kind"] for x in ev]
    assert all(set(x["detail"]) <= {"reason", "role", "epoch", "listeners", "previous_owner", "duration_s", "connection", "status", "action", "category",
                                    "policy_version", "recording_id", "variant"} for x in ev)
    assert api.get(f"/api/staff/calls/{cid}/events?limit=0", headers=s1).status_code == 422


def test_unknown_call_ids_are_404(api):  # noqa: F811
    h = auth(api, "staff1")
    for method, suffix in ALL:
        assert getattr(api, method)(f"/api/staff/calls/c_doesnotexist{suffix}", headers=h).status_code == 404


def test_ended_calls_stay_listed_for_a_while_with_their_final_state(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        rows = api.get("/api/staff/calls", headers=auth(api, "staff1")).json()
        if rows and rows[0]["status"] == "ended":
            break
        time.sleep(0.05)
    assert rows[0]["status"] == "ended" and rows[0]["end_reason"] == "caller_disconnected" and rows[0]["live"] is False
    kinds = [x["kind"] for x in api.get(f"/api/staff/calls/{rows[0]['id']}/events", headers=auth(api, "staff1")).json()]
    assert kinds == ["created", "connected", "participant_disconnected", "ended"]


# ---- the staff socket: authentication, origin, expiry ------------------------------------------------------------------------
def first_message(api, cid, send, **kw):  # noqa: F811
    with staff_stream(api, cid, None, **kw) as ws:
        if send is not None:
            ws.send_json(send) if isinstance(send, dict) else ws.send_text(send)
        m = ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
        return m


def test_the_staff_socket_refuses_missing_bad_student_expired_and_unauthenticated_connections(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    secret = api.app.state.secret
    with api.websocket_connect("/api/voice/ws") as caller:
        start(caller)
        cid = one_call(api)[0]["id"]
        expired = issue_token(secret, Principal(T, "staff", "staff1", None), ttl_seconds=-5)
        for send, why in [({"type": "hello"}, "Expected an auth message"), ({"type": "auth"}, "Expected an auth message"), ("not json", "Expected an auth message"),
                          ({"type": "auth", "token": "garbage"}, "invalid or expired"), ({"type": "auth", "token": expired}, "invalid or expired"),
                          ({"type": "auth", "token": tok(api, "asha")}, "staff access required")]:
            m = first_message(api, cid, send)
            assert m["type"] == "error" and m["fatal"] and why in m["detail"], (send, m)
        with api.websocket_connect("/api/voice/ws") as _:     # none of that touched the call
            pass
        assert api.get(f"/api/staff/calls/{cid}", headers=auth(api, "staff1")).json()["listeners"] == 0


def test_the_staff_socket_checks_origin_before_the_handshake(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    with api.websocket_connect("/api/voice/ws") as caller:
        start(caller)
        cid = one_call(api)[0]["id"]
        with pytest.raises(WebSocketDisconnect):
            with staff_stream(api, cid, None, headers={"origin": "https://evil.example"}):
                pass
        with staff_stream(api, cid, None, headers={"origin": "http://localhost:3000"}) as ws:
            assert open_stream(api, ws, tok(api, "staff1")).joined["call_id"] == cid


def test_a_staff_socket_for_an_unknown_or_ended_call_is_refused(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    t = tok(api, "staff1")
    assert "not found" in first_message(api, "c_nope", {"type": "auth", "token": t})["detail"]
    with api.websocket_connect("/api/voice/ws") as caller:
        start(caller)
        cid = one_call(api)[0]["id"]
    deadline = time.monotonic() + 3                                         # wait for the end to be recorded
    while api.get(f"/api/staff/calls/{cid}", headers=auth(api, "staff1")).json()["status"] != "ended" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "not found or already ended" in first_message(api, cid, {"type": "auth", "token": t})["detail"]


def test_the_staff_socket_closes_when_its_token_expires_and_the_abandoned_takeover_ends_the_call_never_returning_it_to_the_ai(api):  # noqa: F811
    api.app.state.settings.calls_takeover_grace_s = 1
    setup_parts(api, [QUESTION])
    short = issue_token(api.app.state.secret, Principal(T, "staff", "staff1", None), ttl_seconds=4)
    with api.websocket_connect("/api/voice/ws") as caller:
        start(caller)
        cp = Pump(caller)
        cid = one_call(api)[0]["id"]
        with staff_stream(api, cid, None) as ws:
            p = open_stream(api, ws, short)
            time.sleep(0.3)
            assert api.post(f"/api/staff/calls/{cid}/takeover", headers={"Authorization": f"Bearer {tok(api, 'staff1')}"}).status_code == 200
            p.json_of("ownership", owner="staff")
            err = p.json_of("error", timeout=8)
            assert err["detail"] == "session expired" and err["fatal"] is True
            p.until(lambda k, x: k == "closed")
        end = cp.json_of("call_end", timeout=6)                                  # staff gone and not back within the grace period: the call ends, the AI does NOT resume
        assert end["reason"] == "staff_unreachable"
        time.sleep(0.5)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and api.get(f"/api/staff/calls/{cid}", headers=auth(api, "staff1")).json()["status"] != "failed":
            time.sleep(0.05)
        v = api.get(f"/api/staff/calls/{cid}", headers=auth(api, "staff1")).json()
        assert (v["status"], v["end_reason"], v["category"], v["intervention_by"]) == ("failed", "staff_unreachable", "ended_after_intervention", "staff1")
        assert v["listeners"] == 0
        assert not any(k == "json" and x.get("type") in ("reply", "thinking") for k, x in cp.log)      # the assistant never spoke again


def test_the_expiry_watcher_also_closes_for_a_disabled_account(api):  # noqa: F811
    import asyncio

    from app.api.deps import principal_and_expiry
    from app.api.routes.calls import _watch
    from app.calls.hub import StaffConn

    token = tok(api, "staff1")
    principal, exp = principal_and_expiry(api.app.state.secret, token)

    async def run(conn):
        await asyncio.wait_for(_watch(conn, api.app.state.secret, token, exp, 0), 3)

    healthy = StaffConn(principal, 10)
    with pytest.raises(asyncio.TimeoutError):                       # an enabled account keeps its socket (the watcher keeps polling)
        asyncio.run(asyncio.wait_for(_watch(healthy, api.app.state.secret, token, exp, 0), 0.4))
    assert not healthy.out._d
    with session_scope() as db:
        db.get(Account, (T, "staff1")).disabled = True
    conn = StaffConn(principal, 10)
    asyncio.run(run(conn))
    assert [conn.out._d.popleft() for _ in range(2)] == [("json", {"type": "error", "detail": "session ended", "fatal": True}), ("close", "auth_expired")]


# ---- the full sequence over real sockets -----------------------------------------------------------------------------------------
def test_the_full_call_sequence_listen_takeover_announcement_staff_speaks_end(api, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    setup_parts(api, [QUESTION])
    s1 = auth(api, "staff1")
    t1 = tok(api, "staff1")
    with api.websocket_connect("/api/voice/ws") as cws:
        ready = start(cws, token=tok(api, "asha"))
        caller = Pump(cws)
        # 1. the caller talks to the AI and gets an answer
        speak(cws)
        reply1 = caller.json_of("reply")
        caller.json_of("audio_end")
        assert reply1["status"] == "answered" and reply1["citations"]
        # 2. staff sees the active call (AI owns it)
        [c] = api.get("/api/staff/calls", headers=s1).json()
        cid = c["id"]
        assert (c["status"], c["owner"]) == ("active_ai", "ai")
        # 3. staff listens (REST, then the audio socket)
        assert api.post(f"/api/staff/calls/{cid}/listen", headers=s1).status_code == 200
        with staff_stream(api, cid, t1) as sws:
            staff = open_stream(api, sws, t1)
            assert staff.joined["owner"] == "ai" and staff.joined["you_own"] is False
            assert api.get(f"/api/staff/calls/{cid}", headers=s1).json()["listening"] is True
            # 4. staff hears the caller and the AI (same session: the ticket id is unchanged)
            speak(cws)
            reply2 = caller.json_of("reply")
            caller.json_of("audio_end")
            assert reply2["ticket_id"] == reply1["ticket_id"] == ready["ticket_id"]
            staff.json_of("ai_audio_end")
            tags = {p[0] for k, p in staff.log if k == "bytes"}
            assert {1, 2} <= tags
            texts = [p for k, p in staff.log if k == "json" and p.get("type") == "transcript"]
            assert {t["speaker"] for t in texts} == {"caller", "ai"} and any(QUESTION in t["text"] for t in texts)
            # 5. staff takes over: both sides are told, and the epoch moves (let the finished turn's own writes settle first: the SQLite test DB is one shared connection)
            caller.quiet(0.4)
            r = api.post(f"/api/staff/calls/{cid}/takeover", headers=s1).json()
            own = staff.json_of("ownership", owner="staff")
            assert own["staff"] == "staff1" and own["epoch"] == r["epoch"]
            caller.json_of("ownership", owner="staff")
            # 6. the caller hears the hand-over announcement first (it is a notice, not an AI reply) and it finishes before anything else
            caller.json_of("audio_start", notice="handover")
            staff.json_of("announcement", state="finished", timeout=10)
            caller.json_of("audio_end", notice="handover")
            caller.quiet(0.4)
            staff.quiet(0.1)
            # 7. the AI is silent from here on: the caller talks, nobody answers, but staff hear the caller
            speak(cws)
            got = caller.quiet(1.0)
            assert not [x for x in got if x[0] == "bytes" or (x[0] == "json" and x[1]["type"] in ("thinking", "reply", "audio_start", "transcript", "speech_start"))]
            heard = staff.quiet(0.3)
            assert any(k == "bytes" and p[0] == 1 for k, p in heard)
            # 8. staff speak and the caller hears them as a staff response with a higher turn id
            sws.send_json({"type": "mic_open", "epoch": own["epoch"]})
            assert staff.json_of("mic")["state"] == "open"
            pcm = b"\x10\x00" * 640
            for _ in range(5):
                sws.send_bytes(own["epoch"].to_bytes(4, "little") + pcm)
            start_msg = caller.json_of("audio_start", staff=True)
            assert start_msg["sample_rate"] == 16000 and start_msg["turn_id"] > reply2["turn_id"]
            frames = []
            while len(frames) < 5:
                k, p = caller.until(lambda k, p: k == "bytes")
                frames.append(p)
            assert [int.from_bytes(f[4:8], "little") for f in frames] == [0, 1, 2, 3, 4] and {int.from_bytes(f[:4], "little") for f in frames} == {start_msg["turn_id"]}
            assert all(f[8:] == pcm for f in frames)
            # stale epoch frames never reach the caller
            sws.send_bytes((own["epoch"] - 1).to_bytes(4, "little") + pcm)
            assert not [x for x in caller.quiet(0.4) if x[0] == "bytes"]
            # 9. nobody can give the call back to the AI, and the old/other microphones are dead
            assert api.post(f"/api/staff/calls/{cid}/return", headers=s1).status_code in (404, 405)
            sws.send_json({"type": "return"})                                        # a hand-made socket message is ignored
            sws.send_json({"type": "ownership", "owner": "ai", "epoch": own["epoch"] + 1})
            assert not [x for x in caller.quiet(0.4) if x[0] == "json" and x[1].get("type") == "ownership"]
            assert api.get(f"/api/staff/calls/{cid}", headers=s1).json()["owner"] == "staff"
            # 10. the caller talks again: still nobody from the AI
            speak(cws)
            assert not [x for x in caller.quiet(0.8) if x[0] == "bytes" or (x[0] == "json" and x[1].get("type") in ("reply", "thinking"))]
            # 11. staff ends the call: caller and staff sockets are closed
            e = api.post(f"/api/staff/calls/{cid}/end", headers=s1).json()
            assert e["status"] == "ended"
            staff.json_of("call_ended")
            staff.until(lambda k, p: k == "closed")
        caller.json_of("call_end")
        caller.until(lambda k, p: k == "closed")
    # 11. the whole story is in the audit trail, and nothing sensitive is in the logs
    ev = [x["kind"] for x in api.get(f"/api/staff/calls/{cid}/events", headers=s1).json()]
    assert [k for k in ev if not k.startswith(("announcement", "staff_mic"))] == ["created", "connected", "listen_started", "takeover_requested", "takeover_succeeded", "ended"]
    assert {"announcement_started", "announcement_finished", "staff_mic_started"} <= set(ev)
    logs = caplog.text
    assert QUESTION not in logs and t1 not in logs and "Bearer" not in logs and "instalment" not in logs.lower()


def test_when_the_owners_socket_drops_the_staff_audio_is_closed_for_the_caller_and_the_call_ends_after_the_grace_period(api):  # noqa: F811
    api.app.state.settings.calls_takeover_grace_s = 1
    setup_parts(api, [QUESTION])
    s1, t1 = auth(api, "staff1"), tok(api, "staff1")
    with api.websocket_connect("/api/voice/ws") as cws:
        start(cws)
        caller = Pump(cws)
        cid = one_call(api)[0]["id"]
        with staff_stream(api, cid, t1) as sws:
            staff = open_stream(api, sws, t1)
            ep = api.post(f"/api/staff/calls/{cid}/takeover", headers=s1).json()["epoch"]
            staff.json_of("ownership", owner="staff")
            staff.json_of("announcement", state="finished", timeout=10)
            sws.send_json({"type": "mic_open", "epoch": ep})
            staff.json_of("mic")
        caller.json_of("audio_end", staff=True)                              # the staff response is closed for the caller when the socket drops
        assert caller.json_of("call_end", timeout=6)["reason"] == "staff_unreachable"
        time.sleep(0.3)
        assert not any(k == "json" and x.get("type") == "reply" for k, x in caller.log)


# ---- compatibility ---------------------------------------------------------------------------------------------------------------
def test_the_voice_line_still_works_when_the_calls_tables_are_missing(api, caplog):  # noqa: F811
    from sqlalchemy import text

    with session_scope() as db:
        for t in ("call_recordings", "call_consents", "feedback_reviews", "intervention_feedback", "call_events", "calls", "trial_events", "trial_settings"):
            db.execute(text(f"DROP TABLE {t}"))
    setup_parts(api, [QUESTION])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        speak(ws)
        msgs = []
        while True:
            m = ws.receive()
            if m.get("bytes") is not None:
                continue
            d = json.loads(m["text"])
            msgs.append(d)
            if d["type"] == "audio_end":
                break
    assert any(m["type"] == "reply" and m["status"] == "answered" for m in msgs)
    assert "run `make db-migrate`" in caplog.text


def test_startup_marks_calls_left_live_by_a_dead_process_as_failed(api):  # noqa: F811
    from fastapi.testclient import TestClient

    with session_scope() as db:
        db.add(Call(tenant_id=T, id="c_leftover", ticket_id="a-x.y", status="staff_takeover", owner="staff", owner_subject="staff1", epoch=3))
    with TestClient(api.app):                                                  # a restart: the lifespan runs again
        pass
    with session_scope() as db:
        c = db.get(Call, (T, "c_leftover"))
        assert (c.status, c.end_reason, c.owner) == ("failed", "server_restart", "none")
    assert events("c_leftover")[-1][0] == "failed"


def test_the_students_own_text_chat_and_staff_console_are_unaffected(api):  # noqa: F811
    r = api.post("/api/chat", json={"text": QUESTION, "tenant": T})
    assert r.status_code == 200 and r.json()["status"] == "answered"
    assert api.get("/api/staff/escalations", headers=auth(api, "staff1")).status_code == 200


def test_audio_never_crosses_between_two_calls_of_the_same_tenant(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    s1, t1 = auth(api, "staff1"), tok(api, "staff1")
    with api.websocket_connect("/api/voice/ws") as ws_a, api.websocket_connect("/api/voice/ws") as ws_b:
        ref_a, ref_b = (start(w)["ticket_id"].split(".")[0][:10] for w in (ws_a, ws_b))
        a, b = Pump(ws_a), Pump(ws_b)
        by_ref = {c["ticket_ref"]: c["id"] for c in api.get("/api/staff/calls", headers=s1).json()}
        id_a, id_b = by_ref[ref_a], by_ref[ref_b]
        assert id_a != id_b
        with staff_stream(api, id_a, t1) as sws:
            staff = open_stream(api, sws, t1)
            time.sleep(0.4)                                                          # SQLite test DB = one shared connection: let background writes settle
            ep = api.post(f"/api/staff/calls/{id_a}/takeover", headers=s1).json()["epoch"]
            staff.json_of("ownership", owner="staff")
            a.json_of("ownership", owner="staff")
            staff.json_of("announcement", state="finished", timeout=10)
            sws.send_json({"type": "mic_open", "epoch": ep})
            staff.json_of("mic")
            speak(ws_b)                                                             # the OTHER caller talks: the staff member attached to call A hears nothing of it
            for _ in range(5):
                sws.send_bytes(ep.to_bytes(4, "little") + b"\x10\x00" * 640)
            a.json_of("audio_start", staff=True)
            got_b = b.quiet(0.8)
            assert not any(k == "json" and p.get("type") in ("ownership", "audio_start") and p.get("staff") for k, p in got_b)   # call B never heard the staff member
            got_staff = staff.quiet(0.3)
            caller_audio_heard = sum(1 for k, p in got_staff if k == "bytes" and p[0] == 1)
            assert caller_audio_heard == 0                                          # and the staff member never heard call B's caller
            assert api.get(f"/api/staff/calls/{id_b}", headers=s1).json()["owner"] == "ai"
