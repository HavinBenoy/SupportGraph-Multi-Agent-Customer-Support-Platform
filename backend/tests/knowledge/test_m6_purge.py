"""Permanent deletion (final M6, refinement 2.4): a separate, confirmed, administrator-only operation that removes what archiving keeps.
Archive behaviour is unchanged; a live document can never be purged; legal hold / retention / open requests block; everything holding text or vectors goes;
only a content-free tombstone stays; idempotent; tenant-scoped."""

from sqlalchemy import select

from app.db.models import Chunk, Document, KbChangeItem, KbChangeRequest, KbDocument, KbEvent, KbVersion, KbVersionChunk
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.kbhelp import BASE, FEES_V1, FEES_V2, SECRET_TEXT, add_version, admin, other_admin, process, retrieve_ids
from tests.knowledge.kwhelp import ST, drafted, live_doc, staff
from tests.rag.conftest import embedder, ingested  # noqa: F401

TITLE = "Fees Test"


def doc(api, d="fees-test"):
    r = api.get(f"{BASE}/documents/{d}", headers=admin(api))
    return r.json() if r.status_code == 200 else None


def purge(api, d="fees-test", h=None, expect=200, **kw):
    cur = doc(api, d) or {"row_version": 0, "title": TITLE}
    body = {"confirm_doc_id": d, "confirm_title": cur["title"], "expected_row_version": cur["row_version"], **kw}
    r = api.post(f"{BASE}/documents/{d}/purge", json=body, headers=h or admin(api))
    assert r.status_code == expect, r.text
    return r.json()


def archived_via_deletion_request(api):
    """The normal, approval-based way a live document becomes archived (unchanged by this feature)."""
    live_doc(api, content=FEES_V1.replace("two instalments", f"two instalments {SECRET_TEXT}"))
    A.inline(api)
    rid = api.post(f"{ST}/documents/fees-test/deletion-request", json={"reason": "course discontinued", "expected_version": 1}, headers=staff(api)).json()["request_id"]
    A.approve(api, rid)
    assert A.publish(api, rid)["outcome"] == "deleted"
    assert doc(api)["status"] == "archived"
    return rid


def rows(model, d="fees-test"):
    with session_scope() as db:
        return db.execute(select(model).where(model.tenant_id == T, model.doc_id == d)).scalars().all()


def test_a_live_document_can_never_be_purged_and_archive_is_unchanged(api):  # noqa: F811
    live_doc(api)
    assert "Archive it first" in purge(api, expect=409)["detail"]
    assert doc(api)["status"] == "published" and rows(Chunk)                                 # nothing touched
    assert api.post(f"{BASE}/documents/fees-test/archive", headers=admin(api)).status_code == 409   # the M6 archive rule still holds
    assert rows(KbVersion)


def test_purging_an_archived_document_removes_text_and_vectors_everywhere_and_keeps_a_content_free_tombstone(api):  # noqa: F811
    rid = archived_via_deletion_request(api)
    before = [e.id for e in rows(KbEvent)]
    pv = api.get(f"{BASE}/documents/fees-test/purge-preview", headers=admin(api)).json()
    assert pv["blockers"] == [] and pv["removes"]["versions"] == 1 and pv["removes"]["change_items"] == 1 and SECRET_TEXT not in str(pv)
    out = purge(api)
    assert out["outcome"] == "deleted" and out["removed"]["versions"] == 1
    for model in (KbDocument, KbVersion, KbVersionChunk, Chunk, Document, KbChangeItem):
        assert rows(model) == [], model.__name__                                               # no orphaned text, passages or embeddings
    assert "fees-test" not in {r[0] for r in retrieve_ids(api, "instalments")}
    with session_scope() as db:
        req = db.get(KbChangeRequest, (T, rid))
        assert req is not None and "fees-test" in req.analysis["purged_docs"] and TITLE not in (req.description or "")   # the request stays, without the text
        evs = db.execute(select(KbEvent).where(KbEvent.tenant_id == T, KbEvent.doc_id == "fees-test").order_by(KbEvent.id)).scalars().all()
        assert [e.id for e in evs][:len(before)] == before                                    # the audit trail is kept as it was ...
        tomb = [e for e in evs if e.action == "document_purged"]
        assert len(tomb) == 1 and tomb[0].actor == "admin1" and tomb[0].summary["count"] == 1   # ... plus one tombstone
        assert SECRET_TEXT not in str([(e.summary, e.resource_id) for e in evs]) and TITLE not in str(tomb[0].summary)
    assert api.get(f"{BASE}/documents/fees-test", headers=admin(api)).status_code == 404
    assert api.get(f"{ST}/changes/{rid}", headers=staff(api)).json()["document_count"] == 0


def test_it_is_idempotent_and_the_id_cannot_be_reused(api):  # noqa: F811
    archived_via_deletion_request(api)
    purge(api)
    assert purge(api)["outcome"] == "already_deleted"                                        # a repeated click deletes nothing twice and does not fail
    r = api.post(f"{BASE}/documents", json={"doc_id": "fees-test", "title": "Again", "category": "fees", "content": FEES_V1}, headers=admin(api))
    assert r.status_code == 409 and "permanently deleted" in r.json()["detail"]
    with session_scope() as db:
        assert len(db.execute(select(KbEvent).where(KbEvent.tenant_id == T, KbEvent.action == "document_purged")).scalars().all()) == 1


def test_the_confirmation_must_name_this_exact_document_and_version(api):  # noqa: F811
    archived_via_deletion_request(api)
    cur = doc(api)
    assert "id" in purge(api, confirm_doc_id="other-doc", expect=422)["detail"]
    assert "title" in purge(api, confirm_title="Fees test", expect=422)["detail"]             # exact title (case matters)
    assert "changed" in purge(api, expected_row_version=cur["row_version"] - 1, expect=409)["detail"]   # a stale page is refused
    r = api.post(f"{BASE}/documents/fees-test/purge", json={"confirm_doc_id": "fees-test", "expected_row_version": cur["row_version"]}, headers=admin(api))
    assert r.status_code == 422                                                                # the title is required
    assert doc(api) is not None and rows(KbVersion)                                           # nothing was deleted


def test_legal_hold_and_retention_block_it(api):  # noqa: F811
    archived_via_deletion_request(api)
    cur = doc(api)
    assert api.put(f"{BASE}/documents/fees-test/legal-hold", json={"hold": True, "expected_row_version": cur["row_version"]}, headers=admin(api)).status_code == 422
    r = api.put(f"{BASE}/documents/fees-test/legal-hold", json={"hold": True, "reason": "audit 2026", "expected_row_version": cur["row_version"]}, headers=admin(api))
    assert r.status_code == 200 and r.json()["legal_hold"] is True and r.json()["legal_hold_by"] == "admin1"
    assert "legal hold" in purge(api, expect=409)["detail"]
    assert rows(KbVersion)
    cur = doc(api)
    assert api.put(f"{BASE}/documents/fees-test/legal-hold", json={"hold": False, "expected_row_version": cur["row_version"]}, headers=admin(api)).json()["legal_hold"] is False
    api.app.state.settings.kb_purge_min_archived_days = 30
    assert "retention" in purge(api, expect=409)["detail"]
    api.app.state.settings.kb_purge_min_archived_days = 0
    api.app.state.settings.kb_purge_enabled = False
    assert purge(api, expect=403)["detail"].startswith("Permanent deletion is switched off")
    api.app.state.settings.kb_purge_enabled = True
    assert purge(api)["outcome"] == "deleted"
    acts = [e.action for e in rows(KbEvent)]
    assert "document_legal_hold_set" in acts and "document_legal_hold_released" in acts


def test_an_open_change_request_blocks_it(api):  # noqa: F811
    live_doc(api)
    rid, _ = drafted(api)                                                                       # a staff draft based on the live version
    api.app.state.settings.kb_direct_archive = True
    assert api.post(f"{BASE}/documents/fees-test/archive", headers=admin(api)).status_code == 200
    assert rid in purge(api, expect=409)["detail"]
    assert api.post(f"{ST}/changes/{rid}/cancel", headers=staff(api)).status_code == 200
    assert purge(api)["outcome"] == "deleted"


def test_a_never_published_draft_can_be_purged_directly_with_its_staged_passages(api):  # noqa: F811
    from tests.knowledge.kbhelp import create
    create(api, doc_id="draft-only", title="Draft only", content=f"# Draft only\n\n{SECRET_TEXT} body.\n")
    process(api, "draft-only", 1)
    assert rows(KbVersionChunk, "draft-only")
    r = api.post(f"{BASE}/documents/draft-only/purge", json={"confirm_doc_id": "draft-only", "confirm_title": "Draft only", "expected_row_version": doc(api, "draft-only")["row_version"]},
                 headers=admin(api))
    assert r.status_code == 200 and r.json()["outcome"] == "deleted"
    assert rows(KbVersionChunk, "draft-only") == [] and rows(KbVersion, "draft-only") == []


def test_a_published_then_unpublished_draft_must_be_archived_first(api):  # noqa: F811
    live_doc(api)
    add_version(api, "fees-test", FEES_V2)
    assert api.post(f"{BASE}/documents/fees-test/unpublish", headers=admin(api)).status_code == 200
    assert "Archive it first" in purge(api, expect=409)["detail"]


def test_rbac_and_tenant_isolation(api):  # noqa: F811
    archived_via_deletion_request(api)
    body = {"confirm_doc_id": "fees-test", "confirm_title": TITLE, "expected_row_version": doc(api)["row_version"]}
    assert api.post(f"{BASE}/documents/fees-test/purge", json=body).status_code == 401
    assert api.post(f"{BASE}/documents/fees-test/purge", json=body, headers=staff(api)).status_code == 403
    assert api.post(f"{BASE}/documents/fees-test/purge", json=body, headers=other_admin(api)).status_code == 404   # another tenant cannot even find it
    assert api.get(f"{BASE}/documents/fees-test/purge-preview", headers=other_admin(api)).status_code == 404
    assert api.put(f"{BASE}/documents/fees-test/legal-hold", json={"hold": True, "reason": "x y z", "expected_row_version": 0}, headers=staff(api)).status_code == 403
    assert api.post(f"{ST}/documents/fees-test/purge", json=body, headers=staff(api)).status_code in (404, 405)   # no staff route exists
    assert doc(api) is not None


def test_a_disk_imported_document_is_not_brought_back_by_ingest_or_import(api, embedder):  # noqa: F811
    from app.knowledge.importer import unmanaged_disk_docs
    from app.rag.ingest import ingest_tenant
    r = api.post(f"{BASE}/import-disk", headers=admin(api))
    assert r.status_code == 200 and "fees-and-payments" in r.json()["imported"]
    api.app.state.settings.kb_direct_archive = True
    assert api.post(f"{BASE}/documents/fees-and-payments/archive", headers=admin(api)).status_code == 200
    d = doc(api, "fees-and-payments")
    pv = api.get(f"{BASE}/documents/fees-and-payments/purge-preview", headers=admin(api)).json()
    assert pv["disk_file_note"]
    r = api.post(f"{BASE}/documents/fees-and-payments/purge", json={"confirm_doc_id": "fees-and-payments", "confirm_title": d["title"],
                                                                    "expected_row_version": d["row_version"]}, headers=admin(api))
    assert r.status_code == 200, r.text
    with session_scope() as db:
        rep = ingest_tenant(db, T, embedder)
        assert "fees-and-payments" in rep.purged_skipped and db.get(Document, (T, "fees-and-payments")) is None
    assert "fees-and-payments" not in unmanaged_disk_docs(T)
    again = api.post(f"{BASE}/import-disk", headers=admin(api)).json()
    assert "fees-and-payments" in again["skipped"] and doc(api, "fees-and-payments") is None
