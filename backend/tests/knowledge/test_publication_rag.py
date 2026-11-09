"""M6 publication and the existing RAG pipeline: what is retrievable when, atomic switch, failure safety, idempotence, disk-ingest coexistence."""

import threading

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import Chunk, Document, KbDocument, KbVersion, KbVersionChunk
from app.db.session import session_scope
from app.knowledge import pipeline
from app.knowledge.errors import KbError
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge.kbhelp import (BASE, FEES_V1, FEES_V2, FEES_V3, add_version, admin, create, go_live, live_chunks, live_doc, process, publish, retrieve_ids,
                                    staged_count)
from tests.rag.conftest import embedder, ingested  # noqa: F401

QUERY_V1 = "programme fee two instalments first instalment enrolment"
UNIQUE_V3 = "quokkafee"


def ids(api, q, **kw):
    return {(d, v) for d, v, _s, _t in retrieve_ids(api, q, **kw)}


# ---- drafts never affect retrieval --------------------------------------------------------------------------------------------------
def test_drafts_and_processed_but_unpublished_versions_are_invisible_to_retrieval(api):  # noqa: F811
    create(api)
    assert not [1 for d, *_ in retrieve_ids(api, QUERY_V1) if d == "fees-test"]
    r = process(api, "fees-test", 1)
    assert r["status"] == "ready" and r["chunks"] >= 1 and r["unchanged"] is False
    assert staged_count("fees-test", 1) == r["chunks"]                     # staged, but staged rows are never searched
    assert live_chunks("fees-test") == [] and live_doc("fees-test") is None
    assert not [1 for d, *_ in retrieve_ids(api, QUERY_V1) if d == "fees-test"]


def test_an_unprocessed_failed_or_missing_version_cannot_be_published(api):  # noqa: F811
    create(api)
    assert "draft" in publish(api, "fees-test", 1, expect=409)["detail"].lower()
    assert api.post(f"{BASE}/documents/fees-test/versions/5/publish", headers=admin(api)).status_code == 404
    assert live_doc("fees-test") is None


# ---- publishing --------------------------------------------------------------------------------------------------------------------
def test_publishing_makes_the_version_retrievable_with_its_ids_and_attribution(api):  # noqa: F811
    go_live(api)
    got = retrieve_ids(api, QUERY_V1)
    top = [g for g in got if g[0] == "fees-test"]
    assert top and all(v == 1 for _d, v, _s, _t in top)
    d = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()
    assert (d["status"], d["published_version"], d["published_by"]) == ("published", 1, "admin1")
    v = api.get(f"{BASE}/documents/fees-test/versions/1", headers=admin(api)).json()
    assert (v["status"], v["lifecycle"], v["published_by"]) == ("published", "published", "admin1") and v["published_at"]
    live = live_doc("fees-test")
    assert live["kb_version"] == 1 and live["content_hash"] == v["checksum"]
    assert staged_count("fees-test", 1) == 0                              # staging is emptied once live
    assert len(live_chunks("fees-test")) == v["chunk_count"]


def test_publishing_again_is_idempotent(api):  # noqa: F811
    go_live(api)
    before = live_chunks("fees-test")
    assert publish(api, "fees-test", 1)["unchanged"] is True
    assert live_chunks("fees-test") == before


def test_a_new_published_version_replaces_the_old_one_and_the_old_one_is_superseded(api):  # noqa: F811
    go_live(api)
    add_version(api, "fees-test", FEES_V3, "four", title="Fees (v3)")
    process(api, "fees-test", 2)
    assert ids(api, UNIQUE_V3) == ids(api, UNIQUE_V3) and ("fees-test", 2) not in ids(api, UNIQUE_V3)           # not live yet
    assert any("two instalments" in t for _d, _v, _s, t in retrieve_ids(api, QUERY_V1))
    r = publish(api, "fees-test", 2)
    assert r["previous_version"] == 1
    texts = [t for d, _v, _s, t in retrieve_ids(api, UNIQUE_V3) if d == "fees-test"]
    assert texts and all("two instalments" not in t for t in texts) and any(UNIQUE_V3 in t for t in texts)
    assert all(v == 2 for d, v, *_ in retrieve_ids(api, QUERY_V1) if d == "fees-test")
    h = api.get(f"{BASE}/documents/fees-test/versions", headers=admin(api)).json()["items"]
    assert {x["version"]: x["lifecycle"] for x in h} == {1: "superseded", 2: "published"}
    assert live_doc("fees-test")["title"] == "Fees (v3)"
    assert api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["title"] == "Fees (v3)"


def test_restoring_an_old_version_and_publishing_it_goes_live_as_a_new_version_number(api):  # noqa: F811
    go_live(api)
    add_version(api, "fees-test", FEES_V3, "four")
    process(api, "fees-test", 2)
    publish(api, "fees-test", 2)
    v3 = api.post(f"{BASE}/documents/fees-test/versions/1/restore", json={}, headers=admin(api)).json()["version"]["version"]
    assert v3 == 3
    process(api, "fees-test", 3)
    publish(api, "fees-test", 3)
    assert live_doc("fees-test")["kb_version"] == 3
    assert any("two instalments" in t for d, _v, _s, t in retrieve_ids(api, QUERY_V1) if d == "fees-test")
    assert {x["version"]: x["lifecycle"] for x in api.get(f"{BASE}/documents/fees-test/versions", headers=admin(api)).json()["items"]} == {1: "superseded", 2: "superseded", 3: "published"}


def test_publish_changes_the_cache_fingerprint_so_old_cached_answers_cannot_be_served(api):  # noqa: F811
    go_live(api)
    h1 = live_doc("fees-test")["content_hash"]
    add_version(api, "fees-test", FEES_V2, "n")
    process(api, "fees-test", 2)
    publish(api, "fees-test", 2)
    assert live_doc("fees-test")["content_hash"] != h1


def test_the_published_content_is_the_content_of_the_published_version_even_after_newer_drafts(api):  # noqa: F811
    go_live(api)
    add_version(api, "fees-test", FEES_V3, "draft edit")
    assert all("quokkafee" not in t for _d, _v, _s, t in retrieve_ids(api, UNIQUE_V3))
    assert [t for _i, t in live_chunks("fees-test")] and "two instalments" in " ".join(t for _i, t in live_chunks("fees-test"))


# ---- failure safety ---------------------------------------------------------------------------------------------------------------
class Boom:
    name = "fake:hashing-v1-768"
    dim = 768

    def embed_documents(self, texts, *a, **k):
        raise RuntimeError("embedding service down: SECRET-PAYLOAD-" + texts[0][:20])

    def embed_query(self, t):
        raise RuntimeError("down")


def test_a_failed_ingestion_marks_the_version_failed_and_leaves_the_published_version_untouched(api):  # noqa: F811
    go_live(api)
    before = live_chunks("fees-test")
    add_version(api, "fees-test", FEES_V3, "four")
    s = Settings()
    r = pipeline.process_version(T, "admin1", "fees-test", 2, Boom(), s)
    assert r["status"] == "failed" and "SECRET-PAYLOAD" not in r["error"]
    v = api.get(f"{BASE}/documents/fees-test/versions/2", headers=admin(api)).json()
    assert v["status"] == "failed" and v["lifecycle"] == "failed" and v["processing_error"] and "SECRET-PAYLOAD" not in v["processing_error"]
    assert staged_count("fees-test", 2) == 0
    assert publish(api, "fees-test", 2, expect=409)
    assert live_chunks("fees-test") == before and live_doc("fees-test")["kb_version"] == 1                    # still serving v1
    assert any("two instalments" in t for d, _v, _s, t in retrieve_ids(api, QUERY_V1) if d == "fees-test")
    r2 = process(api, "fees-test", 2)                                                                          # recoverable: just process again
    assert r2["status"] == "ready"
    publish(api, "fees-test", 2)
    assert live_doc("fees-test")["kb_version"] == 2


def test_processing_twice_does_not_duplicate_chunks_and_force_rebuilds_cleanly(api):  # noqa: F811
    create(api)
    a = process(api, "fees-test", 1)
    b = process(api, "fees-test", 1)
    assert b["unchanged"] is True and b["chunks"] == a["chunks"] and staged_count("fees-test", 1) == a["chunks"]
    c = process(api, "fees-test", 1, force=True)
    assert c["status"] == "ready" and c["unchanged"] is False and staged_count("fees-test", 1) == a["chunks"]
    with session_scope() as db:
        rows = db.execute(select(KbVersionChunk.chunk_index).where(KbVersionChunk.doc_id == "fees-test")).all()
    assert len(rows) == len({r[0] for r in rows})


def test_a_publish_that_fails_midway_rolls_back_completely(api, monkeypatch):  # noqa: F811
    go_live(api)
    before = live_chunks("fees-test")
    add_version(api, "fees-test", FEES_V3, "four")
    process(api, "fees-test", 2)
    real = pipeline._drop_staged

    def explode(db, t, d, n):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "_drop_staged", explode)
    with pytest.raises(RuntimeError):
        with session_scope() as db:
            pipeline.publish_version(db, T, "admin1", "fees-test", 2, "fake:hashing-v1-768")
    monkeypatch.setattr(pipeline, "_drop_staged", real)
    assert live_chunks("fees-test") == before and live_doc("fees-test")["kb_version"] == 1
    d = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()
    assert (d["published_version"], d["status"]) == (1, "published")
    assert {x["version"]: x["lifecycle"] for x in api.get(f"{BASE}/documents/fees-test/versions", headers=admin(api)).json()["items"]} == {1: "published", 2: "ready_for_review"}
    assert staged_count("fees-test", 2) >= 1                                                                    # still processed: a retry succeeds
    publish(api, "fees-test", 2)
    assert live_doc("fees-test")["kb_version"] == 2


def test_a_changed_embedding_model_blocks_publishing_until_reprocessed(api):  # noqa: F811
    create(api)
    process(api, "fees-test", 1)
    with session_scope() as db:
        with pytest.raises(KbError):
            pipeline.publish_version(db, T, "admin1", "fees-test", 1, "some-other-model:v9")
    assert live_doc("fees-test") is None


def test_stale_expected_row_version_blocks_publish(api):  # noqa: F811
    create(api)
    process(api, "fees-test", 1)
    rv = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["row_version"]
    r = api.post(f"{BASE}/documents/fees-test/versions/1/publish", json={"expected_row_version": rv + 5}, headers=admin(api))
    assert r.status_code == 409 and live_doc("fees-test") is None
    assert api.post(f"{BASE}/documents/fees-test/versions/1/publish", json={"expected_row_version": rv}, headers=admin(api)).status_code == 200


# ---- unpublish / archive -------------------------------------------------------------------------------------------------------------
def test_unpublish_removes_retrieval_but_keeps_all_history(api):  # noqa: F811
    go_live(api)
    r = api.post(f"{BASE}/documents/fees-test/unpublish", headers=admin(api))
    assert r.status_code == 200 and r.json()["unpublished_version"] == 1
    assert live_doc("fees-test") is None and live_chunks("fees-test") == []
    assert not [1 for d, *_ in retrieve_ids(api, QUERY_V1) if d == "fees-test"]
    d = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()
    assert d["status"] == "draft" and d["published_version"] is None and d["latest_version"] == 1
    assert api.get(f"{BASE}/documents/fees-test/versions/1", headers=admin(api)).json()["content"] == FEES_V1
    assert api.post(f"{BASE}/documents/fees-test/unpublish", headers=admin(api)).status_code == 409
    process(api, "fees-test", 1)                                                                               # can be re-processed and published again
    publish(api, "fees-test", 1)
    assert live_doc("fees-test")["kb_version"] == 1


def test_archive_removes_retrieval_retains_history_and_blocks_new_publication_until_unarchived(api):  # noqa: F811
    go_live(api)
    add_version(api, "fees-test", FEES_V3, "four")
    process(api, "fees-test", 2)
    assert api.post(f"{BASE}/documents/fees-test/archive", headers=admin(api)).json()["status"] == "archived"
    assert live_doc("fees-test") is None and live_chunks("fees-test") == []
    assert not [1 for d, *_ in retrieve_ids(api, QUERY_V1) if d == "fees-test"]
    d = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()
    assert (d["status"], d["published_version"], d["latest_version"]) == ("archived", None, 2)
    assert publish(api, "fees-test", 2, expect=409)
    assert api.post(f"{BASE}/documents/fees-test/versions", json={"content": FEES_V2, "change_note": "x"}, headers=admin(api)).status_code in (201, 409)
    assert api.post(f"{BASE}/documents/fees-test/archive", headers=admin(api)).json()["unchanged"] is True
    assert api.get(f"{BASE}/documents?status=archived", headers=admin(api)).json()["total"] == 1
    assert api.get("/api/staff/knowledge/documents/fees-test", headers=auth(api, "staff1")).status_code == 404
    r = api.post(f"{BASE}/documents/fees-test/unarchive", headers=admin(api))
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert live_doc("fees-test") is None                                                                       # unarchiving never silently republishes
    assert api.post(f"{BASE}/documents/fees-test/unarchive", headers=admin(api)).status_code == 409
    process(api, "fees-test", 2, force=True)
    publish(api, "fees-test", 2)
    assert live_doc("fees-test")["kb_version"] == 2


# ---- tenants ------------------------------------------------------------------------------------------------------------------------
def test_retrieval_is_tenant_scoped_for_store_documents(api):  # noqa: F811
    go_live(api)
    assert not [1 for d, *_ in retrieve_ids(api, QUERY_V1, tenant="other_institute") if d == "fees-test"]


# ---- chat answers use the published version with source attribution ------------------------------------------------------------------
def test_the_public_chat_answers_from_the_published_version_and_cites_it(api):  # noqa: F811
    go_live(api, doc_id="fees-and-payments-kb", content="# Fees\n\n## Instalments\n\nStudents may pay the programme fee in two instalments. The first instalment is due at enrolment.\n", title="Fee rules")
    r = api.post("/api/chat", json={"text": "Can I pay the programme fee in instalments?", "tenant": T})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"]
    assert any(x["id"].startswith("fees-and-payments-kb#") for x in body["citations"]), body["citations"]


# ---- concurrent publication -------------------------------------------------------------------------------------------------------
def test_concurrent_publishes_of_two_versions_leave_exactly_one_consistent_live_version(api):  # noqa: F811
    go_live(api)
    add_version(api, "fees-test", FEES_V2, "b")
    add_version(api, "fees-test", FEES_V3, "c")
    process(api, "fees-test", 2)
    process(api, "fees-test", 3)
    res = []

    def go(n):
        try:
            with session_scope() as db:
                pipeline.publish_version(db, T, f"admin{n}", "fees-test", n, "fake:hashing-v1-768")
            res.append(("ok", n))
        except KbError as e:
            res.append(("conflict", n))
        except Exception as e:                     # noqa: BLE001
            res.append(("err", repr(e)))

    ts = [threading.Thread(target=go, args=(n,)) for n in (2, 3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not [r for r in res if r[0] == "err"], res
    assert [r for r in res if r[0] == "ok"], res
    with session_scope() as db:
        d = db.get(KbDocument, (T, "fees-test"))
        live = db.get(Document, (T, "fees-test"))
        pub = [r[0] for r in db.execute(select(KbVersion.version_no).where(KbVersion.doc_id == "fees-test", KbVersion.status == "published")).all()]
        n = db.execute(select(func.count()).select_from(Chunk).where(Chunk.doc_id == "fees-test")).scalar()
        idx = [r[0] for r in db.execute(select(Chunk.chunk_index).where(Chunk.doc_id == "fees-test")).all()]
    assert pub == [d.published_version] and live.kb_version == d.published_version and d.status == "published"
    assert n == len(set(idx))
    with session_scope() as db:
        expected = db.get(KbVersion, (T, "fees-test", d.published_version)).chunk_count
    assert n == expected


# ---- disk ingest coexistence ---------------------------------------------------------------------------------------------------------
def test_disk_ingest_neither_overwrites_nor_withdraws_documents_managed_by_the_store(api, embedder):  # noqa: F811
    """A store article that shares its id with a disk file keeps the store's content; ingest reports it as skipped and never removes store-only articles."""
    from app.rag.ingest import ingest_tenant
    go_live(api, doc_id="fees-and-payments", content="# Fees\n\n## Store text\n\nThis store-managed paragraph about quokkafee must survive a disk ingest.\n", title="Store fees")
    go_live(api, doc_id="store-only", content="# Only\n\nA store only article that has no file on disk anywhere.\n", title="Store only")
    before = {d: live_chunks(d) for d in ("fees-and-payments", "store-only")}
    with session_scope() as db:
        rep = ingest_tenant(db, T, embedder)
    assert "fees-and-payments" in rep.managed_skipped and "fees-and-payments" not in rep.updated + rep.added + rep.removed
    assert "store-only" not in rep.removed
    assert {d: live_chunks(d) for d in before} == before
    assert live_doc("fees-and-payments")["kb_version"] == 1 and live_doc("store-only")["kb_version"] == 1


def test_import_disk_puts_existing_disk_articles_under_the_store_without_changing_what_is_live(api):  # noqa: F811
    from app.rag.ingest import ingest_tenant
    s = api.app.state.settings
    emb = __import__("app.llm.gateway", fromlist=["x"]).get_embedding_client(s)
    with session_scope() as db:
        ingest_tenant(db, T, emb)
    with session_scope() as db:
        before = {c: t for c, t in db.execute(select(Chunk.doc_id, func.count()).where(Chunk.tenant_id == T).group_by(Chunk.doc_id)).all()}
    assert before
    assert "fees-and-payments" in api.get(f"{BASE}/meta", headers=admin(api)).json()["unmanaged_disk_documents"]
    r = api.post(f"{BASE}/import-disk", headers=admin(api))
    assert r.status_code == 200, r.text
    with session_scope() as db:
        after = {c: t for c, t in db.execute(select(Chunk.doc_id, func.count()).where(Chunk.tenant_id == T).group_by(Chunk.doc_id)).all()}
    assert after == before                                                                                      # imported verbatim, nothing duplicated or dropped
    d = api.get(f"{BASE}/documents/fees-and-payments", headers=admin(api)).json()
    assert d["origin"] == "imported" and d["status"] == "published" and d["published_version"] == 1
    assert api.get(f"{BASE}/meta", headers=admin(api)).json()["unmanaged_disk_documents"] == []
    assert api.post(f"{BASE}/import-disk", headers=admin(api)).status_code == 200                               # idempotent
    with session_scope() as db:
        again = {c: t for c, t in db.execute(select(Chunk.doc_id, func.count()).where(Chunk.tenant_id == T).group_by(Chunk.doc_id)).all()}
    assert again == before
    with session_scope() as db:
        ingest_tenant(db, T, emb)                                                                               # a later disk ingest leaves managed articles alone
    with session_scope() as db:
        assert {c: t for c, t in db.execute(select(Chunk.doc_id, func.count()).where(Chunk.tenant_id == T).group_by(Chunk.doc_id)).all()} == before


# ---- processing claims ---------------------------------------------------------------------------------------------------------------
def _force_processing(doc_id, n, age_s):
    from datetime import timedelta
    from sqlalchemy import update
    from app.knowledge import store
    with session_scope() as db:
        db.execute(update(KbVersion).where(KbVersion.doc_id == doc_id, KbVersion.version_no == n)
                   .values(status="processing", processing_started_at=store.now() - timedelta(seconds=age_s)).execution_options(synchronize_session=False))


def test_a_version_being_processed_right_now_is_refused_but_a_stale_claim_can_be_taken_over(api):  # noqa: F811
    create(api)
    _force_processing("fees-test", 1, 5)                                                   # claimed 5 s ago: another worker is on it
    assert api.post(f"{BASE}/documents/fees-test/versions/1/process", headers=admin(api)).status_code == 409
    assert publish(api, "fees-test", 1, expect=409)
    _force_processing("fees-test", 1, 100_000)                                              # claimed long ago: the worker died
    r = process(api, "fees-test", 1)
    assert r["status"] == "ready" and staged_count("fees-test", 1) == r["chunks"]
    ev = api.get(f"{BASE}/audit?action=ingestion_started&limit=10", headers=admin(api)).json()["items"]
    assert any(e["summary"].get("stale_takeover") is True for e in ev)


def test_an_archived_document_cannot_be_processed(api):  # noqa: F811
    go_live(api)
    api.post(f"{BASE}/documents/fees-test/archive", headers=admin(api))
    assert api.post(f"{BASE}/documents/fees-test/versions/1/process", headers=admin(api)).status_code == 409


def test_import_disk_is_audited(api):  # noqa: F811
    from app.rag.ingest import ingest_tenant
    s = api.app.state.settings
    emb = __import__("app.llm.gateway", fromlist=["x"]).get_embedding_client(s)
    with session_scope() as db:
        ingest_tenant(db, T, emb)
    assert api.post(f"{BASE}/import-disk", headers=admin(api)).status_code == 200
    ev = api.get(f"{BASE}/audit?action=disk_import", headers=admin(api)).json()["items"]
    assert ev and ev[0]["summary"]["imported"] >= 1
