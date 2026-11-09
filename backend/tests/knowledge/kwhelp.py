"""Helpers for the Knowledge Workspace (Part 1) tests."""

from app.auth.accounts import create_account
from app.db.session import session_scope
from tests.api.conftest import PW, T, auth
from tests.knowledge.kbhelp import BASE, FEES_V1, admin, go_live

ST = "/api/staff/knowledge"
EDITED = FEES_V1.replace("two instalments", "three instalments") + "\nA late fee of 2% applies after the due date.\n"


def staff(api, user="staff1"):
    if user != "staff1":
        with session_scope() as db:
            from app.db.models import Account
            if db.get(Account, (T, user)) is None:
                create_account(db, T, user, PW, "staff")
    return auth(api, user)


def student(api):
    return auth(api, "asha")


def folder(api, name, parent=None, h=None, expect=201):
    r = api.post(f"{BASE}/folders", json={"name": name, **({"parent_id": parent} if parent else {})}, headers=h or admin(api))
    assert r.status_code == expect, r.text
    return r.json()


def live_doc(api, doc_id="fees-test", content=FEES_V1, **kw):
    go_live(api, doc_id=doc_id, content=content, **kw)
    return doc_id


def start(api, doc_id="fees-test", h=None, expect=201, **body):
    r = api.post(f"{ST}/changes", json={"doc_id": doc_id, **body}, headers=h or staff(api))
    assert r.status_code == expect, r.text
    return r.json()


def save(api, rid, doc_id, content, rv, h=None, expect=200, **kw):
    r = api.put(f"{ST}/changes/{rid}/items/{doc_id}", json={"content": content, "expected_row_version": rv, **kw}, headers=h or staff(api))
    assert r.status_code == expect, r.text
    return r.json()


def submit(api, rid, desc="Update the instalment rule", h=None, expect=200, **kw):
    r = api.post(f"{ST}/changes/{rid}/submit", json={"description": desc, **kw}, headers=h or staff(api))
    assert r.status_code == expect, r.text
    return r.json()


def drafted(api, doc_id="fees-test", content=EDITED, **kw):
    """start + save an edit; returns (request_id, item)."""
    out = start(api, doc_id, **kw)
    rid = out["request"]["request_id"]
    it = save(api, rid, doc_id, content, out["request"]["items"][0]["row_version"], h=kw.get("h"))
    return rid, it


def other_staff(api):
    from app.auth.tokens import Principal, issue_token
    from app.db.models import Account
    from tests.knowledge.kbhelp import OTHER
    with session_scope() as db:
        if db.get(Account, (OTHER, "ostaff")) is None:
            create_account(db, OTHER, "ostaff", PW, "staff")
    return {"Authorization": "Bearer " + issue_token(api.app.state.secret, Principal(OTHER, "staff", "ostaff", None), ttl_seconds=600)}


def live_texts(api, text, doc_id):
    from tests.knowledge.kbhelp import retrieve_ids
    return [t for d, _, _, t in retrieve_ids(api, text) if d == doc_id]
