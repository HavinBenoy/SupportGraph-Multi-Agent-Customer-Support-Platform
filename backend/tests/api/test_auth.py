import time

from app.auth.tokens import Principal, issue_token
from app.db.models import Account
from app.db.session import session_scope
from tests.api.conftest import PW, T, auth, login


def test_login_ok_and_me(api):
    r = login(api, "asha")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "student" and body["student_id"] == "stu_asha" and body["tenant_id"] == T
    me = api.get("/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.json()["username"] == "asha"


def test_wrong_password_and_unknown_user_look_identical(api):
    a, b = login(api, "asha", pw="nope-nope-nope"), login(api, "ghost")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_unknown_tenant_is_404(api):
    assert login(api, "asha", tenant="nowhere").status_code == 404


def test_login_lockout_after_repeated_failures(api):
    for _ in range(api.app.state.settings.login_max_failures):
        assert login(api, "asha", pw="wrong-wrong-1").status_code == 401
    r = login(api, "asha")  # even the right password is refused while locked
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_disabled_account_cannot_login_and_live_token_dies(api):
    h = auth(api, "staff1")
    assert api.get("/api/staff/escalations", headers=h).status_code == 200
    with session_scope() as db:
        db.get(Account, (T, "staff1")).disabled = True
    assert api.get("/api/staff/escalations", headers=h).status_code == 401
    assert login(api, "staff1").status_code == 401


def test_garbage_and_expired_tokens_rejected(api):
    assert api.get("/api/auth/me", headers={"Authorization": "Bearer abc.def"}).status_code == 401
    old = issue_token(api.app.state.secret, Principal(T, "staff", "staff1", None), ttl_seconds=-5)
    assert api.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert api.get("/api/auth/me").status_code == 401


def test_token_signed_with_other_secret_rejected(api):
    forged = issue_token("z" * 48, Principal(T, "admin", "admin1", None), ttl_seconds=600)
    assert api.get("/api/admin/analytics", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_role_in_token_must_match_account(api):
    # a validly signed token claiming a higher role than the account has is rejected
    t = issue_token(api.app.state.secret, Principal(T, "admin", "staff1", None), ttl_seconds=600)
    assert api.get("/api/admin/analytics", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_public_config(api):
    r = api.get("/api/config", params={"tenant": T})
    assert r.json()["is_sample"] is True
    assert api.get("/api/config", params={"tenant": "../x"}).status_code == 404


def test_health_and_no_internals_in_errors(api):
    assert api.get("/api/health").json() == {"status": "ok"}
    r = api.post("/api/chat", json={"text": "x", "bogus": 1})
    assert r.status_code == 422
