"""M8 administration and self-service PIN API: RBAC, tenant isolation, IDOR, validation, re-authentication, audit without numbers or PINs."""

from app.db.models import AccountPhone, PhonePin, TelephonyAudit
from app.db.session import session_scope
from tests.api.conftest import PW, T, api, auth  # noqa: F401
from tests.api.test_staff_admin import OTHER, other_headers
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401
from tests.telephony.phonehelp import NUMBER, set_student_phone

ADMIN = [("get", "/api/admin/telephony/status"), ("get", "/api/admin/telephony/accounts"), ("put", "/api/admin/telephony/phones/staff1"),
         ("delete", "/api/admin/telephony/phones/staff1"), ("delete", "/api/admin/telephony/pins/asha"), ("get", "/api/admin/telephony/summary"),
         ("get", "/api/admin/telephony/audit")]


def call(api, method, path, headers=None, **kw):  # noqa: F811
    if method == "put":
        kw.setdefault("json", {"phone": NUMBER})
    return getattr(api, method)(path, headers=headers or {}, **kw)


def test_every_telephony_admin_route_needs_an_administrator_of_the_same_tenant(api):  # noqa: F811
    for m, path in ADMIN:
        assert call(api, m, path).status_code == 401, path
        assert call(api, m, path, auth(api, "staff1")).status_code == 403, path
        assert call(api, m, path, auth(api, "asha")).status_code == 403, path
    other = other_headers(api, "otheradmin", "admin")
    assert call(api, "put", "/api/admin/telephony/phones/staff1", other).status_code == 404          # another tenant's account is not found
    assert call(api, "delete", "/api/admin/telephony/pins/asha", other).status_code == 404
    assert all(a["username"] == "otheradmin" for a in call(api, "get", "/api/admin/telephony/accounts", other).json()["items"])
    with session_scope() as db:
        assert db.query(AccountPhone).count() == 0


def test_phone_numbers_of_staff_are_normalised_masked_audited_and_a_students_number_is_not_editable_here(api):  # noqa: F811
    adm = auth(api, "admin1")
    assert api.put("/api/admin/telephony/phones/staff1", json={"phone": "098450 12345"}, headers=adm).json() == {"username": "staff1", "phone_masked": "+91 ******2345"}
    assert api.put("/api/admin/telephony/phones/staff1", json={"phone": "12-34"}, headers=adm).status_code == 422
    assert api.put("/api/admin/telephony/phones/asha", json={"phone": NUMBER}, headers=adm).status_code == 422
    assert api.put("/api/admin/telephony/phones/nobody", json={"phone": NUMBER}, headers=adm).status_code == 404
    assert api.put("/api/admin/telephony/phones/staff1", json={"phone": NUMBER, "x": 1}, headers=adm).status_code == 422   # unknown fields refused
    set_student_phone()
    items = {a["username"]: a for a in api.get("/api/admin/telephony/accounts", headers=adm).json()["items"]}
    assert items["staff1"]["phone_masked"] == "+91 ******2345" and items["asha"]["phone_source"] == "student record" and items["meera"]["phone_masked"] is None
    assert "9845012345" not in str(items)
    assert api.delete("/api/admin/telephony/phones/staff1", headers=adm).json() == {"removed": True}
    assert api.delete("/api/admin/telephony/phones/staff1", headers=adm).json() == {"removed": False}
    with session_scope() as db:
        rows = db.query(TelephonyAudit).filter(TelephonyAudit.tenant_id == T).all()
        assert [r.action for r in rows] == ["phone_set", "phone_removed"] and all(r.actor == "admin1" for r in rows)
        assert "9845" not in str([(r.action, r.subject) for r in rows])


def test_an_account_sets_its_own_pin_only_after_reentering_its_password(api):  # noqa: F811
    me = auth(api, "asha")
    assert api.get("/api/me/phone-pin", headers=me).json()["has_pin"] is False
    assert api.put("/api/me/phone-pin", json={"current_password": "wrong-password", "pin": "482913"}, headers=me).status_code == 403
    for weak in ("111111", "123456", "654321", "12345", "abcdef"):
        assert api.put("/api/me/phone-pin", json={"current_password": PW, "pin": weak}, headers=me).status_code == 422, weak
    r = api.put("/api/me/phone-pin", json={"current_password": PW, "pin": "482913"}, headers=me)
    assert r.status_code == 200 and r.json()["has_pin"] is True and "482913" not in r.text
    with session_scope() as db:
        row = db.get(PhonePin, (T, "asha"))
        assert row.pin_hash.startswith("scrypt$") and "482913" not in row.pin_hash
    assert api.get("/api/me/phone-pin", headers=auth(api, "meera")).json()["has_pin"] is False      # only one's own
    assert api.request("DELETE", "/api/me/phone-pin", json={"current_password": "nope-nope"}, headers=me).status_code == 403
    assert api.request("DELETE", "/api/me/phone-pin", json={"current_password": PW}, headers=me).json() == {"removed": True}
    assert api.get("/api/me/phone-pin").status_code == 401


def test_repeated_wrong_passwords_on_the_pin_form_are_rate_limited(api):  # noqa: F811
    me = auth(api, "staff1")
    codes = [api.put("/api/me/phone-pin", json={"current_password": f"bad-{i}", "pin": "482913"}, headers=me).status_code for i in range(12)]
    assert 429 in codes and codes[0] == 403
    assert api.put("/api/me/phone-pin", json={"current_password": PW, "pin": "482913"}, headers=me).status_code == 429   # still blocked


def test_the_summary_counts_phone_calls_by_identity_status_for_this_tenant_only(api):  # noqa: F811
    from app.calls import service as svc
    with session_scope() as db:
        svc.create_call(db, T, "t1", None, channel="phone", phone={"caller_status": "unknown"})
        svc.create_call(db, T, "t2", None, channel="phone", phone={"caller_status": "verified"})
        svc.create_call(db, T, "t3", None)
    r = api.get("/api/admin/telephony/summary?days=7", headers=auth(api, "admin1")).json()
    assert r["phone"]["total"] == 2 and r["phone"]["verified"] == 1 and r["phone"]["unknown"] == 1 and r["web_calls"] == 1
    assert api.get("/api/admin/telephony/summary?days=0", headers=auth(api, "admin1")).status_code == 422
    assert api.get("/api/admin/telephony/summary", headers=other_headers(api, "oa", "admin")).json()["phone"]["total"] == 0
