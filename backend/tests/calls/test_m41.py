"""M4.1 over the real HTTP/WebSocket API: the complete transcript, the four status categories, the post-call intervention feedback, exclusive control by the
intervention owner (including simultaneous requests), and the fact that nothing in the feedback flow touches the knowledge base."""

import threading
import time

import pytest
from sqlalchemy import func, select

from app.db.models import CallEvent, Chunk, Document, FeedbackReview, InterventionFeedback
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.api.test_staff_admin import other_headers
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.calls.test_calls_api import QUESTION, Pump, one_call, open_stream, speak, staff2, staff_stream, tok  # noqa: F401
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401
from tests.voice.test_voice_ws import setup_parts, start

FB = dict(category="incorrect_information", what_was_wrong="The assistant quoted the wrong instalment rule.", suggested_correction="Fees can be paid in two instalments.")


def run_call(api, *, ask=True, take: str | None = "staff1", end: str | None = "staff1", listen_only: str | None = None):
    """One call over the real sockets. `take` takes it over (waiting for the announcement to finish), `end` ends it (as that account); returns its id."""
    setup_parts(api, [QUESTION])
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws, token=tok(api, "asha"))
        caller = Pump(ws)
        if ask:
            speak(ws)
            caller.json_of("reply")
            caller.json_of("audio_end")
        cid = one_call(api)[0]["id"]
        if listen_only:
            assert api.post(f"/api/staff/calls/{cid}/listen", headers=auth(api, listen_only)).status_code == 200
        if take:
            r = api.post(f"/api/staff/calls/{cid}/takeover", headers=auth(api, take) if take != "staff2" else staff2(api))
            assert r.status_code == 200, r.text
            caller.json_of("audio_end", notice="handover", timeout=10)
            time.sleep(0.4)
        if end:
            h = staff2(api) if end == "staff2" else auth(api, end)
            assert api.post(f"/api/staff/calls/{cid}/end", headers=h).status_code == 200
            caller.json_of("call_end")
        else:
            time.sleep(0.2)
    time.sleep(0.2)
    return cid


# ---- transcript ------------------------------------------------------------------------------------------------------------------
def test_the_transcript_is_complete_chronological_and_marks_system_lines_apart_from_the_ai(api):  # noqa: F811
    cid = run_call(api)
    h = auth(api, "staff1")
    t = api.get(f"/api/staff/calls/{cid}/transcript", headers=h)
    assert t.status_code == 200
    body = t.json()
    es = body["entries"]
    assert body["call_id"] == cid and body["complete"] is True
    assert [e["speaker"] for e in es if e["speaker"] in ("customer", "ai")][:2] == ["customer", "ai"]
    assert any(QUESTION in (e["text"] or "") for e in es if e["speaker"] == "customer")
    codes = [e.get("code") for e in es if e["speaker"] == "system"]
    assert codes.index("takeover") < codes.index("announcement") < codes.index("announcement_done") < codes.index("ended")
    ats = [e["at"] for e in es if e.get("at")]
    assert ats == sorted(ats)                                                    # chronological
    ann = api.app.state.settings.calls_handover_announcement
    assert ann.startswith("I want to make sure you receive accurate information.")
    assert [e for e in es if e["speaker"] == "system" and e.get("code") == "announcement"][0]["text"] == ann
    assert not [e for e in es if e["speaker"] == "ai" and ann in (e["text"] or "")]      # the announcement is a system entry, never an AI reply
    assert any("not transcribed" in n for n in body["notes"])                  # staff speech is honestly reported as not transcribed
    assert all(e["text"] is None for e in es if e.get("kind") == "speech")           # nothing is invented for it


def test_who_may_read_a_transcript(api):  # noqa: F811
    cid = run_call(api)
    url = f"/api/staff/calls/{cid}/transcript"
    assert api.get(url).status_code == 401
    assert api.get(url, headers=auth(api, "asha")).status_code == 403
    for h in (auth(api, "staff1"), staff2(api), auth(api, "admin1")):
        assert api.get(url, headers=h).status_code == 200
    assert api.get(url, headers=other_headers(api, "otherstaff", "staff")).status_code == 404         # another tenant: same answer as "no such call"
    assert api.get(url, headers=other_headers(api, "otheradmin", "admin")).status_code == 404
    assert api.get("/api/staff/calls/c_does_not_exist/transcript", headers=auth(api, "staff1")).status_code == 404
    with session_scope() as db:
        views = db.scalars(select(CallEvent).where(CallEvent.call_id == cid, CallEvent.kind == "transcript_viewed")).all()
    assert sorted(v.actor for v in views) == ["admin1", "staff1", "staff2"]          # every reader audited once
    api.get(url, headers=auth(api, "staff1"))
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(CallEvent).where(CallEvent.call_id == cid, CallEvent.kind == "transcript_viewed")) == 3   # repeats are not re-logged


def test_a_call_with_no_conversation_has_an_empty_but_honest_transcript(api):  # noqa: F811
    cid = run_call(api, ask=False, take=None, end="staff1")
    es = api.get(f"/api/staff/calls/{cid}/transcript", headers=auth(api, "staff1")).json()["entries"]
    assert [e["speaker"] for e in es if e["speaker"] != "system"] == []
    assert es[-1]["code"] == "ended"


# ---- the four status categories --------------------------------------------------------------------------------------------------
def test_the_status_category_distinguishes_listening_from_taking_over(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    s1 = auth(api, "staff1")
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws, token=tok(api, "asha"))
        caller = Pump(ws)
        cid = one_call(api)[0]["id"]
        get = lambda: api.get(f"/api/staff/calls/{cid}", headers=s1).json()      # noqa: E731
        assert get()["category"] == "ai_handling"
        with staff_stream(api, cid, None) as sws:
            open_stream(api, sws, tok(api, "staff1"))
            v = get()
            assert (v["category"], v["listening"], v["intervened"], v["owner"]) == ("ai_handling", True, False, "ai")      # listening is NOT taking over
        assert api.post(f"/api/staff/calls/{cid}/end", headers=s1).status_code == 200
        caller.json_of("call_end")
    v = get()
    assert (v["category"], v["status"], v["intervened"], v["intervention_by"]) == ("ended_normally", "ended", False, None)
    assert "feedback" not in v



def test_a_call_that_was_taken_over_and_ended_is_ended_after_intervention(api):  # noqa: F811
    cid = run_call(api, ask=False)
    v = api.get(f"/api/staff/calls/{cid}", headers=auth(api, "staff1")).json()
    assert (v["category"], v["intervened"], v["intervention_by"], v["status"]) == ("ended_after_intervention", True, "staff1", "ended")


# ---- feedback ----------------------------------------------------------------------------------------------------------------------
def test_a_normal_call_has_no_feedback_form_and_no_intervention_record(api):  # noqa: F811
    cid = run_call(api, take=None, end="staff1")
    s1 = auth(api, "staff1")
    assert api.get(f"/api/staff/calls/{cid}/feedback", headers=s1).status_code == 404
    assert api.post(f"/api/staff/calls/{cid}/feedback", headers=s1, json=FB).status_code == 404
    assert api.post(f"/api/staff/calls/{cid}/feedback/skip", headers=s1).status_code == 404
    assert api.get("/api/staff/interventions", headers=s1).json()["items"] == []


def test_the_feedback_form_belongs_to_the_intervention_owner_alone(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    s1, s2, adm = auth(api, "staff1"), staff2(api), auth(api, "admin1")
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws, token=tok(api, "asha"))
        caller = Pump(ws)
        cid = one_call(api)[0]["id"]
        assert api.post(f"/api/staff/calls/{cid}/takeover", headers=s1).status_code == 200
        caller.json_of("audio_end", notice="handover", timeout=10)
        time.sleep(0.4)
        fb = f"/api/staff/calls/{cid}/feedback"
        assert api.post(fb, headers=s1, json=FB).status_code == 409                    # the form opens only after the call has ended
        assert api.get(fb, headers=s1).json()["form_open"] is False
        assert api.post(f"/api/staff/calls/{cid}/end", headers=s1).status_code == 200
        caller.json_of("call_end")
    time.sleep(0.2)
    v = api.get(f"/api/staff/calls/{cid}", headers=s1).json()
    assert v["feedback"] == {"state": "awaiting", "form_open": True, "review_status": None}
    for h in (s2, adm):                                                                  # another staff member and the admin: no read, no write, no skip
        assert api.get(fb, headers=h).status_code == (200 if h is adm else 403)      # an admin may READ the record (to review it), never write it
        assert api.post(fb, headers=h, json=FB).status_code == 403
        assert api.put(fb, headers=h, json=FB).status_code == 403
        assert api.post(fb + "/skip", headers=h).status_code == 403
        assert "feedback" not in api.get(f"/api/staff/calls/{cid}", headers=h).json()
    assert api.get("/api/staff/interventions", headers=s2).json()["items"] == []
    assert api.post(fb, headers=other_headers(api, "otherstaff", "staff"), json=FB).status_code == 404
    with session_scope() as db:
        row = db.get(InterventionFeedback, (T, cid))
        assert (row.state, row.staff_subject, row.what_was_wrong) == ("awaiting", "staff1", None)      # nothing was written by the refused attempts


def test_feedback_validation_submit_duplicate_edit_and_skip(api):  # noqa: F811
    cid = run_call(api, ask=False)
    s1 = auth(api, "staff1")
    fb = f"/api/staff/calls/{cid}/feedback"
    assert api.post(fb, headers=s1, json={**FB, "category": "nonsense"}).status_code == 422
    assert api.post(fb, headers=s1, json={**FB, "what_was_wrong": "   "}).status_code == 422
    assert api.post(fb, headers=s1, json={**FB, "what_was_wrong": "bad"}).status_code == 422
    assert api.post(fb, headers=s1, json={"category": "other"}).status_code == 422
    assert api.post(fb, headers=s1, json={**FB, "what_was_wrong": "x" * 4001}).status_code == 422
    ok = api.post(fb, headers=s1, json=FB)
    assert ok.status_code == 201
    b = ok.json()
    assert (b["state"], b["review_status"], b["category"], b["editable"], b["form_open"]) == ("submitted", "pending_review", "incorrect_information", True, False)
    assert b["what_was_wrong"] == FB["what_was_wrong"] and b["suggested_correction"] == FB["suggested_correction"] and b["submitted_at"]
    assert api.post(fb, headers=s1, json=FB).status_code == 409                              # no duplicate submissions
    e = api.put(fb, headers=s1, json={**FB, "category": "outdated_knowledge", "what_was_wrong": "The fee table is out of date."})
    assert (e.status_code, e.json()["category"], e.json()["review_status"]) == (200, "outdated_knowledge", "pending_review")
    assert api.post(fb + "/skip", headers=s1).status_code == 409                             # a submitted record cannot be skipped
    mine = api.get("/api/staff/interventions", headers=s1).json()
    assert [i["state"] for i in mine["items"]] == ["submitted"] and "other" in mine["categories"]
    ev = [x["kind"] for x in api.get(f"/api/staff/calls/{cid}/events", headers=s1).json()]
    assert ev.count("feedback_submitted") == 1 and ev.count("feedback_edited") == 1
    for x in api.get(f"/api/staff/calls/{cid}/events", headers=s1).json():          # the audit trail carries the category, never the free text
        assert FB["what_was_wrong"] not in str(x) and "outdated" not in x["detail"].get("what", "")


def test_skipping_is_recorded_is_idempotent_and_can_still_be_followed_by_a_submission(api):  # noqa: F811
    cid = run_call(api, ask=False)
    s1 = auth(api, "staff1")
    fb = f"/api/staff/calls/{cid}/feedback"
    a = api.post(fb + "/skip", headers=s1)
    b = api.post(fb + "/skip", headers=s1)
    assert (a.status_code, b.status_code, a.json()["state"], b.json()["state"]) == (200, 200, "skipped", "skipped")
    assert api.get(f"/api/staff/calls/{cid}", headers=s1).json()["feedback"]["state"] == "skipped"
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(CallEvent).where(CallEvent.call_id == cid, CallEvent.kind == "feedback_skipped")) == 1
        assert db.get(InterventionFeedback, (T, cid)).skipped_at is not None
    assert api.post(fb, headers=s1, json=FB).status_code == 201                              # changed their mind: allowed once, then it is a normal submission
    assert api.get(fb, headers=s1).json()["state"] == "submitted"


def test_admin_review_flow_never_touches_the_knowledge_base_and_locks_editing(api):  # noqa: F811
    cid = run_call(api, ask=False)
    s1, adm = auth(api, "staff1"), auth(api, "admin1")
    fb = f"/api/staff/calls/{cid}/feedback"
    review = lambda to, h=adm: api.post(f"/api/admin/trial/feedback/{cid}/review", headers=h, json={"status": to, "note": "checked"})   # noqa: E731
    assert review("reviewed").status_code == 409                                       # nothing submitted yet
    api.post(fb, headers=s1, json=FB)
    with session_scope() as db:
        kb_before = (db.scalar(select(func.count()).select_from(Document)), db.scalar(select(func.count()).select_from(Chunk)),
                     [(d.doc_id, d.content_hash if hasattr(d, "content_hash") else None) for d in db.scalars(select(Document)).all()])
    assert review("reviewed", s1).status_code == 403                                   # staff cannot review
    assert review("bogus").status_code in (409, 422)
    r = review("reviewed")
    assert r.status_code == 200 and r.json()["review_status"] == "reviewed" and r.json()["reviewed_by"] == "admin1"
    assert api.put(fb, headers=s1, json=FB).status_code == 409                          # reviewed: the owner can no longer edit it
    assert review("reviewed").status_code == 409                                       # not a valid move
    assert review("approved_for_update").json()["review_status"] == "approved_for_update"
    assert api.get(fb, headers=s1).json()["review_status"] == "approved_for_update"     # the owner sees the status
    assert review("rejected").json()["review_status"] == "rejected"
    assert review("approved_for_update").status_code == 409                            # rejected is final
    with session_scope() as db:
        assert (db.scalar(select(func.count()).select_from(Document)), db.scalar(select(func.count()).select_from(Chunk)),
                [(d.doc_id, d.content_hash if hasattr(d, "content_hash") else None) for d in db.scalars(select(Document)).all()]) == kb_before
        hist = [(h.from_status, h.to_status, h.reviewer) for h in db.scalars(select(FeedbackReview).where(FeedbackReview.call_id == cid).order_by(FeedbackReview.id)).all()]
    assert hist == [("pending_review", "reviewed", "admin1"), ("reviewed", "approved_for_update", "admin1"), ("approved_for_update", "rejected", "admin1")]
    queue_ = api.get("/api/admin/trial/feedback", headers=adm).json()
    assert [q["call_id"] for q in queue_["items"]] == [cid] and queue_["items"][0]["review_status"] == "rejected"
    assert api.get("/api/admin/trial/feedback", headers=s1).status_code == 403


def test_an_administrator_who_took_over_cannot_review_their_own_feedback(api):  # noqa: F811
    cid = run_call(api, ask=False, take="admin1", end="admin1")
    adm = auth(api, "admin1")
    assert api.post(f"/api/staff/calls/{cid}/feedback", headers=adm, json=FB).status_code == 201
    r = api.post(f"/api/admin/trial/feedback/{cid}/review", headers=adm, json={"status": "approved_for_update"})
    assert r.status_code == 403 and "another administrator" in r.json()["detail"]


# ---- exclusive control ----------------------------------------------------------------------------------------------------------
def test_simultaneous_takeover_requests_produce_exactly_one_owner_who_alone_keeps_control(api):  # noqa: F811
    setup_parts(api, [QUESTION])
    heads = {"staff1": auth(api, "staff1"), "staff2": staff2(api), "admin1": auth(api, "admin1")}
    with api.websocket_connect("/api/voice/ws") as ws:
        start(ws, token=tok(api, "asha"))
        caller = Pump(ws)
        cid = one_call(api)[0]["id"]
        out, gate = {}, threading.Barrier(3)

        def go(who):
            gate.wait()
            out[who] = api.post(f"/api/staff/calls/{cid}/takeover", headers=heads[who]).status_code

        ts = [threading.Thread(target=go, args=(w,)) for w in heads]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(out.values()) == [200, 409, 409], out
        winner = next(w for w, c in out.items() if c == 200)
        caller.json_of("audio_end", notice="handover", timeout=10)
        time.sleep(0.4)
        with session_scope() as db:
            c = db.get(__import__("app.db.models", fromlist=["Call"]).Call, (T, cid))
            assert (c.intervention_owner, c.owner_subject, c.status) == (winner, winner, "staff_takeover")
            assert db.get(InterventionFeedback, (T, cid)).staff_subject == winner
        for who, h in heads.items():                                                       # only the winner may end it
            if who != winner:
                assert api.post(f"/api/staff/calls/{cid}/end", headers=h).status_code == 403
        assert api.get(f"/api/staff/calls/{cid}", headers=heads[winner]).json()["status"] == "staff_takeover"
        assert api.post(f"/api/staff/calls/{cid}/end", headers=heads[winner]).status_code == 200
        caller.json_of("call_end")
    time.sleep(0.2)
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(InterventionFeedback).where(InterventionFeedback.call_id == cid)) == 1
