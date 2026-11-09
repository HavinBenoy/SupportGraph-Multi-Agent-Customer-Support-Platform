"""M6 audit trail: append-only, attributed, tenant-scoped, and free of document or feedback text."""

import json

import pytest
from sqlalchemy import select, update

from app.db.models import KbEvent
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge.kbhelp import (BASE, FEES_V2, SECRET_TEXT, add_version, admin, admin2, create, go_live, mk_feedback, other_admin, process, publish)
from tests.knowledge.test_feedback_proposals import propose, review
from tests.rag.conftest import embedder, ingested  # noqa: F401


def events(api, h=None, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = api.get(f"{BASE}/audit?{qs}", headers=h or admin(api))
    assert r.status_code == 200, r.text
    return r.json()


def actions(api, **q):
    return [e["action"] for e in events(api, limit=200, **q)["items"]][::-1]


def test_every_lifecycle_action_is_recorded_in_order_with_the_actor(api):  # noqa: F811
    go_live(api)
    add_version(api, "fees-test", FEES_V2, "two")
    api.put(f"{BASE}/documents/fees-test", json={"category": "faq"}, headers=admin(api))
    api.post(f"{BASE}/documents/fees-test/versions/1/restore", json={}, headers=admin(api))
    process(api, "fees-test", 3)
    publish(api, "fees-test", 3)
    api.post(f"{BASE}/documents/fees-test/unpublish", headers=admin(api))
    api.post(f"{BASE}/documents/fees-test/archive", headers=admin(api))
    api.post(f"{BASE}/documents/fees-test/unarchive", headers=admin2(api))
    got = actions(api, doc_id="fees-test")
    for a in ("document_created", "version_created", "ingestion_completed", "version_published", "metadata_updated", "version_restored", "version_unpublished",
              "document_archived", "document_unarchived"):
        assert a in got, (a, got)
    assert got.index("document_created") < got.index("ingestion_completed") < got.index("version_published")
    assert got.count("document_archived") == 1
    items = events(api, limit=200, doc_id="fees-test")["items"]
    assert {e["actor"] for e in items} == {"admin1", "admin2"} and next(e for e in items if e["action"] == "document_unarchived")["actor"] == "admin2"
    pub = next(e for e in items if e["action"] == "version_published" and e["version"] == 1)
    assert pub["version"] == 1 and pub["resource_type"] == "version" and pub["summary"]["model"].startswith("fake:") and pub["summary"]["chunks"] >= 1


def test_failed_ingestion_and_proposal_actions_are_recorded(api):  # noqa: F811
    from app.config import Settings
    from app.knowledge import pipeline
    from tests.knowledge.test_publication_rag import Boom
    create(api)
    pipeline.process_version(T, "admin1", "fees-test", 1, Boom(), Settings())
    mk_feedback("c-1")
    mk_feedback("c-2")
    propose(api, "c-1", new_document={"doc_id": "p1", "title": "P1", "category": "fees"})
    propose(api, "c-2", new_document={"doc_id": "p2", "title": "P2", "category": "fees"})
    review(api, "c-1", "approve")
    review(api, "c-2", "reject")
    got = actions(api)
    for a in ("ingestion_failed", "proposal_created", "proposal_approved", "proposal_rejected"):
        assert a in got, (a, got)
    assert events(api, action="ingestion_failed")["items"][0]["summary"]["error"]


def test_no_document_text_feedback_text_or_secret_ever_reaches_the_audit_trail(api):  # noqa: F811
    body = f"# Policy\n\nThis paragraph contains {SECRET_TEXT} and must stay out of the audit trail.\n"
    create(api, content=body, title=f"Title with {SECRET_TEXT}", description=f"Describes {SECRET_TEXT}", change_note=None) if False else None
    r = api.post(f"{BASE}/documents", json={"doc_id": "secret-doc", "title": f"Title {SECRET_TEXT}", "category": "faq", "content": body, "description": f"About {SECRET_TEXT}",
                                            "tags": ["t1"]}, headers=admin(api))
    assert r.status_code == 201, r.text
    add_version(api, "secret-doc", body + "\nmore words in this second revision\n", f"note {SECRET_TEXT}")
    process(api, "secret-doc", 1)
    publish(api, "secret-doc", 1)
    mk_feedback("c-1", text=f"wrong: {SECRET_TEXT}", fix=f"fix: {SECRET_TEXT}")
    propose(api, "c-1", new_document={"doc_id": "p1", "title": f"T {SECRET_TEXT}", "category": "fees"}, content=body)
    review(api, "c-1", "approve", note=f"note {SECRET_TEXT}")
    dump = json.dumps(events(api, limit=200))
    assert SECRET_TEXT not in dump
    with session_scope() as db:
        raw = json.dumps([[e.actor, e.action, e.resource_id, e.summary] for e in db.execute(select(KbEvent)).scalars()])
    assert SECRET_TEXT not in raw


def test_audit_record_drops_keys_that_are_not_on_the_allow_list(api):  # noqa: F811
    from app.knowledge import audit
    with session_scope() as db:
        audit.record(db, T, "admin1", "metadata_updated", "document", "d", doc_id="d", content="LEAK", password="LEAK", fields=["category", "tags"], nested={"a": 1})
    with session_scope() as db:
        e = db.execute(select(KbEvent).order_by(KbEvent.id.desc())).scalars().first()
        assert e.summary == {"fields": "category,tags"}


def test_the_audit_trail_is_append_only(api):  # noqa: F811
    create(api)
    with session_scope() as db:
        eid = db.execute(select(KbEvent.id)).scalars().first()
    from app.db.models import KbImmutableError
    with pytest.raises(KbImmutableError):
        with session_scope() as db:
            db.get(KbEvent, eid).actor = "mallory"
            db.flush()
    with pytest.raises(KbImmutableError):
        with session_scope() as db:
            db.delete(db.get(KbEvent, eid))
            db.flush()
    for m in ("put", "patch", "delete", "post"):
        assert getattr(api, m)(f"{BASE}/audit", headers=admin(api)).status_code == 405
        assert getattr(api, m)(f"{BASE}/audit/{eid}", headers=admin(api)).status_code in (404, 405)


def test_audit_is_tenant_scoped(api):  # noqa: F811
    create(api)
    assert events(api, h=other_admin(api))["items"] == []
    assert len(events(api)["items"]) >= 1


def test_filters_and_cursor_pagination(api):  # noqa: F811
    create(api)
    for i in range(5):
        add_version(api, "fees-test", f"# Fees\n\nRevision number {i} contains different words entirely.\n", f"r{i}")
    page1 = events(api, limit=3)
    assert len(page1["items"]) == 3 and page1["next_before_id"]
    page2 = events(api, limit=3, before_id=page1["next_before_id"])
    ids = [e["id"] for e in page1["items"]] + [e["id"] for e in page2["items"]]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == len(ids) == 6
    page3 = events(api, limit=3, before_id=page2["next_before_id"])
    assert len(page3["items"]) == 1 and page3["next_before_id"] is None             # document_created + 6 versions = 7 events in all
    assert {e["action"] for e in events(api, action="version_created", limit=50)["items"]} == {"version_created"}
    assert events(api, actor="nobody")["items"] == [] and events(api, doc_id="nothing")["items"] == []
    assert len(events(api, actor="admin1", limit=50)["items"]) == 7
    for bad in ("action=bogus", "limit=0", "limit=500", "before_id=0"):
        assert api.get(f"{BASE}/audit?{bad}", headers=admin(api)).status_code == 422, bad


def test_failed_requests_leave_no_audit_event(api):  # noqa: F811
    create(api)
    n = len(events(api, limit=200)["items"])
    api.post(f"{BASE}/documents", json={"doc_id": "fees-test", "title": "x", "category": "fees", "content": FEES_V2}, headers=admin(api))        # duplicate
    api.post(f"{BASE}/documents/fees-test/versions/1/publish", headers=admin(api))                                                                # not ready
    api.post(f"{BASE}/documents/fees-test/versions", json={"content": "same", "change_note": ""}, headers=admin(api))
    assert len(events(api, limit=200)["items"]) == n
