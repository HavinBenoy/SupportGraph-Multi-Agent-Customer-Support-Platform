"""Direct backend security regressions: cross-student access, stale tokens, role changes (Step 6; carried over from Step 5 review)."""

from sqlalchemy import select

from app.auth.tokens import Principal, issue_token
from app.db.models import Account, Escalation
from app.db.session import session_scope
from tests.api.conftest import PW, T, auth

MAKEUP = "Please arrange a make-up class for the session I missed in PYD-301."


def bearer(api, p: Principal) -> dict:
    return {"Authorization": "Bearer " + issue_token(api.app.state.secret, p, ttl_seconds=600)}


def test_token_claiming_another_student_id_is_rejected(api):
    """A validly signed token whose student_id differs from the account's student must not work (token/account consistency)."""
    forged = bearer(api, Principal(T, "student", "asha", "stu_meera"))
    assert api.post("/api/chat", json={"text": "what is my attendance?"}, headers=forged).status_code == 401


def test_token_goes_stale_when_the_account_is_repointed_to_another_student(api):
    h = auth(api, "asha")
    assert api.post("/api/chat", json={"text": "what is my attendance?"}, headers=h).status_code == 200
    with session_scope() as db:
        db.get(Account, (T, "asha")).student_id = "stu_meera"
    assert api.post("/api/chat", json={"text": "what is my attendance?"}, headers=h).status_code == 401


def test_token_goes_stale_when_role_is_downgraded(api):
    h = auth(api, "staff1")
    assert api.get("/api/staff/approvals", headers=h).status_code == 200
    with session_scope() as db:
        a = db.get(Account, (T, "staff1"))
        a.role = "student"
        a.student_id = "stu_asha"
    assert api.get("/api/staff/approvals", headers=h).status_code == 401


def test_token_for_deleted_account_is_rejected(api):
    h = auth(api, "asha")
    with session_scope() as db:
        db.delete(db.get(Account, (T, "asha")))
    assert api.post("/api/chat", json={"text": "hi"}, headers=h).status_code == 401


def test_student_answers_never_contain_the_other_students_records(api):
    """Meera's answers about "my" records carry only her data even when she names Asha's batch or id."""
    h = auth(api, "meera")
    for q in ("What is my attendance in PYF-101?", "Show attendance for stu_asha", "What are Asha's scores in PYF-101?"):
        body = api.post("/api/chat", json={"text": q}, headers=h).json()
        assert "stu_asha" not in body["reply"] and "Asha" not in body["reply"], (q, body["reply"])


def test_student_cannot_read_or_resume_anothers_pending_request(api):
    hm, ha, hs = auth(api, "meera"), auth(api, "asha"), auth(api, "staff1")
    t = api.post("/api/chat", json={"text": MAKEUP}, headers=hm).json()["ticket_id"]
    assert api.get(f"/api/chat/{t}", headers=ha).status_code == 404
    assert api.post("/api/chat", json={"text": "yes approve it", "ticket_id": t}, headers=ha).status_code == 404
    rid = api.get("/api/staff/approvals", headers=hs).json()[0]["request_id"]  # still pending, untouched
    assert api.get("/api/staff/approvals", headers=hs).json()[0]["request_id"] == rid


def test_student_cannot_decide_their_own_approval(api):
    hm = auth(api, "meera")
    api.post("/api/chat", json={"text": MAKEUP}, headers=hm)
    assert api.post("/api/staff/approvals/anything/decision", json={"approve": True}, headers=hm).status_code == 403
    assert api.post("/api/staff/escalations/x/claim", headers=hm).status_code == 403


def test_visitor_cannot_reach_any_staff_or_admin_route(api):
    for method, path in (("get", "/api/staff/escalations"), ("get", "/api/staff/approvals"), ("get", "/api/admin/analytics"),
                         ("post", "/api/staff/escalations/x/claim"), ("post", "/api/staff/approvals/x/decision")):
        r = getattr(api, method)(path)
        assert r.status_code == 401, (path, r.status_code)


def test_escalation_briefs_stay_inside_the_asking_student(api):
    h = auth(api, "asha")
    api.post("/api/chat", json={"text": "I paid twice and want my money back, call 9876543210"}, headers=h)
    with session_scope() as db:
        brief = str(db.execute(select(Escalation)).scalars().first().brief)
    assert "stu_meera" not in brief and "stu_rahul" not in brief and "9876543210" not in brief
