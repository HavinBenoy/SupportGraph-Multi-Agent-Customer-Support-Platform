"""Load the institute sample data into a database. Idempotent: re-seeding a tenant replaces its rows."""

from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import (
    Assessment, AssessmentResult, AttendanceRecord, Batch, ClassSession, Course, Enrollment, Student, Tenant,
)
from app.db.seed_data import SeedTenant, build_seed


def _delete_tenant(db: Session, tenant_id: str) -> None:
    # Child tables first (foreign keys). The tenant row itself is kept and updated in place, and documents/chunks are
    # left alone: ingestion owns them, and they reference the tenant row, so re-seeding must not orphan them.
    for model in (AssessmentResult, AttendanceRecord, Assessment, ClassSession, Enrollment, Batch, Course, Student):
        db.execute(delete(model).where(model.tenant_id == tenant_id))


def _insert_tenant(db: Session, t: SeedTenant, as_of: date) -> int:
    c = t.config
    tid = c.tenant_id
    db.merge(Tenant(id=tid, name=c.name, timezone=c.timezone, attendance_threshold_pct=c.attendance_threshold_pct,
                    is_sample=c.is_sample))
    db.flush()
    for sid, name, email in t.students:
        db.add(Student(tenant_id=tid, id=sid, name=name, email=email))
    for co in t.courses:
        db.add(Course(tenant_id=tid, id=co.id, brand=co.brand, name=co.name))
    db.flush()
    for b in t.batches:
        db.add(Batch(tenant_id=tid, id=b.id, course_id=b.course_id, mode=b.mode, centre=b.centre, timing=b.timing,
                     start_date=as_of - timedelta(days=b.started_days_ago)))
    db.flush()

    past_dates: dict[str, list[date]] = {}
    for b in t.batches:
        past = [as_of - timedelta(days=b.past_sessions - i) for i in range(b.past_sessions)]  # oldest first
        future = [as_of + timedelta(days=j) for j in range(1, b.upcoming_sessions + 1)]
        past_dates[b.id] = past
        for n, d in enumerate(past + future):
            db.add(ClassSession(tenant_id=tid, batch_id=b.id, session_date=d, topic=b.topics[n % len(b.topics)]))
    for e in t.enrollments:
        db.add(Enrollment(tenant_id=tid, id=e.id, student_id=e.student_id, batch_id=e.batch_id, status=e.status,
                          enrolled_on=as_of - timedelta(days=e.enrolled_days_ago)))
    for a in t.assessments:
        db.add(Assessment(tenant_id=tid, id=a.id, batch_id=a.batch_id, title=a.title, kind=a.kind,
                          due_date=as_of + timedelta(days=a.due_in_days), max_score=a.max_score))
    db.flush()
    for e in t.enrollments:
        dates = past_dates[e.batch_id]
        if len(e.attendance) != len(dates):  # a seed mistake should fail loudly, not give a wrong percentage
            raise ValueError(f"{e.id}: attendance pattern has {len(e.attendance)} marks for {len(dates)} past sessions")
        for d, mark in zip(dates, e.attendance, strict=True):
            db.add(AttendanceRecord(tenant_id=tid, enrollment_id=e.id, session_date=d, present=mark == "P"))
    for r in t.results:
        db.add(AssessmentResult(tenant_id=tid, assessment_id=r.assessment_id, enrollment_id=r.enrollment_id,
                                status=r.status, score=r.score))
    db.flush()
    return len(t.enrollments)


def seed(db: Session, as_of: date | None = None, include_isolation_fixture: bool = False) -> dict[str, int]:
    """Returns {tenant_id: number_of_enrollments}. Caller commits."""
    as_of = as_of or date.today()
    result = {}
    for t in build_seed(include_isolation_fixture):
        _delete_tenant(db, t.config.tenant_id)
        result[t.config.tenant_id] = _insert_tenant(db, t, as_of)
    return result
