"""Migrations 0007 (intervention feedback) and 0008 (trial mode): additive, reversible, data-preserving. SQLite here (from a database at revision 0006);
the same steps on PostgreSQL 16 are in tests/db/test_postgres_m5.py. (Revisions 0001-0003 use PostgreSQL-only DDL, so a from-scratch SQLite upgrade is not
supported and never was: SQLite is the unit-test engine, PostgreSQL is the product's database.)"""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from tests.db.test_postgres import BACKEND

NEW_0007 = {"intervention_feedback", "feedback_reviews"}
NEW_0008 = {"trial_settings", "trial_events", "call_consents", "call_recordings"}


def cfg_for(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


@pytest.fixture
def at_0006(tmp_path):
    """A SQLite database shaped like revision 0006 (the M4 release), holding one tenant and one call."""
    url = f"sqlite:///{tmp_path / 'm.db'}"
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(text("PRAGMA foreign_keys=OFF"))
        for t in ("kb_change_items", "kb_change_requests", "kb_doc_placement", "kb_folders", "kb_events", "kb_proposals", "kb_version_chunks", "kb_versions", "kb_documents",              # M6 (0009) tables are not part of revision 0006
                  "feedback_reviews", "intervention_feedback", "call_recordings", "call_consents", "trial_events", "trial_settings"):
            c.execute(text(f"DROP TABLE {t}"))
        c.execute(text("ALTER TABLE documents DROP COLUMN kb_version"))
        c.execute(text("ALTER TABLE calls DROP COLUMN intervention_owner"))
        c.execute(text("ALTER TABLE calls DROP COLUMN trial_mode"))
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('keep_me','keep','Asia/Kolkata',75,1)"))
        c.execute(text("INSERT INTO calls (tenant_id, id, ticket_id, status, owner, epoch, created_at) VALUES ('keep_me','c_old','t1','ended','none',2,'2026-10-01 10:00:00')"))
    cfg = cfg_for(url)
    command.stamp(cfg, "0006")
    yield url, cfg, eng
    eng.dispose()


def test_0007_and_0008_upgrade_downgrade_and_re_upgrade_keep_existing_data(at_0006):
    url, cfg, eng = at_0006
    assert not (NEW_0007 | NEW_0008) & set(inspect(eng).get_table_names())
    command.upgrade(cfg, "head")
    insp = inspect(eng)
    names = set(insp.get_table_names())
    assert (NEW_0007 | NEW_0008) <= names
    cols = {c["name"] for c in insp.get_columns("calls")}
    assert {"intervention_owner", "trial_mode"} <= cols
    with eng.connect() as c:                                                  # old rows survive, with the safe defaults
        row = c.execute(text("SELECT status, intervention_owner, trial_mode FROM calls WHERE id='c_old'")).one()
        assert (row[0], row[1], bool(row[2])) == ("ended", None, False)
        assert c.execute(text("SELECT count(*) FROM tenants WHERE id='keep_me'")).scalar() == 1
    command.downgrade(cfg, "0007")                                            # 0008 off: the trial tables and the column go, M4.1 stays
    insp = inspect(eng)
    assert not NEW_0008 & set(insp.get_table_names()) and NEW_0007 <= set(insp.get_table_names())
    assert "trial_mode" not in {c["name"] for c in insp.get_columns("calls")} and "intervention_owner" in {c["name"] for c in insp.get_columns("calls")}
    command.downgrade(cfg, "0006")
    insp = inspect(eng)
    assert not (NEW_0007 | NEW_0008) & set(insp.get_table_names())
    assert not {"intervention_owner", "trial_mode"} & {c["name"] for c in insp.get_columns("calls")}
    with eng.connect() as c:
        assert c.execute(text("SELECT status FROM calls WHERE id='c_old'")).scalar() == "ended"      # the call is intact after a full downgrade
    command.upgrade(cfg, "head")
    assert (NEW_0007 | NEW_0008) <= set(inspect(eng).get_table_names())
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM calls")).scalar() == 1


def test_the_new_tables_enforce_their_constraints(at_0006):
    url, cfg, eng = at_0006
    command.upgrade(cfg, "head")
    def run(sql):
        with eng.begin() as c:
            c.execute(text("PRAGMA foreign_keys=ON"))
            c.execute(text(sql))
    run("INSERT INTO intervention_feedback (tenant_id, call_id, staff_subject, state, intervened_at) VALUES ('keep_me','c_old','staff1','awaiting','2026-10-01 10:00:00')")
    for bad in (
        "INSERT INTO intervention_feedback (tenant_id, call_id, staff_subject, state, intervened_at) VALUES ('keep_me','c_old','staff1','awaiting','2026-10-01')",     # duplicate
        "UPDATE intervention_feedback SET state='bogus'",
        "UPDATE intervention_feedback SET state='submitted'",                                       # a submission needs its text and category
        "UPDATE intervention_feedback SET category='nonsense'",
        "UPDATE intervention_feedback SET review_status='nonsense'",
        "INSERT INTO call_consents (tenant_id, call_id, status, policy_version, offered_at) VALUES ('keep_me','c_old','maybe',1,'2026-10-01')",
        "INSERT INTO call_consents (tenant_id, call_id, status, policy_version, offered_at) VALUES ('keep_me','c_missing','offered',1,'2026-10-01')",     # FK
        "INSERT INTO call_recordings (tenant_id, id, call_id, consent_policy_version, status, provider, started_at) VALUES ('keep_me','r','c_old',1,'weird','local','2026-10-01')",
        "INSERT INTO trial_settings (tenant_id, enabled, version, disclosure_text, recording_notice, consent_policy_version, retention_recording_days, retention_consent_days, "
        "retention_feedback_days, withdrawal_policy, recording_offered) VALUES ('keep_me',1,1,'d','n',1,30,365,365,'shred',0)",
        "INSERT INTO trial_settings (tenant_id, enabled, version, disclosure_text, recording_notice, consent_policy_version, retention_recording_days, retention_consent_days, "
        "retention_feedback_days, withdrawal_policy, recording_offered) VALUES ('no_such',1,1,'d','n',1,30,365,365,'delete',0)",
    ):
        with pytest.raises(IntegrityError):
            run(bad)


def test_the_returning_to_ai_status_is_still_valid_for_legacy_rows_only(at_0006):
    url, cfg, eng = at_0006
    command.upgrade(cfg, "head")
    with eng.begin() as c:
        c.execute(text("UPDATE calls SET status='returning_to_ai' WHERE id='c_old'"))        # an M4 row left in that state is still representable (no rewrite)
    with eng.connect() as c:
        assert c.execute(text("SELECT status FROM calls WHERE id='c_old'")).scalar() == "returning_to_ai"
