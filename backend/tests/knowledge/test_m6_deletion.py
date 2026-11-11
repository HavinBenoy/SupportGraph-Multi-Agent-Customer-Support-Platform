"""Document deletion with administrator approval (final M6). A deletion is a change request of kind "deletion": nothing is removed when it is requested; only an
administrator's approval + the publish step archive the document (it leaves retrieval; versions and audit stay; no hard delete)."""

from sqlalchemy import select

from app.db.models import KbDocument, KbEvent, KbVersion
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.kbhelp import BASE, FEES_V1, FEES_V2, add_version, admin, admin2, live_chunks, other_admin, process, retrieve_ids
from tests.knowledge.kwhelp import ST, live_doc, other_staff, staff, student
from tests.rag.conftest import embedder, ingested  # noqa: F401


def ask(api, doc_id="fees-test", h=None, expect=201, base=ST, **kw):
    body = {"reason": "This course is no longer offered", "expected_version": 1, **kw}
    r = api.post(f"{base}/documents/{doc_id}/deletion-request", json=body, headers=h or staff(api))
    assert r.status_code == expect, r.text
    return r.json()


def doc_row(doc_id="fees-test"):
    with session_scope() as db:
        d = db.get(KbDocument, (T, doc_id))
        return d.status, d.published_version


def events(action):
    with session_scope() as db:
        return db.execute(select(KbEvent).where(KbEvent.tenant_id == T, KbEvent.action == action)).scalars().all()


def test_a_staff_request_removes_nothing_until_an_administrator_approves_and_publishes(api):  # noqa: F811
    live_doc(api)
    A.inline(api)
    req = ask(api)
    assert req["status"] == "submitted" and req["analysis"]["kind"] == "deletion" and req["analysis"]["deletion_reason"].startswith("This course")
    assert doc_row() == ("published", 1) and live_chunks("fees-test")                        # nothing happened yet
    assert "fees-test" in {r[0] for r in retrieve_ids(api, "instalments")}
    rid = req["request_id"]
    assert api.get(f"{BASE}/changes/{rid}", headers=admin(api)).json()["status"] == "submitted"   # it is in the administrators' queue
    A.approve(api, rid)
    assert doc_row() == ("published", 1)                                                       # approval alone publishes nothing
    out = A.publish(api, rid)
    assert out["outcome"] == "deleted" and out["request"]["status"] == "published"
    assert doc_row() == ("archived", None) and live_chunks("fees-test") == []                   # out of retrieval
    assert "fees-test" not in {r[0] for r in retrieve_ids(api, "instalments")}
    with session_scope() as db:                                                                  # no hard delete: every version is kept
        assert [v.status for v in db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.doc_id == "fees-test")).scalars()] == ["archived"]
    assert events("document_deletion_requested") and events("document_deletion_published") and events("document_archived")
    assert A.publish(api, rid)["outcome"] == "already_published"                               # a repeated click deletes nothing twice


def test_a_deletion_is_refused_when_the_document_changed_after_the_request(api):  # noqa: F811
    live_doc(api)
    A.inline(api)
    rid = ask(api)["request_id"]
    add_version(api, "fees-test", FEES_V2)
    process(api, "fees-test", 2)
    from tests.knowledge.kbhelp import publish as kb_publish
    if doc_row() != ("published", 2):
        kb_publish(api, "fees-test", 2)
    r = api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve"}, headers=admin(api))
    assert r.status_code == 409 and "published again" in r.json()["detail"]
    assert doc_row() == ("published", 2)
    assert ask(api, expected_version=1, expect=409)                                            # a request must name the current version


def test_reject_and_cancel_leave_the_document_published(api):  # noqa: F811
    live_doc(api)
    A.inline(api)
    rid = ask(api)["request_id"]
    assert ask(api, expect=409)                                                                 # one open deletion per document
    A.a_post(api, rid, "review", {"decision": "reject", "note": "still offered"})
    assert doc_row() == ("published", 1)
    rid2 = ask(api)["request_id"]
    r = api.post(f"{ST}/changes/{rid2}/cancel", headers=staff(api))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert doc_row() == ("published", 1) and live_chunks("fees-test")


def test_a_deletion_request_cannot_be_edited_or_returned(api):  # noqa: F811
    live_doc(api)
    A.inline(api)
    rid = ask(api)["request_id"]
    r = api.post(f"{BASE}/changes/{rid}/review", json={"decision": "return", "note": "please edit"}, headers=admin(api))
    assert r.status_code == 422
    r = api.put(f"{BASE}/changes/{rid}/items/fees-test/edit", json={"content": FEES_V1 + "x"}, headers=admin(api))
    assert r.status_code in (409, 422)


def test_an_administrator_must_confirm_and_still_goes_through_review(api):  # noqa: F811
    live_doc(api)
    A.inline(api)
    assert ask(api, h=admin(api), base=BASE, expect=422)                                        # no confirmation, no request
    req = ask(api, h=admin(api), base=BASE, confirm=True)
    assert req["status"] == "submitted" and doc_row() == ("published", 1)                       # no bypass: the queue, approval and audit as anyone
    api.app.state.settings.kb_require_separate_reviewer = True
    r = api.post(f"{BASE}/changes/{req['request_id']}/review", json={"decision": "approve"}, headers=admin(api))
    assert r.status_code == 403                                                                 # with a second reviewer required, the requester cannot approve
    A.approve(api, req["request_id"], h=admin2(api))
    assert A.publish(api, req["request_id"], h=admin2(api))["outcome"] == "deleted"


def test_rbac_and_tenant_isolation(api):  # noqa: F811
    live_doc(api)
    A.inline(api)
    assert api.post(f"{ST}/documents/fees-test/deletion-request", json={"reason": "gone now", "expected_version": 1}).status_code == 401
    assert ask(api, h=student(api), expect=403)
    assert ask(api, h=other_staff(api), expect=404)                                             # another tenant cannot even see the document
    rid = ask(api)["request_id"]
    assert api.get(f"{BASE}/changes/{rid}", headers=other_admin(api)).status_code == 404
    assert api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve"}, headers=other_admin(api)).status_code == 404
    assert ask(api, doc_id="no-such-doc", expect=404)
    assert ask(api, reason="x", expect=422)


def test_the_folder_api_is_retired_by_default(api):  # noqa: F811
    live_doc(api)
    assert api.post(f"{BASE}/folders", json={"name": "Fees"}, headers=admin(api)).status_code == 410
    assert api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": None}, headers=admin(api)).status_code == 410
    t = api.get(f"{BASE}/tree", headers=admin(api)).json()                                     # the document list still works (and keeps every document)
    assert [d["doc_id"] for d in t["documents"]] == ["fees-test"]
