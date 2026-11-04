"""Staff console and admin analytics over HTTP: role gates, workflow, tenant isolation."""

from app.auth.accounts import create_account
from app.db.session import session_scope
from app.auth.tokens import Principal, issue_token
from tests.api.conftest import PW, T, auth

OTHER = "other_institute"
DISPUTE = "I paid the first instalment twice and want my money back"


def other_headers(api, user, role):
    """The second tenant has no config.yaml (so no login page): mint its token directly, as the login route would after authenticating."""
    with session_scope() as db:
        create_account(db, OTHER, user, PW, role)
    return {"Authorization": "Bearer " + issue_token(api.app.state.secret, Principal(OTHER, role, user, None), ttl_seconds=600)}


STAFF_ROUTES = ["/api/staff/escalations", "/api/staff/approvals"]


def escalate(api, user="asha"):
    r = api.post("/api/chat", json={"text": DISPUTE}, headers=auth(api, user)).json()
    assert r["status"] == "escalated" and r["escalation_id"], r
    return r


def test_staff_routes_require_staff_role(api):
    for path in STAFF_ROUTES + ["/api/admin/analytics"]:
        assert api.get(path).status_code == 401
    for path in STAFF_ROUTES:
        assert api.get(path, headers=auth(api, "asha")).status_code == 403
    assert api.get("/api/admin/analytics", headers=auth(api, "staff1")).status_code == 403
    assert api.get("/api/admin/analytics", headers=auth(api, "admin1")).status_code == 200
    assert api.get("/api/staff/escalations", headers=auth(api, "admin1")).status_code == 200  # admin has staff powers


def test_escalation_lifecycle_claim_then_resolve(api):
    r = escalate(api)
    hs = auth(api, "staff1")
    rows = api.get("/api/staff/escalations", headers=hs).json()
    assert [x["id"] for x in rows] == [r["escalation_id"]]
    d = api.get(f"/api/staff/escalations/{r['escalation_id']}", headers=hs).json()
    assert d["brief"]["student_id"] == "stu_asha" and d["team"] == "accounts"
    c = api.post(f"/api/staff/escalations/{r['escalation_id']}/claim", headers=hs)
    assert c.status_code == 200 and c.json()["claimed_by"] == "staff1"  # identity from the token, not the request
    assert api.post(f"/api/staff/escalations/{r['escalation_id']}/claim", headers=hs).status_code == 409
    assert api.get("/api/staff/escalations", headers=hs).json() == []  # default filter: open
    assert len(api.get("/api/staff/escalations", params={"status": "claimed"}, headers=hs).json()) == 1
    assert api.post(f"/api/staff/escalations/{r['escalation_id']}/resolve", json={"note": ""}, headers=hs).status_code == 422
    done = api.post(f"/api/staff/escalations/{r['escalation_id']}/resolve", json={"note": "Refunded duplicate."}, headers=hs)
    assert done.status_code == 200 and done.json()["status"] == "resolved"
    assert api.post(f"/api/staff/escalations/{r['escalation_id']}/resolve", json={"note": "again"}, headers=hs).status_code == 409


def test_other_staff_cannot_resolve_a_claimed_escalation(api):
    with session_scope() as db:
        create_account(db, T, "staff2", PW, "staff")
    eid = escalate(api)["escalation_id"]
    api.post(f"/api/staff/escalations/{eid}/claim", headers=auth(api, "staff1"))
    assert api.post(f"/api/staff/escalations/{eid}/resolve", json={"note": "mine now"}, headers=auth(api, "staff2")).status_code == 409


def test_unknown_escalation_is_404(api):
    assert api.get("/api/staff/escalations/nope", headers=auth(api, "staff1")).status_code == 404
    assert api.post("/api/staff/escalations/nope/claim", headers=auth(api, "staff1")).status_code == 404


def test_tenant_isolation_staff_of_another_tenant_sees_nothing(api):
    r = escalate(api)
    ho = other_headers(api, "other_staff", "staff")
    assert api.get("/api/staff/escalations", headers=ho).json() == []
    assert api.get(f"/api/staff/escalations/{r['escalation_id']}", headers=ho).status_code == 404
    assert api.post(f"/api/staff/escalations/{r['escalation_id']}/claim", headers=ho).status_code == 404
    assert api.get("/api/staff/approvals", headers=ho).json() == []
    # an account of one tenant cannot be used as another tenant's: same username, wrong tenant
    forged = {"Authorization": "Bearer " + issue_token(api.app.state.secret, Principal(OTHER, "staff", "staff1", None), ttl_seconds=600)}
    assert api.get("/api/staff/escalations", headers=forged).status_code == 401


def test_other_tenant_cannot_decide_our_approval(api):
    other = other_headers(api, "other_staff", "staff")
    api.post("/api/chat", json={"text": "Please arrange a make-up class for the session I missed in PYD-301."}, headers=auth(api, "meera"))
    rid = api.get("/api/staff/approvals", headers=auth(api, "staff1")).json()[0]["request_id"]
    assert api.post(f"/api/staff/approvals/{rid}/decision", json={"approve": True}, headers=other).status_code == 404
    assert len(api.get("/api/staff/approvals", headers=auth(api, "staff1")).json()) == 1  # still pending


def test_decision_body_is_strict(api):
    hs = auth(api, "staff1")
    assert api.post("/api/staff/approvals/x/decision", json={"approve": True, "approver": "someone_else"}, headers=hs).status_code == 422


def test_analytics_counts_events_and_is_tenant_scoped(api):
    api.post("/api/chat", json={"text": "What is the refund policy?", "tenant": T})
    escalate(api)
    a = api.get("/api/admin/analytics", params={"days": 7}, headers=auth(api, "admin1")).json()
    assert a["chat"]["turns"] >= 2
    assert a["escalations"]["created"] >= 1
    # the other tenant has data in the DB but no config.yaml: analytics refuses cleanly instead of crashing or leaking
    assert api.get("/api/admin/analytics", headers=other_headers(api, "other_admin", "admin")).status_code == 404
    assert api.get("/api/admin/analytics", params={"days": 1000}, headers=auth(api, "admin1")).status_code == 422
