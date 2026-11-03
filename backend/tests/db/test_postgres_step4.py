"""Build step 4 on real Postgres: migration 0004, constraints, the Postgres checkpointer (a pause that survives a "restart"),
and concurrent decisions. Skipped unless SG_TEST_DATABASE_URL is set (same setup as tests/db/test_postgres.py)."""

import threading

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import write_repo
from app.db.base import Base
from app.db.models import AuditLog, Escalation, MakeupRequest, Student, Ticket
from app.db.seed import seed
from app.db.session import session_scope
from app.graph.checkpointer import get_checkpointer, setup_checkpointer
from app.graph.review import ReviewError, converse, resume_ticket
from app.graph.support import ask_support, build_support_graph
from app.rag.ingest import ingest_tenant
from app.tenants.scope import Reviewer
from tests.conftest import AS_OF
from tests.db.test_postgres import ADMIN_URL, _alembic, migrated, pg_url  # noqa: F401  (fixtures)
from tests.rag.conftest import settings

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]

STEP4_TABLES = {"tickets", "escalations", "makeup_requests", "audit_log"}
MAKEUP = "Please arrange a make-up class for the session I missed in PYD-301."
TODAY = AS_OF.isoformat()


@pytest.fixture
def pg_ready(migrated):  # noqa: F811
    from app.llm.gateway import get_embedding_client

    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    with session_scope() as db:
        ingest_tenant(db, "qspiders_sample", get_embedding_client(settings()))


@pytest.fixture
def pg_saver(pg_url, migrated):  # noqa: F811
    saver = get_checkpointer(Settings(_env_file=None, checkpointer="postgres", database_url=pg_url))
    setup_checkpointer(saver)
    yield saver
    saver.conn.close()


def test_migration_0004_is_additive_and_reversible(pg_url):
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0003")
    from sqlalchemy import create_engine

    eng = create_engine(pg_url)
    with eng.begin() as c:  # data that must survive 0004 up and down
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('t1','T','Asia/Kolkata',75,true)"))
        c.execute(text("INSERT INTO students (tenant_id, id, name, email) VALUES ('t1','s1','S','s@example.com')"))
    command.upgrade(cfg, "0004")
    insp = inspect(eng)
    assert STEP4_TABLES <= set(insp.get_table_names()) and "phone" in {c["name"] for c in insp.get_columns("students")}
    with eng.connect() as c:
        assert c.execute(text("SELECT name, phone FROM students WHERE id='s1'")).one() == ("S", None)
    command.downgrade(cfg, "0003")
    insp = inspect(eng)
    assert not (STEP4_TABLES & set(insp.get_table_names())) and "phone" not in {c["name"] for c in insp.get_columns("students")}
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM students WHERE id='s1'")).scalar() == 1
    command.upgrade(cfg, "head")
    eng.dispose()


def test_models_and_migrations_agree(migrated):  # noqa: F811
    with migrated.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert not [d for d in diff if "alembic" not in str(d)], diff


def test_database_constraints_hold(pg_ready):
    with pytest.raises(IntegrityError), session_scope() as db:          # a ticket for a student that does not exist
        db.add(Ticket(tenant_id="qspiders_sample", id="x", student_id="stu_ghost"))
    with pytest.raises(IntegrityError), session_scope() as db:          # a ticket pointing at another tenant's student id
        db.add(Ticket(tenant_id="qspiders_sample", id="x2", student_id="stu_zed"))
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(Ticket(tenant_id="qspiders_sample", id="x3", status="bogus"))
    with session_scope() as db:
        db.add(Ticket(tenant_id="qspiders_sample", id="t1", student_id="stu_asha"))
    with pytest.raises(IntegrityError), session_scope() as db:          # idempotency key is unique per tenant
        for i in range(2):
            db.add(MakeupRequest(tenant_id="qspiders_sample", id=f"MK-{i}", student_id="stu_asha", batch_id="PYF-101",
                                 session_date=AS_OF, reason="r", status="pending", idempotency_key="same"))
    with pytest.raises(IntegrityError), session_scope() as db:          # a make-up request for a batch that does not exist
        db.add(MakeupRequest(tenant_id="qspiders_sample", id="MK-9", student_id="stu_asha", batch_id="NOPE-999",
                             session_date=AS_OF, reason="r", status="pending", idempotency_key="k9"))


def test_postgres_checkpointer_pause_survives_a_restart(pg_ready, pg_url):  # noqa: F811
    s = Settings(_env_file=None, checkpointer="postgres", database_url=pg_url, embedding_provider="fake", llm_provider="fake")
    saver1 = get_checkpointer(s)
    setup_checkpointer(saver1)
    setup_checkpointer(saver1)                                          # idempotent
    g1 = build_support_graph(settings=s, checkpointer=saver1)
    out = ask_support(g1, MAKEUP, tenant_id="qspiders_sample", student_id="stu_meera", ticket_id="pg-1", today=TODAY)
    assert out["awaiting"] == "staff"
    saver1.conn.close()                                                 # the process "dies"; only the database remains
    del g1, saver1
    saver2 = get_checkpointer(s)                                        # a new process connects
    try:
        g2 = build_support_graph(settings=s, checkpointer=saver2)
        done = resume_ticket(g2, Reviewer("qspiders_sample", "s_1", "staff"), "pg-1", approve=True, today=TODAY)
        assert done["outcome"] == "approved"
        with pytest.raises(ReviewError):
            resume_ticket(g2, Reviewer("qspiders_sample", "s_1", "staff"), "pg-1", approve=True, today=TODAY)
    finally:
        saver2.conn.close()
    with session_scope() as db:
        assert db.scalar(select(MakeupRequest.status)) == "approved"
        assert [a.action for a in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars()] == [
            "makeup_request.created", "action.proposed", "makeup_request.approved"]


def test_postgres_checkpoint_threads_are_isolated_by_tenant_and_student(pg_ready, pg_saver):  # noqa: F811
    s = settings()
    g = build_support_graph(settings=s, checkpointer=pg_saver)
    ask_support(g, "Please update my phone number to 9876543210", tenant_id="qspiders_sample", student_id="stu_meera", ticket_id="pg-2", today=TODAY)
    out = converse(g, "yes", tenant_id="qspiders_sample", student_id="stu_asha", ticket_id="pg-2", today=TODAY)
    assert out["route"] != "write"
    with session_scope() as db:
        assert db.get(Student, ("qspiders_sample", "stu_meera")).phone is None


def test_full_phone_confirmation_and_escalation_on_postgres(pg_ready, pg_saver):  # noqa: F811
    g = build_support_graph(settings=settings(), checkpointer=pg_saver)
    kw = dict(tenant_id="qspiders_sample", today=TODAY)
    converse(g, "Please update my phone number to +91 98765 43210", student_id="stu_meera", ticket_id="pg-3", **kw)
    done = converse(g, "yes", student_id="stu_meera", ticket_id="pg-3", **kw)
    assert done["outcome"] == "approved"
    esc = ask_support(g, "I paid twice and want my money back", student_id="stu_asha", ticket_id="pg-4", **kw)
    assert esc["escalation_id"]
    with session_scope() as db:
        assert db.get(Student, ("qspiders_sample", "stu_meera")).phone == "+919876543210"
        row = db.scalar(select(Escalation))
        assert row.team == "accounts" and row.brief["student_id"] == "stu_asha" and isinstance(row.brief, dict)


def test_two_reviewers_deciding_at_once_only_one_wins(pg_ready):  # noqa: F811
    from app.actions.executor import prepare_action
    from datetime import date

    from app.tenants.scope import Scope

    scope = Scope("qspiders_sample", "stu_meera", AS_OF)
    with session_scope() as db:
        write_repo.ensure_ticket(db, "qspiders_sample", "race", "stu_meera")
        rid = prepare_action(db, scope, ticket_id="race", action="request_makeup_class",
                             args={"batch_id": "PYD-301", "session_date": date(2026, 10, 20), "reason": "r"})
    results: list[str] = []
    barrier = threading.Barrier(2)

    def decide(approve: bool, who: str) -> None:
        barrier.wait()
        try:
            with session_scope() as db:
                write_repo.decide_makeup_request(db, "qspiders_sample", rid, approve=approve, reviewer_id=who)
            results.append("won")
        except write_repo.WriteError:
            results.append("lost")

    threads = [threading.Thread(target=decide, args=(True, "s_1")), threading.Thread(target=decide, args=(False, "s_2"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["lost", "won"]
    with session_scope() as db:
        decided = [a for a in db.execute(select(AuditLog)).scalars() if a.action.startswith("makeup_request.") and a.action != "makeup_request.created"]
        assert len(decided) == 1


def test_reseed_guard_on_postgres(pg_ready):  # noqa: F811
    from app.db.seed import CasesExistError

    with session_scope() as db:
        write_repo.ensure_ticket(db, "qspiders_sample", "keep-me", "stu_asha")
    with pytest.raises(CasesExistError), session_scope() as db:
        seed(db, as_of=AS_OF)
    with session_scope() as db:
        assert db.get(Ticket, ("qspiders_sample", "keep-me")) is not None
