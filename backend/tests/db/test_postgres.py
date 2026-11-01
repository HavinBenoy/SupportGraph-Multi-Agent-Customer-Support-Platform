"""Real-Postgres checks: migrations, constraints, and the tools running against the actual schema.

Skipped unless SG_TEST_DATABASE_URL points at a Postgres server where the user may CREATE DATABASE, e.g.
    SG_TEST_DATABASE_URL=postgresql+psycopg://supportgraph:supportgraph@localhost:5432/postgres make test-pg
Each test module gets its own throwaway database, so your dev data is never touched.
"""

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.models import AssessmentResult, Enrollment
from app.db.seed import seed
from app.db.session import configure_engine, session_scope
from tests.conftest import AS_OF, scope_cfg

ADMIN_URL = os.environ.get("SG_TEST_DATABASE_URL")
pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]

BACKEND = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {"tenants", "students", "courses", "batches", "enrollments", "class_sessions", "attendance_records",
                   "assessments", "assessment_results", "documents", "chunks"}
RETAIL_TABLES = {"customers", "orders", "order_items"}


def _alembic(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


@pytest.fixture(scope="module")
def pg_url():
    name = f"sg_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    yield url
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def migrated(pg_url):
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = configure_engine(pg_url)
    yield engine


def test_migration_upgrades_and_downgrades_cleanly(pg_url):
    cfg = _alembic(pg_url)
    command.upgrade(cfg, "head")
    engine = create_engine(pg_url)
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables and not (RETAIL_TABLES & tables)
    command.downgrade(cfg, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(cfg, "head")  # and up again
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


ZERO_VECTOR = "array_fill(0::real, ARRAY[768])::vector"


def test_upgrade_from_the_retail_schema_replaces_demo_data_and_keeps_other_tenants(pg_url):
    """The real upgrade path: a database that is at revision 0002 with retail demo rows."""
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0002")
    engine = create_engine(pg_url)
    with engine.begin() as c:
        for t in ("acme_store", "other_store", "keep_me"):
            c.execute(text("INSERT INTO tenants (id, name, currency, return_window_days) VALUES (:t, :t, 'USD', 30)"), {"t": t})
        c.execute(text("INSERT INTO customers (tenant_id, id, name, email) VALUES ('acme_store','c1','n','e')"))
        for t in ("acme_store", "keep_me"):
            c.execute(text("INSERT INTO documents (tenant_id, doc_id, title, audience, source_path, content_hash) "
                           "VALUES (:t, 'd1', 't', 'customer', 'docs/d1.md', 'h')"), {"t": t})
            c.execute(text("INSERT INTO chunks (tenant_id, doc_id, chunk_index, heading, text, token_count, audience, "
                           f"embedding_model, embedding) VALUES (:t, 'd1', 0, 'h', 'x', 1, 'customer', 'm', {ZERO_VECTOR})"),
                      {"t": t})
    command.upgrade(cfg, "head")

    with engine.connect() as c:
        assert {r[0] for r in c.execute(text("SELECT id FROM tenants"))} == {"keep_me"}   # only the known demo tenants went
        assert c.execute(text("SELECT timezone, attendance_threshold_pct, is_sample FROM tenants")).one() == ("Asia/Kolkata", 75, True)
        assert c.execute(text("SELECT tenant_id, audience FROM documents")).all() == [("keep_me", "public")]
        assert c.execute(text("SELECT tenant_id, audience FROM chunks")).all() == [("keep_me", "public")]
    with pytest.raises(IntegrityError), engine.begin() as c:   # the new CHECK is live
        c.execute(text("UPDATE documents SET audience = 'internal'"))
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text("UPDATE chunks SET audience = 'customer'"))   # the OLD value is no longer allowed either

    command.downgrade(cfg, "0002")     # structure comes back (empty), documents regain the old audience value
    assert RETAIL_TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.execute(text("SELECT audience FROM documents")).scalar() == "customer"
        assert c.execute(text("SELECT currency, return_window_days FROM tenants")).one() == ("USD", 30)
    command.upgrade(cfg, "head")
    engine.dispose()


def test_models_and_migrations_are_in_sync(migrated):
    """If someone edits models.py without writing a migration, this fails."""
    with migrated.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"models differ from migrations: {diff}"


def test_every_foreign_key_includes_tenant_id_except_the_tenant_root(migrated):
    insp = inspect(migrated)
    for table in EXPECTED_TABLES - {"tenants"}:
        fks = insp.get_foreign_keys(table)
        assert fks, table
        assert all("tenant_id" in fk["constrained_columns"] for fk in fks), table


def test_postgres_rejects_cross_tenant_reference_and_bad_rows(migrated):
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    base = dict(tenant_id="qspiders_sample", id="E-9999", student_id="stu_asha", batch_id="PYF-101",
                status="active", enrolled_on=AS_OF)
    for override in (dict(student_id="stu_zed"),                   # other tenant's student
                     dict(batch_id="OTH-900"),                      # other tenant's batch
                     dict(status="exploded")):
        with pytest.raises(IntegrityError), session_scope() as db:
            db.add(Enrollment(**{**base, **override}))
    with pytest.raises(IntegrityError), session_scope() as db:      # graded without a score
        db.add(AssessmentResult(tenant_id="qspiders_sample", assessment_id="ASM-3", enrollment_id="E-1001",
                                status="graded", score=None))


def test_boolean_attendance_sums_correctly_on_postgres(migrated):
    from app.db import repo
    from app.tenants.scope import Scope

    with session_scope() as db:
        seed(db, as_of=AS_OF)
        assert repo.attendance_counts(db, Scope("qspiders_sample", "stu_asha", AS_OF), "E-1001") == (10, 8)
        assert repo.attendance_counts(db, Scope("qspiders_sample", "stu_rahul", AS_OF), "E-1001") == (0, 0)  # not Rahul's


def test_tools_and_graph_work_against_postgres(migrated):
    from langchain_core.messages import HumanMessage

    from app.graph.builder import build_graph
    from app.tools.read_tools import get_my_attendance, get_my_batches, get_my_schedule, list_my_assessments

    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    asha = scope_cfg("qspiders_sample", "stu_asha")
    assert get_my_attendance.invoke({"batch_id": "PYF-101"}, asha)["attendance_pct"] == 80.0
    assert get_my_attendance.invoke({"batch_id": "QAT-201"}, scope_cfg("qspiders_sample", "stu_sneha"))["below_threshold"] is False
    assert [u["date"] for u in get_my_schedule.invoke({"batch_id": "PYF-101"}, asha)["upcoming"]][0] == "2026-10-25"
    assert list_my_assessments.invoke({"batch_id": "PYF-101"}, asha)["assessments"][0]["score"] == 17
    # isolation on the real engine: same student id and batch id, two tenants, each sees only its own
    other = scope_cfg("other_institute", "stu_asha")
    assert get_my_batches.invoke({}, asha)["batches"][0]["course"].startswith("Python Full Stack")
    assert get_my_batches.invoke({}, other)["batches"][0]["course"] == "SECRET OTHER-TENANT COURSE"
    assert get_my_attendance.invoke({"batch_id": "QAT-201"}, asha) == {"error": "Batch not found for this account."}
    result = build_graph().invoke({"messages": [HumanMessage("What is my attendance in PYF-101?")]}, asha)
    assert "80.0%" in result["messages"][-1].content


def test_pgvector_extension_is_installed_after_migration(migrated):
    with migrated.connect() as c:
        assert c.execute(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")).scalar() == 1
