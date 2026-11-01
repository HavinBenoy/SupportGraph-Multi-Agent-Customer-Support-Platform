"""All institute queries. Every function takes a Scope, so a query without tenant_id is not expressible here.

Student-owned data (enrolments, and through them attendance and results) is additionally filtered by student_id:
a student can only see their own. Withdrawn enrolments are treated as "no access" everywhere.
"""

from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Assessment, AssessmentResult, AttendanceRecord, Batch, ClassSession, Course, Enrollment, Tenant,
)
from app.tenants.scope import Scope

VISIBLE_STATUSES = ("active", "on_hold", "completed")  # 'withdrawn' enrolments grant no access


def get_tenant(db: Session, scope: Scope) -> Tenant | None:
    return db.get(Tenant, scope.tenant_id)


def get_my_enrollments(db: Session, scope: Scope, batch_id: str | None = None) -> list[tuple[Enrollment, Batch, Course]]:
    """The student's own enrolments (joined with batch and course), newest enrolment first."""
    stmt = (
        select(Enrollment, Batch, Course)
        .join(Batch, (Batch.tenant_id == Enrollment.tenant_id) & (Batch.id == Enrollment.batch_id))
        .join(Course, (Course.tenant_id == Batch.tenant_id) & (Course.id == Batch.course_id))
        .where(Enrollment.tenant_id == scope.tenant_id, Enrollment.student_id == scope.student_id,
               Enrollment.status.in_(VISIBLE_STATUSES))
        .order_by(Enrollment.enrolled_on.desc(), Enrollment.id)
    )
    if batch_id is not None:
        stmt = stmt.where(Enrollment.batch_id == batch_id)
    return [(e, b, c) for e, b, c in db.execute(stmt).all()]


def upcoming_sessions(db: Session, scope: Scope, batch_id: str, limit: int = 5) -> list[ClassSession]:
    """Future classes of a batch. Call only for a batch the student is enrolled in (see get_my_enrollments)."""
    stmt = (
        select(ClassSession)
        .where(ClassSession.tenant_id == scope.tenant_id, ClassSession.batch_id == batch_id,
               ClassSession.session_date >= scope.today)
        .order_by(ClassSession.session_date)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def attendance_counts(db: Session, scope: Scope, enrollment_id: str) -> tuple[int, int]:
    """(sessions_held, sessions_present) up to and including today, for one of THIS student's enrolments."""
    stmt = (
        select(func.count(), func.coalesce(func.sum(AttendanceRecord.present.cast(Integer)), 0))
        .select_from(AttendanceRecord)
        .join(Enrollment, (Enrollment.tenant_id == AttendanceRecord.tenant_id) & (Enrollment.id == AttendanceRecord.enrollment_id))
        .where(AttendanceRecord.tenant_id == scope.tenant_id, AttendanceRecord.enrollment_id == enrollment_id,
               Enrollment.student_id == scope.student_id,  # ownership re-checked at the data row, not trusted from the caller
               AttendanceRecord.session_date <= scope.today)
    )
    held, present = db.execute(stmt).one()
    return int(held), int(present)


def my_assessments(db: Session, scope: Scope, enrollment_id: str, batch_id: str) -> list[tuple[Assessment, AssessmentResult | None]]:
    stmt = (
        select(Assessment, AssessmentResult)
        .outerjoin(AssessmentResult,
                   (AssessmentResult.tenant_id == Assessment.tenant_id) & (AssessmentResult.assessment_id == Assessment.id)
                   & (AssessmentResult.enrollment_id == enrollment_id))
        .join(Enrollment, (Enrollment.tenant_id == Assessment.tenant_id) & (Enrollment.batch_id == Assessment.batch_id))
        .where(Assessment.tenant_id == scope.tenant_id, Assessment.batch_id == batch_id,
               Enrollment.id == enrollment_id, Enrollment.student_id == scope.student_id)
        .order_by(Assessment.due_date, Assessment.id)
    )
    return [(a, r) for a, r in db.execute(stmt).all()]
