"""M4 on real Postgres: migration 0006 (calls, call_events), its constraints, and the atomic ownership move under REAL concurrent transactions.
Skipped unless SG_TEST_DATABASE_URL is set (same setup as tests/db/test_postgres.py)."""

import threading

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.calls import service as svc
from app.calls.state import TransitionError, transition
from app.db.models import Call, CallEvent
from app.db.seed import seed
from app.db.session import session_scope
from tests.conftest import AS_OF
from tests.db.test_postgres import ADMIN_URL, _alembic, migrated, pg_url  # noqa: F401  (fixtures)

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]
T = "qspiders_sample"


@pytest.fixture
def pg_calls(migrated):  # noqa: F811
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    return migrated


def test_migration_0006_is_additive_and_reversible(pg_url):  # noqa: F811
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0005")
    engine = create_engine(pg_url)
    assert not {"calls", "call_events"} & set(inspect(engine).get_table_names())
    with engine.begin() as c:                                   # data that exists before the upgrade must survive it
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('keep_me','keep','Asia/Kolkata',75,true)"))
    command.upgrade(cfg, "0006")
    names = set(inspect(engine).get_table_names())
    assert {"calls", "call_events", "accounts", "chat_events", "audit_log"} <= names
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM tenants WHERE id='keep_me'")).scalar() == 1
    command.downgrade(cfg, "0005")                              # drops exactly the two new tables
    names = set(inspect(engine).get_table_names())
    assert not {"calls", "call_events"} & names and {"accounts", "chat_events", "audit_log", "tenants"} <= names
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM tenants WHERE id='keep_me'")).scalar() == 1
    command.upgrade(cfg, "head")
    engine.dispose()


def test_call_constraints_hold_on_postgres(pg_calls):
    with session_scope() as db:
        svc.create_call(db, T, "t", None)
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(Call(tenant_id=T, id="c_bad", ticket_id="t", status="bogus", owner="none", epoch=0))
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(Call(tenant_id=T, id="c_bad2", ticket_id="t", status="created", owner="robot", epoch=0))
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(Call(tenant_id="no_such_tenant", id="c_bad3", ticket_id="t", status="created", owner="none", epoch=0))
    with pytest.raises(IntegrityError), session_scope() as db:   # an event cannot point at another tenant's / a missing call
        db.add(CallEvent(tenant_id=T, call_id="c_missing", kind="created", actor="x", detail={}))


def test_concurrent_takeovers_exactly_one_transaction_wins(pg_calls):
    with session_scope() as db:
        cid = svc.create_call(db, T, "t", None).id
        transition(db, T, cid, "created", "active_ai")
    results, barrier = [], threading.Barrier(8)

    def attempt(i: int) -> None:
        barrier.wait()
        try:
            with session_scope() as db:
                svc.move(db, T, cid, "active_ai", "staff_takeover", f"staff{i}", "takeover_succeeded", subject=f"staff{i}")
            results.append(("won", i))
        except TransitionError:
            results.append(("lost", i))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(20) for t in threads]
    won = [i for k, i in results if k == "won"]
    assert len(results) == 8 and len(won) == 1
    with session_scope() as db:
        c = db.get(Call, (T, cid))
        assert (c.status, c.owner, c.owner_subject, c.epoch) == ("staff_takeover", "staff", f"staff{won[0]}", 2)
        assert db.query(CallEvent).filter_by(call_id=cid, kind="takeover_succeeded").count() == 1   # and only the winner wrote its event


def test_concurrent_end_and_takeover_leave_exactly_one_final_state(pg_calls):
    for _ in range(5):
        with session_scope() as db:
            cid = svc.create_call(db, T, "t", None).id
            transition(db, T, cid, "created", "active_ai")
        barrier, out = threading.Barrier(2), {}

        def takeover():
            barrier.wait()
            try:
                with session_scope() as db:
                    svc.move(db, T, cid, "active_ai", "staff_takeover", "s1", "takeover_succeeded", subject="s1")
                out["t"] = "ok"
            except TransitionError:
                out["t"] = "lost"

        def end():
            barrier.wait()
            with session_scope() as db:
                svc.finish(db, T, cid, reason="ended_by_staff")
            out["e"] = "ok"

        ts = [threading.Thread(target=takeover), threading.Thread(target=end)]
        [t.start() for t in ts]
        [t.join(20) for t in ts]
        with session_scope() as db:
            c = db.get(Call, (T, cid))
            assert c.status == "ended" and c.owner == "none" and c.owner_subject is None      # whichever order, the call ends ended and owned by nobody
            assert db.query(CallEvent).filter_by(call_id=cid, kind="ended").count() == 1
