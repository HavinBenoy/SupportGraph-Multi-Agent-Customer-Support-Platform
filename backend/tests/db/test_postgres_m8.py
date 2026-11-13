"""M8 on real PostgreSQL 16: migration 0014 is additive and reversible on a database with existing calls; racing wrong PINs from parallel calls can never
exceed the attempt limit (the counter is one atomic UPDATE, the lock is set once); a correct PIN during the lock is refused; the telephony summary query.
Skipped unless SG_TEST_DATABASE_URL is set (a SCRATCH database: these tests create and drop their own)."""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, select, text

from app.calls import service as svc
from app.db.models import PhonePin, TelephonyAudit
from app.db.seed import seed
from app.db.session import session_scope
from app.telephony import identity as idn
from tests.conftest import AS_OF
from tests.db.test_postgres import ADMIN_URL, _alembic, migrated, pg_url  # noqa: F401  (fixtures)
from tests.db.test_postgres_m6 import race

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]
T = "qspiders_sample"


@pytest.fixture
def pg(migrated):  # noqa: F811
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
        from app.auth.accounts import create_account
        create_account(db, T, "asha", "correct-horse-battery", "student", "stu_asha")
    return migrated


def test_0014_upgrades_and_downgrades_without_touching_existing_calls_on_postgres(pg_url):  # noqa: F811
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0013")
    eng = create_engine(pg_url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('keep_me','keep','Asia/Kolkata',75,true)"))
        c.execute(text("INSERT INTO calls (tenant_id, id, ticket_id, status, owner, epoch, created_at, trial_mode) "
                       "VALUES ('keep_me','c_old','t1','ended','none',2,now(),false)"))
    command.upgrade(cfg, "0014")
    with eng.connect() as c:
        assert tuple(c.execute(text("SELECT id, epoch, channel, caller_status FROM calls")).one()) == ("c_old", 2, "web", None)
    assert {"account_phones", "phone_pins", "telephony_audit"} <= set(inspect(eng).get_table_names())
    command.downgrade(cfg, "0013")
    assert "channel" not in {c["name"] for c in inspect(eng).get_columns("calls")}
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM calls")).scalar() == 1
    command.upgrade(cfg, "head")
    eng.dispose()


def test_racing_wrong_pins_never_exceed_the_limit_and_lock_once_on_postgres(pg):  # noqa: F811
    with session_scope() as db:
        idn.set_pin(db, T, "asha", "482913", 6)

    def guess(i):
        with session_scope() as db:
            return idn.verify_pin(db, T, "asha", f"00000{i % 10}", max_attempts=5, lock_minutes=15)
    out, errs = race(8, guess)
    assert not errs, errs
    assert out.count("locked") >= 1 and out.count("wrong") <= 4, out                    # at most max-1 plain failures before the lock
    with session_scope() as db:
        row = db.get(PhonePin, (T, "asha"))
        assert row.locked_until is not None and row.failed_attempts < 5
        assert idn.verify_pin(db, T, "asha", "482913", max_attempts=5, lock_minutes=15) == "locked"   # the right PIN does not open a locked account
        locks = db.execute(select(TelephonyAudit).where(TelephonyAudit.tenant_id == T, TelephonyAudit.action == "pin_locked")).scalars().all()
        assert len(locks) == 1


def test_the_phone_summary_query_runs_on_postgres(pg):  # noqa: F811
    with session_scope() as db:
        svc.create_call(db, T, "t1", None, channel="phone", phone={"caller_status": "claimed", "caller_role": "student", "caller_masked": "+91 ******2345"})
        svc.create_call(db, T, "t2", None)
    from app.api.routes.telephony import summary
    from app.auth.tokens import Principal
    r = summary(days=30, p=Principal(T, "admin", "admin1", None))
    assert r["phone"]["total"] == 1 and r["phone"]["claimed_unverified"] == 1 and r["web_calls"] == 1
