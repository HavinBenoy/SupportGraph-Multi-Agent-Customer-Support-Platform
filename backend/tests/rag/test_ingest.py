"""Ingestion (app/rag/ingest.py): idempotency, change detection, withdrawal, tenant scoping, and the database-level
guarantee that staff-only text cannot be stored (layer 2 of the internal-document defence)."""

import shutil
from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Chunk, Document
from app.db.session import session_scope
from app.llm.embeddings import EmbeddingClient
from app.llm.fake_embeddings import HashingEmbeddings
from app.rag.documents import IngestError, InternalDocumentError
from app.rag.evaluate import internal_markers
from app.rag.ingest import ingest_tenant
from app.tenants.loader import TENANTS_DIR
from tests.rag.conftest import doc_text, make_tenant

DIM = 768


class CountingEmbeddings(Embeddings):
    """Wraps the fake embedder and counts how many texts were embedded."""

    def __init__(self):
        self.inner, self.documents = HashingEmbeddings(DIM), 0

    def embed_documents(self, texts):
        self.documents += len(texts)
        return self.inner.embed_documents(texts)

    def embed_query(self, t):
        return self.inner.embed_query(t)


def client(name="fake:test-a", inner=None):
    return EmbeddingClient(inner=inner or CountingEmbeddings(), name=name, dim=DIM)


def counts(tenant="qspiders_sample"):
    with session_scope() as db:
        return (db.scalar(select(func.count()).select_from(Document).where(Document.tenant_id == tenant)),
                db.scalar(select(func.count()).select_from(Chunk).where(Chunk.tenant_id == tenant)))


@pytest.fixture
def tenants_copy(tmp_path):
    """A private copy of the real tenants folder, so a test can edit or delete docs without touching the repo."""
    dest = tmp_path / "tenants"
    shutil.copytree(TENANTS_DIR, dest)
    return dest


def run(embedder, tenants_dir=TENANTS_DIR, tenant="qspiders_sample", **kw):
    with session_scope() as db:
        return ingest_tenant(db, tenant, embedder, tenants_dir=tenants_dir, **kw)


def test_first_ingest_adds_every_document_with_public_audience_and_the_model_name():
    r = run(client())
    assert len(r.added) == 8 and not r.updated and not r.unchanged and r.chunks_written >= 6 and r.changed
    with session_scope() as db:
        rows = db.execute(select(Chunk)).scalars().all()
        assert {c.audience for c in rows} == {"public"} and {c.embedding_model for c in rows} == {"fake:test-a"}
        assert {c.tenant_id for c in rows} == {"qspiders_sample"} and all(len(c.embedding) == DIM for c in rows)
        assert {d.audience for d in db.execute(select(Document)).scalars()} == {"public"}


def test_second_ingest_is_a_no_op_and_embeds_nothing():
    emb = client()
    run(emb)
    before, calls = counts(), emb.inner.documents
    r = run(emb)
    assert len(r.unchanged) == 8 and not r.changed and r.chunks_written == 0
    assert emb.inner.documents == calls and counts() == before  # no embedding calls, no row changes


def test_editing_one_document_re_embeds_only_that_document(tenants_copy):
    emb = client()
    run(emb, tenants_copy)
    first = emb.inner.documents
    path = tenants_copy / "qspiders_sample" / "docs" / "admissions-and-demo-classes.md"
    path.write_text(path.read_text() + "\n## Gift vouchers\nGift vouchers are accepted at the front desk.\n")
    r = run(emb, tenants_copy)
    assert r.updated == ["admissions-and-demo-classes"] and len(r.unchanged) == 7 and not r.added
    assert 0 < emb.inner.documents - first <= 10  # only that article's passages were embedded
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.text.like("%Gift vouchers are accepted%"))) == 1


def test_changing_the_embedding_model_re_embeds_everything_and_leaves_no_stale_vectors():
    run(client("fake:model-a"))
    r = run(client("fake:model-b"))
    assert len(r.updated) == 8 and not r.unchanged
    with session_scope() as db:
        assert {m for (m,) in db.execute(select(Chunk.embedding_model).distinct())} == {"fake:model-b"}


def test_changing_the_chunk_size_re_chunks_even_though_the_files_did_not_change():
    emb = client()
    run(emb)
    _, before = counts()
    r = run(emb, max_chars=300)
    assert len(r.updated) >= 1 and counts()[1] > before


def test_force_re_embeds_everything():
    emb = client()
    run(emb)
    assert len(run(emb, force=True).updated) == 8


def test_deleting_a_document_withdraws_it_and_its_passages(tenants_copy):
    run(client(), tenants_copy)
    (tenants_copy / "qspiders_sample" / "docs" / "placement-and-career-support.md").unlink()
    r = run(client(), tenants_copy)
    assert r.removed == ["placement-and-career-support"]
    with session_scope() as db:
        assert db.get(Document, ("qspiders_sample", "placement-and-career-support")) is None
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.doc_id == "placement-and-career-support")) == 0


def test_a_shorter_article_leaves_no_orphan_passages(tenants_copy):
    emb = client()
    run(emb, tenants_copy)
    path = tenants_copy / "qspiders_sample" / "docs" / "fees-and-payments.md"
    path.write_text(doc_text("fees-and-payments", "Fees and Payments", "## Only section\nShort now."))
    run(emb, tenants_copy)
    with session_scope() as db:
        rows = db.execute(select(Chunk).where(Chunk.doc_id == "fees-and-payments")).scalars().all()
        assert [c.text for c in rows] == ["Short now."]


def test_ingesting_one_tenant_never_touches_another_tenants_rows(tenants_copy):
    make_tenant(tenants_copy, "other_institute", {"faq.md": doc_text("faq", "Other FAQ", "## Hours\nWe are open daily.")})
    run(client(), tenants_copy, "qspiders_sample")
    run(client(), tenants_copy, "other_institute")
    other_before = counts("other_institute")
    for f in (tenants_copy / "qspiders_sample" / "docs").glob("*.md"):  # sample withdraws every document but one
        if f.stem != "fees-and-payments":
            f.unlink()
    r = run(client(), tenants_copy, "qspiders_sample")
    assert len(r.removed) == 7 and counts("other_institute") == other_before == (1, 1)


def test_two_tenants_may_use_the_same_doc_id(tenants_copy):
    make_tenant(tenants_copy, "other_institute", {"r.md": doc_text("fees-and-payments", "Other Fees", "## R\nSeven days only.")})
    run(client(), tenants_copy, "qspiders_sample")
    run(client(), tenants_copy, "other_institute")
    with session_scope() as db:
        titles = dict(db.execute(select(Document.tenant_id, Document.title).where(Document.doc_id == "fees-and-payments")).all())
    assert titles == {"qspiders_sample": "Fees and Payments (SAMPLE)", "other_institute": "Other Fees"}


def test_an_unknown_tenant_is_refused_with_the_fix_in_the_message(tenants_copy):
    make_tenant(tenants_copy, "ghost_institute", {"a.md": doc_text("a", "A", "## S\nx")})
    with pytest.raises(IngestError, match="make db-seed"):
        run(client(), tenants_copy, "ghost_institute")


def test_an_internal_document_in_docs_aborts_the_ingest_and_writes_nothing(tenants_copy):
    (tenants_copy / "qspiders_sample" / "docs" / "leaked.md").write_text(
        doc_text("leaked", "Refund approvals", "## Thresholds\nOver $500 needs a branch manager.", audience="internal"))
    with pytest.raises(InternalDocumentError):
        run(client(), tenants_copy)
    assert counts() == (0, 0)


def test_a_failing_embedding_service_leaves_nothing_half_written():
    class Flaky(Embeddings):
        def __init__(self):
            self.n = 0

        def embed_documents(self, texts):
            self.n += 1
            if self.n == 3:
                raise ConnectionError("embedding server went away")
            return HashingEmbeddings(DIM).embed_documents(texts)

        def embed_query(self, t):
            return []

    with pytest.raises(ConnectionError):
        run(client(inner=Flaky()))
    assert counts() == (0, 0)  # embeddings are computed before any row is written


def test_no_staff_only_line_ever_lands_in_a_stored_passage():
    run(client())
    markers = internal_markers("qspiders_sample")
    assert markers, "the internal policy fixture should provide markers to look for"
    with session_scope() as db:
        stored = " ".join(" ".join(t.lower().split()) for (t,) in db.execute(select(Chunk.text)))
    assert not [m for m in markers if m in stored]
    assert "branch manager" not in stored and "internal" not in stored.replace("internal-", "")


def test_the_database_itself_refuses_an_internal_document_or_passage():
    run(client())
    with pytest.raises(IntegrityError), session_scope() as db:  # raises() outside: the failed session must roll back
        db.add(Document(tenant_id="qspiders_sample", doc_id="sneaky", title="x", audience="internal", source_path="p", content_hash="h"))
        db.flush()
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(Chunk(tenant_id="qspiders_sample", doc_id="fees-and-payments", chunk_index=99, heading="h", text="t", token_count=1,
                     audience="internal", embedding_model="m", embedding=[0.0] * DIM))
        db.flush()


def test_a_passage_cannot_point_at_another_tenants_document():
    run(client())
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(Chunk(tenant_id="other_institute", doc_id="fees-and-payments", chunk_index=0, heading="h", text="t", token_count=1,
                     audience="public", embedding_model="m", embedding=[0.0] * DIM))
        db.flush()


def test_a_document_with_nothing_after_its_title_is_refused(tenants_copy):
    (tenants_copy / "qspiders_sample" / "docs" / "empty.md").write_text(doc_text("empty", "Empty", ""))
    with pytest.raises(IngestError, match="no content"):
        run(client(), tenants_copy)
