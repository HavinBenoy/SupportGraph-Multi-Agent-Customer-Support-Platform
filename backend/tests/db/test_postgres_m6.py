"""M6 knowledge store on real PostgreSQL 16: migration 0009 (additive, reversible, data preserved), its constraints, and the compare-and-set paths under REAL
concurrent transactions (versions, publish, proposals). Skipped unless SG_TEST_DATABASE_URL is set."""

import threading
from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.models import Call, Chunk, Document, InterventionFeedback, KbDocument, KbProposal, KbVersion, KbVersionChunk
from app.db.seed import seed
from app.db.session import session_scope
from app.knowledge import pipeline, proposals, store
from app.knowledge.errors import KbError
from app.llm.gateway import get_embedding_client
from app.rag.retrieval import search
from tests.conftest import AS_OF
from tests.knowledge import fencing
from tests.db.test_postgres import ADMIN_URL, _alembic, migrated, pg_url  # noqa: F401  (fixtures)

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]
T = "qspiders_sample"
KB = {"kb_documents", "kb_versions", "kb_version_chunks", "kb_proposals", "kb_events"}
S = Settings(_env_file=None, embedding_provider="fake", llm_provider="fake")
EMB = get_embedding_client(S)
BODY = "# Fees\n\nStudents may pay the programme fee in {n} instalments. The first instalment is due at enrolment.\n"


@pytest.fixture
def pg(migrated):  # noqa: F811
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    return migrated


def race(n, fn):
    gate, out, errs = threading.Barrier(n), [], []

    def go(i):
        gate.wait()
        try:
            out.append(fn(i))
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts_ = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    [t.start() for t in ts_]
    [t.join() for t in ts_]
    return out, errs


def new_doc(doc_id="fees-test", n="two"):
    with session_scope() as db:
        store.create_document(db, T, "admin1", S, doc_id=doc_id, title="Fees", category="fees", content=BODY.format(n=n))


def go_live(doc_id="fees-test", ver=1):
    pipeline.process_version(T, "admin1", doc_id, ver, EMB, S)
    with session_scope() as db:
        pipeline.publish_version(db, T, "admin1", doc_id, ver, EMB.name)


def test_migration_0009_upgrade_downgrade_and_re_upgrade_on_postgres_without_losing_data(pg_url):  # noqa: F811
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0008")
    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('keep_me','keep','Asia/Kolkata',75,true)"))
        c.execute(text("INSERT INTO documents (tenant_id, doc_id, title, audience, source_path, content_hash, last_reviewed) "
                       "VALUES ('keep_me','disk-doc','On disk','public','a.md','h1','2026-10-01')"))
        vec = "[" + ",".join(["0.01"] * 768) + "]"
        c.execute(text("INSERT INTO chunks (tenant_id, doc_id, chunk_index, heading, text, token_count, audience, embedding_model, embedding) "
                       f"VALUES ('keep_me','disk-doc',0,'H','existing passage',3,'public','fake:hashing-v1-768','{vec}')"))
    assert not KB & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "0009")
    insp = inspect(engine)
    assert KB <= set(insp.get_table_names()) and "kb_version" in {c["name"] for c in insp.get_columns("documents")}
    with engine.connect() as c:
        assert c.execute(text("SELECT kb_version FROM documents WHERE doc_id='disk-doc'")).scalar() is None
        assert c.execute(text("SELECT text FROM chunks WHERE doc_id='disk-doc'")).scalar() == "existing passage"
    command.downgrade(cfg, "0008")
    assert not KB & set(inspect(engine).get_table_names()) and "kb_version" not in {c["name"] for c in inspect(engine).get_columns("documents")}
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM chunks WHERE doc_id='disk-doc'")).scalar() == 1
    command.upgrade(cfg, "head")
    assert KB <= set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM documents WHERE doc_id='disk-doc'")).scalar() == 1
    engine.dispose()


def test_postgres_enforces_the_knowledge_constraints_and_vector_staging(pg):
    new_doc()
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(KbDocument(tenant_id=T, doc_id="bad", title="b", category="fees", status="published", published_version=None, latest_version=1, row_version=1, created_by="x"))
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(KbDocument(tenant_id=T, doc_id="bad2", title="b", category="nonsense", status="draft", latest_version=1, row_version=1, created_by="x"))
    pipeline.process_version(T, "admin1", "fees-test", 1, EMB, S)
    with session_scope() as db:
        row = db.execute(select(KbVersionChunk)).scalars().first()
        assert len(row.embedding) == 768
        assert db.execute(text("SELECT vector_dims(embedding) FROM kb_version_chunks LIMIT 1")).scalar() == 768
    assert inspect(pg).get_indexes("kb_versions")


def test_publish_end_to_end_on_postgres_uses_the_existing_pgvector_search(pg):
    new_doc()
    go_live()
    q = EMB.embed_query("programme fee two instalments")
    with session_scope() as db:
        got = search(db, tenant_id=T, query_vector=q, model_name=EMB.name, top_k=5)
        mine = [c for c in got if c.doc_id == "fees-test"]
        assert mine and all(c.doc_version == 1 for c in mine)
        assert search(db, tenant_id="other_institute", query_vector=q, model_name=EMB.name, top_k=20).__class__ is list
        assert not [c for c in search(db, tenant_id="other_institute", query_vector=q, model_name=EMB.name, top_k=50) if c.doc_id == "fees-test"]


def test_concurrent_new_versions_get_distinct_numbers_on_postgres(pg):
    new_doc()

    def add(i):
        with session_scope() as db:
            return store.create_version(db, T, f"w{i}", S, "fees-test", content=BODY.format(n=f"number-{i}-here"), change_note=f"c{i}").version_no

    out, errs = race(6, add)
    assert not errs, errs
    assert sorted(out) == [2, 3, 4, 5, 6, 7]
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(KbVersion).where(KbVersion.doc_id == "fees-test")) == 7


def test_concurrent_publishes_of_different_versions_leave_exactly_one_live_version_on_postgres(pg):
    new_doc()
    go_live()
    for i, w in ((2, "three"), (3, "four")):
        with session_scope() as db:
            store.create_version(db, T, "admin1", S, "fees-test", content=BODY.format(n=w), change_note=f"v{i}")
        pipeline.process_version(T, "admin1", "fees-test", i, EMB, S)

    def pub(i):
        with session_scope() as db:
            return pipeline.publish_version(db, T, f"a{i}", "fees-test", i + 2, EMB.name)

    out, errs = race(2, pub)
    assert out and all(isinstance(e, KbError) for e in errs), errs
    with session_scope() as db:
        d = db.get(KbDocument, (T, "fees-test"))
        live = db.get(Document, (T, "fees-test"))
        published = db.scalars(select(KbVersion.version_no).where(KbVersion.doc_id == "fees-test", KbVersion.status == "published")).all()
        chunks = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.doc_id == "fees-test"))
        assert published == [d.published_version] and live.kb_version == d.published_version
        assert chunks == db.get(KbVersion, (T, "fees-test", d.published_version)).chunk_count


def test_the_same_version_published_by_many_admins_at_once_is_applied_once_on_postgres(pg):
    new_doc()
    pipeline.process_version(T, "admin1", "fees-test", 1, EMB, S)

    def pub(i):
        with session_scope() as db:
            return pipeline.publish_version(db, T, f"a{i}", "fees-test", 1, EMB.name)

    out, errs = race(5, pub)
    assert out and all(isinstance(e, KbError) for e in errs), errs
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.doc_id == "fees-test")) == db.get(KbVersion, (T, "fees-test", 1)).chunk_count
        assert db.scalar(select(func.count()).select_from(Document).where(Document.doc_id == "fees-test")) == 1


def test_concurrent_processing_of_one_version_produces_one_consistent_staged_set_on_postgres(pg):
    new_doc()

    def proc(i):
        return pipeline.process_version(T, "admin1", "fees-test", 1, EMB, S)["status"]

    out, errs = race(4, proc)
    assert not [e for e in errs if not isinstance(e, KbError)], errs
    with session_scope() as db:
        v = db.get(KbVersion, (T, "fees-test", 1))
        n = db.scalar(select(func.count()).select_from(KbVersionChunk).where(KbVersionChunk.doc_id == "fees-test"))
        assert v.status == "ready" and n == v.chunk_count


def test_concurrent_proposals_for_one_feedback_create_exactly_one_on_postgres(pg):
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        db.add(Call(tenant_id=T, id="c1", ticket_id="tk1", status="ended", owner="none", epoch=2, created_at=now, intervention_owner="staff1", takeover_at=now, ended_at=now))
        db.flush()
        db.add(InterventionFeedback(tenant_id=T, call_id="c1", staff_subject="staff1", intervened_at=now, state="submitted", category="incorrect_information",
                                    what_was_wrong="wrong", suggested_correction="fix", submitted_at=now, review_status="approved_for_update", reviewed_by="admin1", reviewed_at=now))

    def prop(i):
        with session_scope() as db:
            return proposals.create_proposal(db, T, f"admin{i}", S, "c1", content=BODY.format(n="five"), new_document={"doc_id": f"d{i}", "title": "T", "category": "fees"})

    out, errs = race(5, prop)
    assert len(out) == 1 and len(errs) == 4 and all(isinstance(e, KbError) for e in errs), errs
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(KbProposal)) == 1 and db.scalar(select(func.count()).select_from(KbDocument)) == 1


def test_a_publish_failure_rolls_back_the_live_switch_on_postgres(pg, monkeypatch):
    new_doc()
    go_live()
    with session_scope() as db:
        store.create_version(db, T, "admin1", S, "fees-test", content=BODY.format(n="six"), change_note="v2")
    pipeline.process_version(T, "admin1", "fees-test", 2, EMB, S)
    monkeypatch.setattr(pipeline, "_drop_staged", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError), session_scope() as db:
        pipeline.publish_version(db, T, "admin1", "fees-test", 2, EMB.name)
    monkeypatch.undo()
    with session_scope() as db:
        assert db.get(KbDocument, (T, "fees-test")).published_version == 1 and db.get(Document, (T, "fees-test")).kb_version == 1
        assert "two instalments" in " ".join(db.scalars(select(Chunk.text).where(Chunk.doc_id == "fees-test")).all())


@pytest.mark.parametrize("case", fencing.ALL, ids=lambda f: f.__name__)
def test_processing_ownership_fencing_on_postgres(pg, case):
    """The same deterministic scenarios as tests/knowledge/test_processing_fencing.py, on real PostgreSQL 16 (row locks, READ COMMITTED re-checks)."""
    case()


def test_migration_0010_on_postgres_adds_the_attempt_column_with_a_default_and_reverses(pg_url):  # noqa: F811
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0009")
    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('keep_me','keep','Asia/Kolkata',75,true)"))
        c.execute(text("INSERT INTO kb_documents (tenant_id, doc_id, title, category, tags, status, origin, latest_version, row_version, created_by, created_at, updated_at) "
                       "VALUES ('keep_me','a','A','faq','[]','draft','store',1,1,'x',now(),now())"))
        c.execute(text("INSERT INTO kb_versions (tenant_id, doc_id, version_no, title, content, checksum, size_bytes, change_note, created_by, created_at, status) "
                       "VALUES ('keep_me','a',1,'A','body','" + "0" * 64 + "',4,'n','x',now(),'ready')"))
    command.upgrade(cfg, "0010")
    with engine.connect() as c:
        assert c.execute(text("SELECT processing_attempt, content FROM kb_versions")).one() == (0, "body")
    command.downgrade(cfg, "0009")
    assert "processing_attempt" not in {c["name"] for c in inspect(engine).get_columns("kb_versions")}
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_versions")).scalar() == 1
    engine.dispose()
