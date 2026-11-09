"""Knowledge workspace (migration 0011) on real PostgreSQL 16: additive/reversible migration with data preserved, constraints, and compare-and-set paths under
REAL concurrent transactions. Skipped unless SG_TEST_DATABASE_URL is set."""

import threading

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.models import KbChangeItem, KbDocPlacement, KbDocument, KbFolder, KbVersion
from app.db.seed import seed
from app.db.session import session_scope
from app.knowledge import changes, workspace
from app.knowledge.errors import Conflict
from tests.conftest import AS_OF
from tests.db.test_postgres import ADMIN_URL, _alembic, migrated, pg_url  # noqa: F401  (fixtures)
from tests.db.test_postgres_m6 import EMB, S, T, go_live, new_doc, race

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]
WS = {"kb_folders", "kb_doc_placement", "kb_change_requests", "kb_change_items"}


@pytest.fixture
def pg(migrated):  # noqa: F811
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    return migrated


def test_migration_0011_is_additive_reversible_and_keeps_m6_data(pg_url):  # noqa: F811
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0010")
    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('keep_me','keep','Asia/Kolkata',75,true)"))
        c.execute(text("INSERT INTO kb_documents (tenant_id, doc_id, title, category, tags, status, origin, latest_version, row_version, created_by, created_at, updated_at) "
                       "VALUES ('keep_me','a','A','faq','[]','draft','store',1,1,'x',now(),now())"))
        c.execute(text("INSERT INTO kb_versions (tenant_id, doc_id, version_no, title, content, checksum, size_bytes, change_note, created_by, created_at, status) "
                       "VALUES ('keep_me','a',1,'A','body','" + "0" * 64 + "',4,'n','x',now(),'ready')"))
    assert not WS & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "0011")
    assert WS <= set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.execute(text("SELECT content FROM kb_versions")).scalar() == "body"
    command.downgrade(cfg, "0010")
    assert not WS & set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_versions")).scalar() == 1 and c.execute(text("SELECT count(*) FROM kb_documents")).scalar() == 1
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_documents")).scalar() == 1
    engine.dispose()


def test_database_constraints_hold_on_postgres(pg):  # noqa: F811
    with session_scope() as db:
        f = workspace.create_folder(db, T, "admin1", "Fees")
    with pytest.raises(IntegrityError):                                   # sibling uniqueness is case-insensitive
        with session_scope() as db:
            db.add(KbFolder(tenant_id=T, folder_id="f_dup", parent_id=None, parent_key="", name="FEES", name_key="fees", row_version=0, created_by="x"))
    with pytest.raises(IntegrityError):                                   # a folder cannot be its own parent
        with session_scope() as db:
            db.add(KbFolder(tenant_id=T, folder_id="f_self", parent_id="f_self", parent_key="f_self", name="s", name_key="s", row_version=0, created_by="x"))
    with pytest.raises(IntegrityError):                                   # a folder of another tenant cannot be the parent (composite FK)
        with session_scope() as db:
            db.add(KbFolder(tenant_id="other_institute", folder_id="f_x", parent_id=f["folder_id"], parent_key=f["folder_id"], name="x", name_key="x", row_version=0, created_by="x"))
    new_doc()
    with pytest.raises(IntegrityError):                                   # a placement cannot point at another tenant's folder
        with session_scope() as db:
            db.add(KbDocPlacement(tenant_id=T, doc_id="fees-test", folder_id="f_" + "0" * 16, moved_by="x"))


def test_racing_folder_creators_get_exactly_one_folder_on_postgres(pg):  # noqa: F811
    def make(i):
        with session_scope() as db:
            return workspace.create_folder(db, T, f"u{i}", "Racing")["folder_id"]
    out, errs = race(8, make)
    assert len(out) == 1 and len(errs) == 7 and all(isinstance(e, Conflict) for e in errs), errs


def test_racing_document_moves_serialize_through_the_row_version_on_postgres(pg):  # noqa: F811
    new_doc()
    with session_scope() as db:
        fids = [workspace.create_folder(db, T, "a", f"F{i}")["folder_id"] for i in range(6)]
        rv = db.get(KbDocument, (T, "fees-test")).row_version

    def move(i):
        with session_scope() as db:
            return workspace.move_document(db, T, f"u{i}", "fees-test", fids[i], expected_row_version=rv)["folder_id"]
    out, errs = race(6, move)
    assert len(out) == 1 and len(errs) == 5 and all(isinstance(e, Conflict) for e in errs), (out, errs)
    with session_scope() as db:
        assert db.get(KbDocPlacement, (T, "fees-test")).folder_id == out[0]


def test_racing_saves_with_one_token_have_a_single_winner_and_the_published_text_is_untouched(pg):  # noqa: F811
    new_doc()
    go_live()
    with session_scope() as db:
        view, _ = changes.start(db, T, "staff1", S, doc_id="fees-test")
    rid = view["request_id"]
    base = "# Fees\n\nStudents may pay the programme fee in {n} instalments. The first instalment is due at enrolment.\n"

    def save(i):
        with session_scope() as db:
            changes.save_item(db, T, "staff1", S, rid, "fees-test", content=base.format(n=f"racer{i}"), expected_row_version=0)
        return i
    out, errs = race(6, save)
    assert len(out) == 1 and len(errs) == 5 and all(isinstance(e, Conflict) for e in errs), (out, errs)
    with session_scope() as db:
        it = db.get(KbChangeItem, (T, rid, "fees-test"))
        assert f"racer{out[0]}" in it.proposed_content and it.row_version == 1
        d = db.get(KbDocument, (T, "fees-test"))
        assert d.published_version == 1 and d.latest_version == 1
        assert "two instalments" in db.get(KbVersion, (T, "fees-test", 1)).content


def test_racing_submits_and_applies_happen_once(pg):  # noqa: F811
    new_doc()
    go_live()
    with session_scope() as db:
        view, _ = changes.start(db, T, "staff1", S, doc_id="fees-test")
        rid = view["request_id"]
        changes.save_item(db, T, "staff1", S, rid, "fees-test", content="# Fees\n\nStudents may pay the programme fee in three instalments, due at enrolment.\n", expected_row_version=0)

    def sub(i):
        with session_scope() as db:
            return changes.submit(db, T, "staff1", rid, description="three instalments")["status"]
    out, errs = race(4, sub)
    assert out == ["submitted"] and len(errs) == 3
    with session_scope() as db:
        changes.review(db, T, "admin1", S, rid, "approve")

    def app(i):
        with session_scope() as db:
            return changes.apply(db, T, f"admin{i}", S, rid)["status"]
    out, errs = race(4, app)
    assert out == ["applied"] and len(errs) == 3, errs
    with session_scope() as db:
        assert [v.version_no for v in db.execute(select(KbVersion).where(KbVersion.doc_id == "fees-test").order_by(KbVersion.version_no)).scalars()] == [1, 2]
