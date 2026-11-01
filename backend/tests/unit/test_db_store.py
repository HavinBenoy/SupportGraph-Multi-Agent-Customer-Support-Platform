"""Schema constraints and seed data, on the fast SQLite store (FKs switched on in conftest)."""

from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Assessment, AssessmentResult, AttendanceRecord, Batch, ClassSession, Document, Enrollment, Student, Tenant,
)
from app.db.seed import seed
from app.db.seed_data import build_seed
from app.db.session import session_scope
from tests.conftest import AS_OF


def count(db, model, tenant=None):
    stmt = select(func.count()).select_from(model)
    return db.scalar(stmt.where(model.tenant_id == tenant) if tenant else stmt)


def _enrollment(**kw):
    base = dict(tenant_id="qspiders_sample", id="E-9999", student_id="stu_asha", batch_id="PYF-101",
                status="active", enrolled_on=AS_OF)
    return Enrollment(**{**base, **kw})


def test_seed_creates_expected_rows():
    with session_scope() as db:
        assert count(db, Tenant) == 2
        assert count(db, Student, "qspiders_sample") == 6
        assert count(db, Enrollment, "qspiders_sample") == 7
        assert count(db, Batch, "qspiders_sample") == 3
        assert count(db, Assessment, "qspiders_sample") == 6
        assert db.get(Tenant, "qspiders_sample").is_sample is True


def test_seed_dates_are_relative_to_as_of():
    with session_scope() as db:
        assert db.get(Batch, ("qspiders_sample", "PYF-101")).start_date == date(2026, 10, 10)
        assert db.get(Assessment, ("qspiders_sample", "ASM-1")).due_date == date(2026, 10, 17)   # 7 days before
        assert db.get(Assessment, ("qspiders_sample", "ASM-3")).due_date == date(2026, 10, 30)   # 6 days after
        dates = sorted(db.scalars(select(ClassSession.session_date).where(
            ClassSession.tenant_id == "qspiders_sample", ClassSession.batch_id == "PYF-101")))
        assert dates[0] == date(2026, 10, 14) and dates[-1] == date(2026, 10, 29) and len(dates) == 15


def test_attendance_rows_match_the_documented_scenarios():
    with session_scope() as db:
        def pct(enrollment_id):
            rows = list(db.scalars(select(AttendanceRecord.present).where(
                AttendanceRecord.tenant_id == "qspiders_sample", AttendanceRecord.enrollment_id == enrollment_id)))
            return len(rows), sum(rows)
        assert pct("E-1001") == (10, 8) and pct("E-1002") == (10, 6) and pct("E-1003") == (8, 6)


def test_seed_is_idempotent():
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    with session_scope() as db:
        expected = build_seed(include_isolation_fixture=True)
        assert count(db, Enrollment) == sum(len(t.enrollments) for t in expected)
        assert count(db, AssessmentResult) == sum(len(t.results) for t in expected)
        assert count(db, AttendanceRecord) == sum(len(e.attendance) for t in expected for e in t.enrollments)
        assert count(db, ClassSession) == sum(b.past_sessions + b.upcoming_sessions for t in expected for b in t.batches)


def test_reseeding_with_another_date_moves_every_date():
    with session_scope() as db:
        seed(db, as_of=date(2027, 1, 1))
    with session_scope() as db:
        assert db.get(Batch, ("qspiders_sample", "PYF-101")).start_date == date(2026, 12, 18)


def test_reseeding_keeps_the_tenant_row_so_ingested_documents_survive():
    with session_scope() as db:
        db.add(Document(tenant_id="qspiders_sample", doc_id="d", title="t", audience="public", source_path="docs/d.md",
                        content_hash="x"))
    with session_scope() as db:
        seed(db, as_of=AS_OF)  # would violate documents -> tenants if the tenant row were deleted and re-inserted
    with session_scope() as db:
        assert db.get(Document, ("qspiders_sample", "d")) is not None


def test_a_wrong_length_attendance_pattern_fails_loudly(monkeypatch):
    good = build_seed()[0]
    bad = replace(good, enrollments=(replace(good.enrollments[0], attendance="PP"),) + good.enrollments[1:])
    monkeypatch.setattr("app.db.seed.build_seed", lambda include_isolation_fixture=False: [bad])
    with pytest.raises(ValueError, match="attendance pattern"):
        with session_scope() as db:
            seed(db, as_of=AS_OF)


def test_database_rejects_an_enrolment_pointing_at_another_tenants_student():
    # stu_zed exists only at other_institute. A composite FK (tenant_id, student_id) makes this impossible.
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(_enrollment(student_id="stu_zed"))


def test_database_rejects_an_enrolment_in_another_tenants_batch():
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(_enrollment(batch_id="OTH-900"))  # OTH-900 exists only at other_institute


def test_database_rejects_attendance_for_another_tenants_enrolment():
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(AttendanceRecord(tenant_id="qspiders_sample", enrollment_id="E-9001", session_date=AS_OF, present=True))


def test_same_ids_may_exist_in_two_tenants_but_not_twice_in_one():
    with session_scope() as db:  # PYF-101 and E-1001 exist in both tenants (seeded), so this is the proof
        assert db.get(Batch, ("qspiders_sample", "PYF-101")) and db.get(Batch, ("other_institute", "PYF-101"))
        assert db.get(Enrollment, ("qspiders_sample", "E-1001")) and db.get(Enrollment, ("other_institute", "E-1001"))
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(_enrollment(id="E-1001"))


@pytest.mark.parametrize("make", [
    lambda: _enrollment(status="enrolled_maybe"),                                            # unknown status
    lambda: Batch(tenant_id="qspiders_sample", id="XXX-001", course_id="PY-FS", mode="offline", centre=None,
                  timing="t", start_date=AS_OF),                                             # offline needs a centre
    lambda: Batch(tenant_id="qspiders_sample", id="XXX-002", course_id="PY-FS", mode="online", centre="Somewhere",
                  timing="t", start_date=AS_OF),                                             # online has no centre
    lambda: Batch(tenant_id="qspiders_sample", id="XXX-003", course_id="PY-FS", mode="hybrid", centre=None,
                  timing="t", start_date=AS_OF),                                             # unknown mode
    lambda: Assessment(tenant_id="qspiders_sample", id="ASM-X", batch_id="PYF-101", title="t", kind="exam",
                       due_date=AS_OF, max_score=10),                                        # unknown kind
    lambda: Assessment(tenant_id="qspiders_sample", id="ASM-Y", batch_id="PYF-101", title="t", kind="quiz",
                       due_date=AS_OF, max_score=0),                                         # max score must be positive
    lambda: AssessmentResult(tenant_id="qspiders_sample", assessment_id="ASM-3", enrollment_id="E-1001",
                             status="graded", score=None),                                   # graded needs a score
    lambda: AssessmentResult(tenant_id="qspiders_sample", assessment_id="ASM-3", enrollment_id="E-1001",
                             status="submitted", score=5),                                   # a score needs grading
    lambda: AssessmentResult(tenant_id="qspiders_sample", assessment_id="ASM-3", enrollment_id="E-1001",
                             status="graded", score=-1),                                     # negative score
])
def test_check_constraints_reject_bad_rows(make):
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(make())


def test_tenant_attendance_threshold_must_be_a_percentage():
    with pytest.raises(IntegrityError), session_scope() as db:
        db.get(Tenant, "qspiders_sample").attendance_threshold_pct = 101


def test_database_refuses_a_non_public_document():
    with pytest.raises(IntegrityError), session_scope() as db:
        db.add(Document(tenant_id="qspiders_sample", doc_id="x", title="t", audience="internal", source_path="p",
                        content_hash="h"))


def test_repo_queries_cannot_be_called_without_a_scope():
    import inspect

    from app.db import repo
    for fn in (repo.get_tenant, repo.get_my_enrollments, repo.upcoming_sessions, repo.attendance_counts,
               repo.my_assessments):
        assert list(inspect.signature(fn).parameters)[:2] == ["db", "scope"]


def test_raw_sql_sanity_check_for_the_fk_pragma(seeded_db):
    with seeded_db.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
