"""Chat -> pause -> staff decision -> student sees the outcome, all through HTTP."""

from tests.api.conftest import T, auth

MAKEUP = "Please arrange a make-up class for the session I missed in PYD-301."


def test_guest_question_answered_with_citations_and_signed_ticket(api):
    r = api.post("/api/chat", json={"text": "What is the refund policy?", "tenant": T})
    assert r.status_code == 200
    b = r.json()
    assert b["signed_in"] is False and b["ticket_id"].startswith("a-") and b["reply"]
    # the same ticket continues the conversation
    r2 = api.post("/api/chat", json={"text": "and for online courses?", "tenant": T, "ticket_id": b["ticket_id"]})
    assert r2.status_code == 200 and r2.json()["ticket_id"] == b["ticket_id"]
    h = api.get(f"/api/chat/{b['ticket_id']}", params={"tenant": T}).json()
    assert [m["role"] for m in h["messages"]][:2] == ["user", "assistant"]


def test_forged_or_foreign_ticket_ids_rejected(api):
    assert api.post("/api/chat", json={"text": "hi", "tenant": T, "ticket_id": "a-forged.0000"}).status_code == 404
    g = api.post("/api/chat", json={"text": "hello there", "tenant": T}).json()["ticket_id"]
    h = auth(api, "asha")
    assert api.post("/api/chat", json={"text": "hi", "ticket_id": g}, headers=h).status_code == 404  # guest id is not a student id


def test_student_cannot_use_another_students_ticket(api):
    ha, hm = auth(api, "asha"), auth(api, "meera")
    t = api.post("/api/chat", json={"text": "what batches do I have?"}, headers=ha).json()["ticket_id"]
    assert api.post("/api/chat", json={"text": "hi", "ticket_id": t}, headers=hm).status_code == 404
    assert api.get(f"/api/chat/{t}", headers=hm).status_code == 404
    assert api.get(f"/api/chat/{t}", headers=ha).status_code == 200


def test_staff_and_admins_use_the_customer_chat_as_visitors_of_their_own_tenant(api):
    """M6 add-on: staff and administrators may use the chat. They get a visitor's conversation in THEIR tenant (from the token): no student identity, so no student
    records; a student's conversation stays the student's; the tenant in the body is ignored for them."""
    for user in ("staff1", "admin1"):
        h = auth(api, user)
        r = api.post("/api/chat", json={"text": "what courses do you offer?", "tenant": "someone_else"}, headers=h)
        assert r.status_code == 200, (user, r.text)
        t = r.json()["ticket_id"]
        assert api.get(f"/api/chat/{t}", headers=h).status_code == 200
        assert api.get(f"/api/chat/{t}", headers=auth(api, "asha")).status_code == 404          # not a student's ticket
    student_ticket = api.post("/api/chat", json={"text": "what batches do I have?"}, headers=auth(api, "asha")).json()["ticket_id"]
    assert api.get(f"/api/chat/{student_ticket}", headers=auth(api, "staff1")).status_code == 404    # staff cannot open a student's conversation through the chat
    assert api.post("/api/chat", json={"text": "what is my attendance?", "ticket_id": student_ticket}, headers=auth(api, "admin1")).status_code == 404


def test_body_cannot_smuggle_identity(api):
    h = auth(api, "asha")
    r = api.post("/api/chat", json={"text": "hi", "student_id": "stu_meera"}, headers=h)
    assert r.status_code == 422


def test_chat_rate_limit(api):
    n = api.app.state.settings.chat_max_messages_per_minute
    h = auth(api, "asha")
    codes = [api.post("/api/chat", json={"text": "what are the fees?"}, headers=h).status_code for _ in range(n + 2)]
    assert 429 in codes and codes[0] == 200


def test_anonymous_cannot_reach_personal_data(api):
    r = api.post("/api/chat", json={"text": "show my attendance and fee balance", "tenant": T}).json()
    assert "stu_asha" not in r["reply"] and "Asha" not in r["reply"]


def test_makeup_request_pauses_then_staff_approves_and_student_sees_it(api):
    ha, hs = auth(api, "meera"), auth(api, "staff1")
    r = api.post("/api/chat", json={"text": MAKEUP}, headers=ha).json()
    t = r["ticket_id"]
    assert r["status"] in ("awaiting_staff", "awaiting_confirmation"), r
    if r["status"] == "awaiting_confirmation":  # the student confirms before it goes to staff
        r = api.post("/api/chat", json={"text": "yes, please submit it", "ticket_id": t}, headers=ha).json()
    assert r["status"] == "awaiting_staff", r
    pending = api.get("/api/staff/approvals", headers=hs).json()
    mine = [p for p in pending if p["ticket_id"] == t]
    assert len(mine) == 1 and mine[0]["student_id"] == "stu_meera"
    d = api.post(f"/api/staff/approvals/{mine[0]['request_id']}/decision", json={"approve": True}, headers=hs)
    assert d.status_code == 200, d.text
    assert not [p for p in api.get("/api/staff/approvals", headers=hs).json() if p["ticket_id"] == t]
    hist = api.get(f"/api/chat/{t}", headers=ha).json()
    assert hist["awaiting"] is None and hist["messages"][-1]["role"] == "assistant"
    # deciding twice is a conflict, not a second write
    assert api.post(f"/api/staff/approvals/{mine[0]['request_id']}/decision", json={"approve": True}, headers=hs).status_code in (404, 409)


def test_makeup_rejected_by_staff(api):
    ha, hs = auth(api, "meera"), auth(api, "staff1")
    r = api.post("/api/chat", json={"text": MAKEUP}, headers=ha).json()
    t = r["ticket_id"]
    if r["status"] == "awaiting_confirmation":
        api.post("/api/chat", json={"text": "yes, please submit it", "ticket_id": t}, headers=ha)
    rid = [p for p in api.get("/api/staff/approvals", headers=hs).json() if p["ticket_id"] == t][0]["request_id"]
    assert api.post(f"/api/staff/approvals/{rid}/decision", json={"approve": False}, headers=hs).status_code == 200
    assert api.get(f"/api/chat/{t}", headers=ha).json()["awaiting"] is None
