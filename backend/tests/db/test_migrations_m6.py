"""Migration 0009 (knowledge store) on SQLite, from a database at revision 0008: additive, reversible, existing data untouched, constraints enforced.
The same steps on PostgreSQL 16 are in tests/db/test_postgres_m6.py."""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from tests.db.test_migrations_m4_1_m5 import cfg_for

KB = {"kb_documents", "kb_versions", "kb_version_chunks", "kb_proposals", "kb_events"}


@pytest.fixture
def at_0008(tmp_path):
    """A SQLite database shaped like revision 0008 (the M5 release) holding a tenant, a disk-ingested document with a chunk, and a call."""
    url = f"sqlite:///{tmp_path / 'm6.db'}"
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(text("PRAGMA foreign_keys=OFF"))
        for t in ("kb_change_items", "kb_change_requests", "kb_doc_placement", "kb_folders", "kb_events", "kb_proposals", "kb_version_chunks", "kb_versions", "kb_documents"):
            c.execute(text(f"DROP TABLE {t}"))
        c.execute(text("ALTER TABLE documents DROP COLUMN kb_version"))
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('keep_me','keep','Asia/Kolkata',75,1)"))
        c.execute(text("INSERT INTO documents (tenant_id, doc_id, title, audience, source_path, content_hash, last_reviewed) "
                       "VALUES ('keep_me','disk-doc','On disk','public','tenants/keep_me/docs/a.md','h1','2026-10-01')"))
        c.execute(text("INSERT INTO chunks (tenant_id, doc_id, chunk_index, heading, text, token_count, audience, embedding_model, embedding) "
                       "VALUES ('keep_me','disk-doc',0,'H','existing passage',3,'public','fake:hashing-v1-768','[0.1,0.2]')"))
        c.execute(text("INSERT INTO calls (tenant_id, id, ticket_id, status, owner, epoch, created_at) VALUES ('keep_me','c_old','t1','ended','none',2,'2026-10-01 10:00:00')"))
    cfg = cfg_for(url)
    command.stamp(cfg, "0008")
    yield url, cfg, eng
    eng.dispose()


def counts(eng):
    with eng.connect() as c:
        return tuple(c.execute(text(q)).scalar() for q in ("SELECT count(*) FROM tenants", "SELECT count(*) FROM documents", "SELECT count(*) FROM chunks", "SELECT count(*) FROM calls"))


def test_0009_upgrades_downgrades_and_re_upgrades_without_touching_existing_data(at_0008):
    url, cfg, eng = at_0008
    assert not KB & set(inspect(eng).get_table_names()) and "kb_version" not in {c["name"] for c in inspect(eng).get_columns("documents")}
    command.upgrade(cfg, "0009")
    insp = inspect(eng)
    assert KB <= set(insp.get_table_names()) and "kb_version" in {c["name"] for c in insp.get_columns("documents")}
    with eng.connect() as c:
        assert c.execute(text("SELECT kb_version FROM documents WHERE doc_id='disk-doc'")).scalar() is None            # disk articles are unmanaged: nothing rewritten
        assert c.execute(text("SELECT text FROM chunks WHERE doc_id='disk-doc'")).scalar() == "existing passage"
        assert c.execute(text("SELECT count(*) FROM kb_documents")).scalar() == 0
    assert counts(eng) == (1, 1, 1, 1)
    command.downgrade(cfg, "0008")
    insp = inspect(eng)
    assert not KB & set(insp.get_table_names()) and "kb_version" not in {c["name"] for c in insp.get_columns("documents")}
    assert counts(eng) == (1, 1, 1, 1)
    with eng.connect() as c:
        assert c.execute(text("SELECT text FROM chunks WHERE doc_id='disk-doc'")).scalar() == "existing passage"
    command.upgrade(cfg, "head")
    assert KB <= set(inspect(eng).get_table_names()) and counts(eng) == (1, 1, 1, 1)


def test_0012_is_the_head_and_the_chain_is_unbroken(at_0008):
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(at_0008[1])
    assert script.get_current_head() == "0012" and script.get_revision("0012").down_revision == "0011" and script.get_revision("0011").down_revision == "0010" and script.get_revision("0010").down_revision == "0009" and script.get_revision("0009").down_revision == "0008"


def test_the_knowledge_tables_enforce_their_rules(at_0008):
    url, cfg, eng = at_0008
    command.upgrade(cfg, "head")

    def run(sql):
        with eng.begin() as c:
            c.execute(text("PRAGMA foreign_keys=ON"))
            c.execute(text(sql))

    doc = "INSERT INTO kb_documents (tenant_id, doc_id, title, category, tags, status, origin, latest_version, published_version, row_version, created_by, created_at, updated_at) VALUES "
    run(doc + "('keep_me','a','A','faq','[]','draft','store',1,NULL,1,'x','2026-11-01','2026-11-01')")
    for bad in ("('keep_me','b','B','nonsense','[]','draft','store',1,NULL,1,'x','2026-11-01','2026-11-01')",                       # category
                "('keep_me','c','C','faq','[]','weird','store',1,NULL,1,'x','2026-11-01','2026-11-01')",                           # status
                "('keep_me','d','D','faq','[]','published','store',1,NULL,1,'x','2026-11-01','2026-11-01')",                       # published without a version
                "('keep_me','e','E','faq','[]','draft','store',1,1,1,'x','2026-11-01','2026-11-01')",                              # a version without being published
                "('keep_me','a','dup','faq','[]','draft','store',1,NULL,1,'x','2026-11-01','2026-11-01')",                         # duplicate id
                "('ghost','f','F','faq','[]','draft','store',1,NULL,1,'x','2026-11-01','2026-11-01')"):                            # unknown tenant
        with pytest.raises(IntegrityError):
            run(doc + bad)
    ver = ("INSERT INTO kb_versions (tenant_id, doc_id, version_no, title, content, checksum, size_bytes, change_note, created_by, created_at, status) VALUES ")
    run(ver + "('keep_me','a',1,'A','body','" + "0" * 64 + "',4,'n','x','2026-11-01','draft')")
    with pytest.raises(IntegrityError):
        run(ver + "('keep_me','a',1,'A','body','" + "0" * 64 + "',4,'n','x','2026-11-01','draft')")                                  # same version number twice
    with pytest.raises(IntegrityError):
        run(ver + "('keep_me','a',2,'A','body','" + "0" * 64 + "',4,'n','x','2026-11-01','bogus')")
    with pytest.raises(IntegrityError):
        run(ver + "('keep_me','a',0,'A','body','" + "0" * 64 + "',4,'n','x','2026-11-01','draft')")
    with pytest.raises(IntegrityError):
        run(ver + "('keep_me','nodoc',1,'A','body','" + "0" * 64 + "',4,'n','x','2026-11-01','draft')")                           # version without a document
    ins = "INSERT INTO kb_events (tenant_id, actor, action, resource_type, resource_id, summary, created_at) VALUES ('keep_me','a','document_created','document','a','{}','2026-11-01')"
    run(ins)
    prop = "INSERT INTO kb_proposals (tenant_id, call_id, doc_id, version_no, status, proposed_by, proposed_at) VALUES "
    with pytest.raises(IntegrityError):
        run(prop + "('keep_me','no-such-call','a',1,'proposed','x','2026-11-01')")                                                 # must reference real feedback


def test_0010_adds_the_processing_attempt_column_keeps_existing_versions_and_reverses(at_0008):
    url, cfg, eng = at_0008
    command.upgrade(cfg, "0009")
    with eng.begin() as c:
        c.execute(text("INSERT INTO kb_documents (tenant_id, doc_id, title, category, tags, status, origin, latest_version, row_version, created_by, created_at, updated_at) "
                       "VALUES ('keep_me','a','A','faq','[]','draft','store',1,1,'x','2026-11-01','2026-11-01')"))
        c.execute(text("INSERT INTO kb_versions (tenant_id, doc_id, version_no, title, content, checksum, size_bytes, change_note, created_by, created_at, status) "
                       "VALUES ('keep_me','a',1,'A','body','" + "0" * 64 + "',4,'n','x','2026-11-01','ready')"))
    assert "processing_attempt" not in {c["name"] for c in inspect(eng).get_columns("kb_versions")}
    command.upgrade(cfg, "0010")
    assert "processing_attempt" in {c["name"] for c in inspect(eng).get_columns("kb_versions")}
    with eng.connect() as c:
        assert c.execute(text("SELECT processing_attempt, status, content FROM kb_versions")).one() == (0, "ready", "body")          # existing rows read 0, nothing rewritten
    command.downgrade(cfg, "0009")
    assert "processing_attempt" not in {c["name"] for c in inspect(eng).get_columns("kb_versions")}
    with eng.connect() as c:
        assert c.execute(text("SELECT status, content FROM kb_versions")).one() == ("ready", "body")
    command.upgrade(cfg, "head")
    assert counts(eng) == (1, 1, 1, 1)


def test_0012_widens_the_request_state_machine_keeps_part1_rows_and_reverses_by_mapping_states_back(at_0008):
    url, cfg, eng = at_0008
    command.upgrade(cfg, "0011")
    with eng.begin() as c:
        c.execute(text("INSERT INTO kb_documents (tenant_id, doc_id, title, category, tags, status, origin, latest_version, published_version, row_version, created_by, created_at, updated_at) "
                       "VALUES ('keep_me','a','A','faq','[]','published','store',1,1,1,'x','2026-11-01','2026-11-01')"))
        c.execute(text("INSERT INTO kb_versions (tenant_id, doc_id, version_no, title, content, checksum, size_bytes, change_note, created_by, created_at, status) "
                       "VALUES ('keep_me','a',1,'A','body','" + "0" * 64 + "',4,'n','x','2026-11-01','published')"))
        for rid, st in (("cr_1", "submitted"), ("cr_2", "applied")):
            c.execute(text(f"INSERT INTO kb_change_requests (tenant_id, request_id, status, source, row_version, created_by, created_at) VALUES ('keep_me','{rid}','{st}','manual',0,'staff1','2026-11-01')"))
        c.execute(text("INSERT INTO kb_change_items (tenant_id, request_id, doc_id, base_version, base_checksum, proposed_title, proposed_content, proposed_checksum, diff, stats, origin, row_version, created_at) "
                       "VALUES ('keep_me','cr_1','a',1,'" + "0" * 64 + "','A','body2','" + "1" * 64 + "','{}','{}','manual',0,'2026-11-01')"))
    cols = lambda t: {c["name"] for c in inspect(eng).get_columns(t)}                       # noqa: E731
    assert "analysis" not in cols("kb_change_requests")
    command.upgrade(cfg, "0012")
    assert {"analysis", "release", "release_attempt", "analysis_attempt", "return_note"} <= cols("kb_change_requests")
    assert {"ai_suggestion", "final_edits", "admin_decision", "process_status", "publish_status", "classification"} <= cols("kb_change_items")
    with eng.connect() as c:
        assert c.execute(text("SELECT request_id, status, release_attempt FROM kb_change_requests ORDER BY request_id")).all() == [("cr_1", "submitted", 0), ("cr_2", "applied", 0)]
        row = c.execute(text("SELECT proposed_content, admin_edited, ai_suggestion FROM kb_change_items")).one()
        assert row[0] == "body2" and not row[1] and row[2] is None
    def run(sql):
        with eng.begin() as c:
            c.execute(text("PRAGMA foreign_keys=ON"))
            c.execute(text(sql))
    ins = "INSERT INTO kb_change_requests (tenant_id, request_id, status, source, row_version, created_by, created_at) VALUES ('keep_me','%s','%s','ai_assisted',0,'staff1','2026-11-01')"
    for i, st in enumerate(("draft", "analysing", "awaiting_clarification", "awaiting_decisions", "under_review", "needs_revision", "processing", "publishing", "published", "failed")):
        run(ins % (f"new{i}", st))
    with pytest.raises(IntegrityError):
        run(ins % ("bad", "bogus"))
    with pytest.raises(IntegrityError):                                                                        # the new item checks hold too
        run("UPDATE kb_change_items SET admin_decision='maybe'")
    with pytest.raises(IntegrityError):
        run("UPDATE kb_change_items SET publish_status='half'")
    command.downgrade(cfg, "0011")
    assert "analysis" not in cols("kb_change_requests") and "ai_suggestion" not in cols("kb_change_items")
    with eng.connect() as c:
        got = dict(c.execute(text("SELECT request_id, status FROM kb_change_requests")).all())
        assert got["cr_1"] == "submitted" and got["cr_2"] == "applied" and got["new1"] == "draft" and got["new4"] == "submitted" and got["new6"] == "approved" and got["new8"] == "applied"
        assert c.execute(text("SELECT proposed_content FROM kb_change_items")).scalar() == "body2"
        assert c.execute(text("SELECT content FROM kb_versions")).scalar() == "body"
    command.upgrade(cfg, "head")
    assert "analysis" in cols("kb_change_requests")
