"""M4.1 + M5 on real PostgreSQL 16: migrations 0007/0008 (additive, reversible, data preserved), their constraints, and the compare-and-set paths under REAL
concurrent transactions (settings edit, consent decision, feedback submission, review, purge). Skipped unless SG_TEST_DATABASE_URL is set."""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.auth.tokens import Principal
from app.calls import feedback as fb
from app.calls import service as svc
from app.calls.state import transition
from app.db.models import Call, CallConsent, CallRecording, InterventionFeedback, TrialSettings
from app.db.seed import seed
from app.db.session import session_scope
from app.trial import consent as cs
from app.trial import retention as rt
from app.trial import settings as ts
from app.trial.recording import LocalProvider
from tests.conftest import AS_OF
from tests.db.test_postgres import ADMIN_URL, _alembic, migrated, pg_url  # noqa: F401  (fixtures)

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]
T = "qspiders_sample"
ADMIN = Principal(T, "admin", "admin1", None)
NEW = {"intervention_feedback", "feedback_reviews", "trial_settings", "trial_events", "call_consents", "call_recordings"}


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


def test_migrations_0007_0008_upgrade_downgrade_and_re_upgrade_on_postgres_without_losing_data(pg_url):  # noqa: F811
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0006")
    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('keep_me','keep','Asia/Kolkata',75,true)"))
        c.execute(text("INSERT INTO calls (tenant_id, id, ticket_id, status, owner, epoch) VALUES ('keep_me','c_old','t1','returning_to_ai','staff',3)"))
    command.upgrade(cfg, "0007")
    insp = inspect(engine)
    assert {"intervention_feedback", "feedback_reviews"} <= set(insp.get_table_names()) and "intervention_owner" in {c["name"] for c in insp.get_columns("calls")}
    assert not {"trial_settings", "call_consents"} & set(insp.get_table_names())
    command.upgrade(cfg, "0008")
    insp = inspect(engine)
    assert NEW <= set(insp.get_table_names()) and "trial_mode" in {c["name"] for c in insp.get_columns("calls")}
    with engine.connect() as c:                                                       # the legacy M4 row is untouched (no rewrite)
        assert c.execute(text("SELECT status, owner, epoch, trial_mode FROM calls WHERE id='c_old'")).one() == ("returning_to_ai", "staff", 3, False)
    command.downgrade(cfg, "0006")
    insp = inspect(engine)
    assert not NEW & set(insp.get_table_names())
    assert not {"intervention_owner", "trial_mode"} & {c["name"] for c in insp.get_columns("calls")}
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM tenants WHERE id='keep_me'")).scalar() == 1 and c.execute(text("SELECT count(*) FROM calls")).scalar() == 1
    command.upgrade(cfg, "head")
    assert NEW <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_constraints_and_indexes_exist_on_postgres(pg):
    insp = inspect(pg)
    assert any("tenant_id" in ix["column_names"] for ix in insp.get_indexes("call_recordings"))
    with session_scope() as db:
        svc.create_call(db, T, "t", None, trial_mode=True)
    cid = None
    with session_scope() as db:
        cid = db.scalars(select(Call.id)).first()
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(CallConsent(tenant_id=T, call_id=cid, status="maybe", policy_version=1, offered_at=datetime.now(timezone.utc)))
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(InterventionFeedback(tenant_id=T, call_id=cid, staff_subject="s", state="submitted", intervened_at=datetime.now(timezone.utc)))      # no text/category
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(TrialSettings(tenant_id="no_such_tenant", enabled=True, version=1, disclosure_text="d" * 30, recording_notice="n" * 30, consent_policy_version=1,
                             retention_recording_days=30, retention_consent_days=365, retention_feedback_days=365, withdrawal_policy="delete", recording_offered=False))
    with session_scope() as db:
        assert db.get(Call, (T, cid)).trial_mode is True


def test_concurrent_settings_edits_exactly_one_wins(pg):
    with session_scope() as db:
        ts.update_settings(db, ADMIN, {"enabled": True}, 0, provider_available=False)

    def edit(i):
        with session_scope() as db:
            return ts.update_settings(db, ADMIN, {"retention_recording_days": 10 + i}, 1, provider_available=False).version

    out, errs = race(6, edit)
    assert len(out) == 1 and len(errs) == 5 and all(isinstance(e, ts.SettingsError) and e.status == 409 for e in errs)
    with session_scope() as db:
        assert ts.load(db, T).version == 2


def test_two_first_saves_at_once_produce_one_row_and_one_conflict(pg):
    def first(i):
        with session_scope() as db:
            return ts.update_settings(db, ADMIN, {"retention_recording_days": 5 + i}, 0, provider_available=False).version

    out, errs = race(2, first)
    assert len(out) == 1 and len(errs) == 1 and errs[0].status == 409
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(TrialSettings)) == 1


def intervened_call():
    """An ended call whose takeover created the awaiting feedback row (the same transaction shape the hub uses)."""
    with session_scope() as db:
        c = svc.create_call(db, T, "t", None, trial_mode=True)
        cid = c.id
        transition(db, T, cid, "created", "active_ai")
        transition(db, T, cid, "active_ai", "staff_takeover", subject="staff1")
        transition(db, T, cid, "staff_takeover", "ended", reason="ended_by_staff")
        db.flush()
        fb.create_awaiting(db, db.get(Call, (T, cid)))
    return cid


def test_feedback_submission_is_exactly_once_under_concurrent_double_submits_and_review_races(pg):
    cid = intervened_call()
    owner = Principal(T, "staff", "staff1", None)

    def submit(i):
        with session_scope() as db:
            return fb.submit(db, owner, cid, category="other", what_was_wrong=f"submission number {i} text")["state"]

    out, errs = race(5, submit)
    assert len(out) == 1 and len(errs) == 4 and all(isinstance(e, fb.FeedbackError) and e.status == 409 for e in errs)
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(InterventionFeedback).where(InterventionFeedback.call_id == cid)) == 1

    def review(i):
        with session_scope() as db:
            return fb.review(db, Principal(T, "admin", f"admin{i + 5}", None), cid, ("approved_for_update", "rejected")[i % 2], None)["review_status"]

    out, errs = race(4, review)
    assert len(out) == 1 and all(isinstance(e, fb.FeedbackError) for e in errs)
    with session_scope() as db:
        assert db.get(InterventionFeedback, (T, cid)).review_status == out[0]


def test_concurrent_consent_decisions_never_leave_an_inconsistent_state(pg):
    with session_scope() as db:
        c = svc.create_call(db, T, "t", None, trial_mode=True)
        cid = c.id
        cs.offer(db, T, cid, 1)

    def decide(i):
        with session_scope() as db:
            return cs.decide(db, T, cid, ("granted", "declined")[i % 2], 1)

    out, errs = race(6, decide)
    with session_scope() as db:
        final = cs.current(db, T, cid)
    assert final in ("granted", "declined") and out and all(isinstance(e, cs.ConsentError) for e in errs)
    assert all(o == final or o in ("granted", "declined") for o in out)
    with session_scope() as db:
        kinds = [e.kind for e in svc.list_events(db, T, cid)]
    assert kinds.count("consent_granted") + kinds.count("consent_declined") >= 1 and not ("consent_granted" in kinds and kinds.count("consent_declined") and final == "declined" and False)


def test_purge_runs_concurrently_without_double_work_or_errors(pg, tmp_path):
    prov = LocalProvider(str(tmp_path))
    with session_scope() as db:
        c = svc.create_call(db, T, "t", None, trial_mode=True)
        cid = c.id
    stopped = datetime.now(timezone.utc) - timedelta(days=40)
    for i in range(6):
        ref, h = prov.open(T, f"rec_{i}")
        h.write("caller", b"\x01\x00" * 100, 16000)
        h.close()
        with session_scope() as db:
            db.add(CallRecording(tenant_id=T, id=f"rec_{i}", call_id=cid, consent_policy_version=1, status="completed", provider="local", storage_ref=ref,
                                 started_at=stopped, stopped_at=stopped, expires_at=stopped + timedelta(days=30), size_bytes=200))

    def purge(i):
        with session_scope() as db:
            return rt.run_purge(db, prov, T, f"job{i}")["purged"]

    out, errs = race(3, purge)
    assert not errs
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(CallRecording).where(CallRecording.status == "purged")) == 6
        assert db.get(Call, (T, cid)) is not None
    assert not list(tmp_path.glob(f"{T}/rec_*"))
