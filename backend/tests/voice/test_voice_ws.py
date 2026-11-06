"""Complete voice conversations over the real FastAPI app and the real support graph (offline fakes), through the WebSocket.
STT/TTS/VAD are the fakes (scripted transcript, tone audio, energy VAD): this proves the integration, identity, RBAC, escalation and approval
behaviour of the voice line, not recognition quality. Real models: `make voice-smoke` on your machine."""

import json

import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.db.models import Escalation, MakeupRequest
from app.db.session import session_scope
from app.voice import session as sess
from app.voice.stt import FakeStt
from app.voice.tts import FakeTts
from app.voice.vad import EnergyVad
from tests.api.conftest import T, api, auth, login  # noqa: F401
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401
from tests.voice.audio import chunks, utterance

OTHER = "other_tenant"


@pytest.fixture(autouse=True)
def roomy_budget(monkeypatch):
    monkeypatch.setattr(sess, "BURST_MS", 120_000)


def setup_parts(api, script):  # noqa: F811
    stt, tts = FakeStt(script), FakeTts()
    api.app.state.voice_parts = (lambda: EnergyVad(), stt, tts)
    return stt, tts


def start(ws, token=None, tenant=T, ticket=None):
    ws.send_json({"type": "start", "tenant": tenant, "token": token, "ticket_id": ticket})
    return json.loads(ws.receive_text())


def say(ws):
    """Send one spoken utterance; return (json messages, audio bytes received) up to the end of the bot's speech."""
    for c in chunks(utterance()):
        ws.send_bytes(c)
    msgs, audio = [], 0
    while True:
        m = ws.receive()
        if m.get("bytes") is not None:
            audio += len(m["bytes"])
            continue
        d = json.loads(m["text"])
        msgs.append(d)
        if d["type"] == "audio_end" or (d["type"] == "error" and d.get("fatal")):
            return msgs, audio


def reply_of(msgs):
    return next(m for m in msgs if m["type"] == "reply")


def token_of(api, user):  # noqa: F811
    return auth(api, user)["Authorization"].split()[1]


# ---- conversations ------------------------------------------------------------------------------------------------------------
def test_anonymous_faq_conversation_with_citation_and_audio(api):  # noqa: F811
    _, tts = setup_parts(api, ["Can I pay the course fee in instalments?"])
    with api.websocket_connect("/api/voice/ws") as ws:
        ready = start(ws)
        assert ready["type"] == "ready" and ready["signed_in"] is False and ready["ticket_id"] and ready["sample_rate"] == 22050
        msgs, audio = say(ws)
    kinds = [m["type"] for m in msgs]
    assert kinds[:4] == ["speech_start", "transcribing", "transcript", "thinking"]
    r = reply_of(msgs)
    assert r["status"] == "answered" and r["citations"] and audio > 0
    assert next(m for m in msgs if m["type"] == "transcript")["text"] == "Can I pay the course fee in instalments?"
    assert tts.spoken and not any("[" in s for s in tts.spoken)  # source ids are never read aloud


def test_voice_and_text_share_one_conversation(api):  # noqa: F811
    setup_parts(api, ["Can I pay the course fee in instalments?"])
    with api.websocket_connect("/api/voice/ws") as ws:
        ticket = start(ws)["ticket_id"]
        say(ws)
    h = api.get(f"/api/chat/{ticket}", params={"tenant": T}).json()
    assert [m["role"] for m in h["messages"]] == ["user", "assistant"] and "instalment" in h["messages"][0]["content"].lower()
    r = api.post("/api/chat", json={"text": "How much is the first instalment?", "ticket_id": ticket, "tenant": T}).json()
    assert r["ticket_id"] == ticket                      # the same ticket continues in text
    with api.websocket_connect("/api/voice/ws") as ws:   # ... and back in voice
        assert start(ws, ticket=ticket)["ticket_id"] == ticket


def test_asking_for_a_person_escalates_and_the_reference_is_spoken_in_full(api):  # noqa: F811
    stt, tts = setup_parts(api, ["I want to talk to a person, not a bot."])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        msgs, audio = say(ws)
    r = reply_of(msgs)
    assert r["status"] == "escalated" and r["escalation_id"]
    with session_scope() as db:
        (esc,) = db.scalars(select(Escalation)).all()
    assert esc.id == r["escalation_id"] and esc.reason == "human_request"
    assert any(" ".join(esc.id.split("-")[0]) in s for s in tts.spoken)  # "E S C dash ..." was what the caller heard


def test_anonymous_caller_cannot_get_own_records_by_voice(api):  # noqa: F811
    setup_parts(api, ["What is my attendance in PYF-101?"])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        msgs, _ = say(ws)
    r = reply_of(msgs)
    assert r["status"] == "needs_detail" and "sign in" in r["reply"].lower() and "%" not in r["reply"]


def test_signed_in_student_gets_own_records_but_not_a_friends(api):  # noqa: F811
    setup_parts(api, ["What is my attendance in PYF-101?", "What is the attendance of my friend Rahul?"])
    with api.websocket_connect("/api/voice/ws") as ws:
        ready = start(ws, token=token_of(api, "asha"))
        assert ready["signed_in"] is True
        own, _ = say(ws)
        other, _ = say(ws)
    assert "%" in reply_of(own)["reply"] or "attendance" in reply_of(own)["reply"].lower()
    assert "rahul" not in reply_of(other)["reply"].lower() or "can't" in reply_of(other)["reply"].lower()


def test_makeup_request_by_voice_pauses_for_staff_and_resumes(api):  # noqa: F811
    setup_parts(api, ["Please arrange a make-up class for the session I missed in PYD-301."])
    with api.websocket_connect("/api/voice/ws") as ws:
        ticket = start(ws, token=token_of(api, "meera"))["ticket_id"]
        msgs, _ = say(ws)
    assert reply_of(msgs)["status"] == "awaiting_staff"
    with session_scope() as db:
        (req,) = db.scalars(select(MakeupRequest)).all()
        assert (req.status, req.student_id) == ("pending", "stu_meera")
    staff = auth(api, "staff1")
    pending = api.get("/api/staff/approvals", headers=staff).json()
    assert len(pending) == 1
    d = api.post(f"/api/staff/approvals/{pending[0]['request_id']}/decision", json={"approve": True}, headers=staff)
    assert d.status_code == 200
    h = api.get(f"/api/chat/{ticket}", headers=auth(api, "meera")).json()
    assert h["awaiting"] is None and "approved" in h["messages"][-1]["content"].lower()


def test_typed_text_inside_a_call(api):  # noqa: F811
    setup_parts(api, ["unused"])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        ws.send_json({"type": "text", "text": "Which programmes do you offer under PySpiders?"})
        msgs = []
        while True:
            m = ws.receive()
            if m.get("text") is None:
                continue
            d = json.loads(m["text"])
            msgs.append(d)
            if d["type"] == "audio_end":
                break
    assert reply_of(msgs)["status"] in ("answered", "needs_detail")


def test_analytics_events_are_recorded_for_voice_turns(api):  # noqa: F811
    from app.db.models import ChatEvent

    setup_parts(api, ["Can I pay the course fee in instalments?"])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        say(ws)
    with session_scope() as db:
        assert len(db.scalars(select(ChatEvent)).all()) == 1


# ---- identity and abuse ------------------------------------------------------------------------------------------------------
def fatal(ws):
    m = json.loads(ws.receive_text())
    assert m["type"] == "error" and m["fatal"]
    return m["detail"]


def test_bad_token_is_refused(api):  # noqa: F811
    setup_parts(api, ["x"])
    with api.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start", "tenant": T, "token": "not-a-token"})
        assert "invalid" in fatal(ws)


def test_staff_token_cannot_use_the_student_voice_line(api):  # noqa: F811
    setup_parts(api, ["x"])
    with api.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start", "tenant": T, "token": token_of(api, "staff1")})
        assert "students and visitors" in fatal(ws)


def test_disabled_account_token_stops_working(api):  # noqa: F811
    from app.db.models import Account

    setup_parts(api, ["x"])
    tok = token_of(api, "asha")
    with session_scope() as db:
        db.get(Account, (T, "asha")).disabled = True
    with api.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start", "tenant": T, "token": tok})
        assert "invalid" in fatal(ws)


def test_forged_or_foreign_ticket_is_refused(api):  # noqa: F811
    setup_parts(api, ["x"])
    asha_ticket = api.post("/api/chat", json={"text": "hello there", "tenant": T}, headers=auth(api, "asha")).json()["ticket_id"]
    with api.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start", "tenant": T, "token": token_of(api, "meera"), "ticket_id": asha_ticket})  # someone else's
        assert "unknown conversation" in fatal(ws)
    with api.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start", "tenant": T, "ticket_id": "anon-forged"})
        assert "unknown conversation" in fatal(ws)


def test_unknown_tenant_is_refused(api):  # noqa: F811
    setup_parts(api, ["x"])
    with api.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "start", "tenant": "no_such_institute"})
        assert "unknown institute" in fatal(ws)


def test_token_decides_the_tenant_not_the_request(api):  # noqa: F811
    setup_parts(api, ["x"])
    with api.websocket_connect("/api/voice/ws") as ws:
        ready = start(ws, token=token_of(api, "asha"), tenant="some_other_tenant")
    assert ready["type"] == "ready" and ready["tenant_name"]  # served in the token's own tenant, the claimed one was ignored


def test_first_message_must_be_start(api):  # noqa: F811
    setup_parts(api, ["x"])
    with api.websocket_connect("/api/voice/ws") as ws:
        ws.send_json({"type": "text", "text": "hi"})
        assert "start" in fatal(ws)


def test_foreign_browser_origin_is_refused_before_the_handshake(api):  # noqa: F811
    setup_parts(api, ["x"])
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect("/api/voice/ws", headers={"origin": "https://evil.example"}):
            pass


def test_allowed_browser_origin_works(api):  # noqa: F811
    setup_parts(api, ["x"])
    with api.websocket_connect("/api/voice/ws", headers={"origin": "http://localhost:3000"}) as ws:
        assert start(ws)["type"] == "ready"


def test_voice_can_be_switched_off(api):  # noqa: F811
    api.app.state.settings = api.app.state.settings.model_copy(update={"voice_enabled": False})
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect("/api/voice/ws"):
            pass
    assert api.get("/api/voice/status").json()["enabled"] is False


def test_voice_shares_the_text_chat_rate_limit(api):  # noqa: F811
    from app.api.ratelimit import SlidingWindow

    setup_parts(api, ["Can I pay the course fee in instalments?"])
    api.app.state.chat_limiter = SlidingWindow(1, 60)
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        first, _ = say(ws)
        second, _ = say(ws)
    assert reply_of(first)["status"] == "answered" and "too fast" in reply_of(second)["reply"]


def test_status_endpoint_reports_fake_provider_ready(api):  # noqa: F811
    j = api.get("/api/voice/status").json()
    assert j["enabled"] and j["ready"] and j["provider"] == "fake"


def test_status_endpoint_explains_missing_real_models(api):  # noqa: F811
    api.app.state.settings = api.app.state.settings.model_copy(update={"voice_provider": "real", "voice_tts_voice": ""})
    j = api.get("/api/voice/status").json()
    assert j["enabled"] and not j["ready"] and any("SG_VOICE_TTS_VOICE" in p for p in j["problems"])


# ---- M2.1: natural conversation and call control, over the real graph --------------------------------------------------------------
def test_a_greeting_by_voice_is_answered_warmly_and_spoken(api):  # noqa: F811
    from app.graph.nodes.replies import CONVERSE

    _, tts = setup_parts(api, ["Hi."])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        msgs, audio = say(ws)
    r = reply_of(msgs)
    assert r["reply"] in CONVERSE["greeting"] and r["status"] == "answered" and not r["citations"] and audio > 0
    assert " ".join(tts.spoken) == r["reply"] and not r["end_call"]   # spoken in sentences


def test_goodbye_by_voice_speaks_the_closing_then_ends_the_call(api):  # noqa: F811
    from app.graph.nodes.replies import CONVERSE

    _, tts = setup_parts(api, ["Okay, thank you. That's it. You can stop the call."])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        msgs, audio = say(ws)                       # up to the end of the bot's speech
        r = reply_of(msgs)
        assert r["reply"] in CONVERSE["goodbye"] and r["end_call"] is True and not r["citations"] and audio > 0
        assert json.loads(ws.receive_text()) == {"type": "call_end", "reason": "goodbye"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()                       # the server hung up
    assert tts.spoken and "knowledge base" not in tts.spoken[0].lower()


def test_forward_my_call_to_support_escalates_instead_of_answering_from_the_faq(api):  # noqa: F811
    stt, tts = setup_parts(api, ["Please forward this call towards your supporting."])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        msgs, audio = say(ws)
    r = reply_of(msgs)
    assert r["status"] == "escalated" and r["escalation_id"] and not r["citations"] and audio > 0
    assert "missed" not in r["reply"].lower()
    assert "E S C dash" in " ".join(tts.spoken)                       # the reference is spelled out for the ear
    with session_scope() as db:
        (esc,) = db.scalars(select(Escalation)).all()
    assert esc.reason == "human_request" and esc.id == r["escalation_id"]


def test_interrupting_with_im_just_testing_is_acknowledged_and_the_call_continues(api):  # noqa: F811
    from app.graph.nodes.replies import CONVERSE

    setup_parts(api, ["I am just testing.", "Can I pay the course fee in instalments?"])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        msgs, _ = say(ws)
        r = reply_of(msgs)
        assert r["reply"] in CONVERSE["interruption"] and not r["end_call"] and r["status"] == "answered"
        msgs, _ = say(ws)                           # the same call carries on with a real question
        assert reply_of(msgs)["citations"]


def test_voice_and_text_give_the_same_replies_for_the_same_words(api):  # noqa: F811
    sentences = ["Hi.", "Thank you.", "I am just testing.", "I want to talk to a person.", "Can I pay the course fee in instalments?"]
    for text in sentences:
        setup_parts(api, [text])
        with api.websocket_connect("/api/voice/ws") as ws:
            start(ws)
            voice = reply_of(say(ws)[0])
        typed = api.post("/api/chat", json={"text": text, "tenant": T}).json()
        assert voice["status"] == typed["status"], text
        if typed["status"] == "escalated":
            assert voice["reply"].replace(voice["escalation_id"], "REF") == typed["reply"].replace(typed["escalation_id"], "REF")
        else:
            assert voice["reply"] == typed["reply"], text
            assert [c["id"] for c in voice["citations"]] == [c["id"] for c in typed["citations"]]


def test_text_chat_closes_politely_on_goodbye_and_nothing_ends(api):  # noqa: F811
    from app.graph.nodes.replies import CONVERSE

    r = api.post("/api/chat", json={"text": "okay thank you, that's it", "tenant": T})
    assert r.status_code == 200 and r.json()["reply"] in CONVERSE["goodbye"]
    again = api.post("/api/chat", json={"text": "Can I pay the course fee in instalments?", "ticket_id": r.json()["ticket_id"], "tenant": T}).json()
    assert again["citations"]                       # the chat simply continues


def test_goodbye_by_voice_while_staff_approval_is_pending_keeps_the_request(api):  # noqa: F811
    from app.graph.nodes.replies import GOODBYE_WHILE_PENDING

    setup_parts(api, ["Please arrange a make-up class for the session I missed in PYD-301.", "Okay thank you, that's it, you can stop the call."])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws, token=token_of(api, "meera"))
        assert reply_of(say(ws)[0])["status"] == "awaiting_staff"
        msgs, _ = say(ws)
        assert reply_of(msgs)["reply"] == GOODBYE_WHILE_PENDING and reply_of(msgs)["end_call"]
        assert json.loads(ws.receive_text())["type"] == "call_end"
    with session_scope() as db:
        (req,) = db.scalars(select(MakeupRequest)).all()
    assert req.status == "pending"
    assert len(api.get("/api/staff/approvals", headers=auth(api, "staff1")).json()) == 1


def test_friendly_words_by_voice_do_not_unlock_records_for_a_visitor(api):  # noqa: F811
    setup_parts(api, ["Hi, thanks. What is my attendance in PYF-101?"])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws)
        r = reply_of(say(ws)[0])
    assert r["status"] == "needs_detail" and "sign in" in r["reply"].lower() and "%" not in r["reply"]
