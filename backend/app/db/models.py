"""Institute schema (build step 1), modelled on a training institute such as QSpiders / PySpiders.

Tenant isolation is built into the keys: every table is keyed by (tenant_id, id) and every foreign key
INCLUDES tenant_id. So the database itself rejects an enrolment that points at another tenant's student;
isolation does not rely on every query remembering a WHERE clause.

Ownership: a student's own data hangs off `enrollments` (student_id). Batches, sessions and assessment definitions
are shared by everyone in the batch; attendance and results are per enrolment, so they are only reachable through
the student's own enrolment.
"""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BATCH_MODES = ("online", "offline")
ENROLLMENT_STATUSES = ("active", "completed", "on_hold", "withdrawn")
ASSESSMENT_KINDS = ("quiz", "assignment", "project", "mock_interview")
RESULT_STATUSES = ("submitted", "graded", "missing")

# Width of every stored embedding. Changing it needs a new migration AND a full re-ingest, because vectors of
# different widths (or from different models) cannot be compared. See docs in README "Build step 2".
EMBEDDING_DIM = 768
# pgvector's `vector(768)` on Postgres; a JSON list on SQLite so the fast test suite needs no extension.
EmbeddingColumn = Vector(EMBEDDING_DIM).with_variant(JSON(), "sqlite")


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("attendance_threshold_pct BETWEEN 1 AND 100", name="attendance_threshold_valid"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64))
    attendance_threshold_pct: Mapped[int] = mapped_column(Integer)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),)

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(20))  # build step 4: changed only through the confirmed write action


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),)

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    brand: Mapped[str] = mapped_column(String(64))  # e.g. "QSpiders", "PySpiders"
    name: Mapped[str] = mapped_column(String(200))


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "course_id"], ["courses.tenant_id", "courses.id"]),
        CheckConstraint("mode IN ('online','offline')", name="mode_valid"),
        # An offline batch happens at a centre; an online one does not.
        CheckConstraint("(mode = 'offline' AND centre IS NOT NULL) OR (mode = 'online' AND centre IS NULL)",
                        name="centre_matches_mode"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # e.g. PYF-101
    course_id: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(8))
    centre: Mapped[str | None] = mapped_column(String(100))
    timing: Mapped[str] = mapped_column(String(100))  # human-readable, e.g. "Mon-Fri 10:00-12:00 IST"
    start_date: Mapped[date] = mapped_column(Date)


class Enrollment(Base):
    """A student in a batch. This row is the ownership link for everything personal (attendance, results)."""

    __tablename__ = "enrollments"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "student_id"], ["students.tenant_id", "students.id"]),
        ForeignKeyConstraint(["tenant_id", "batch_id"], ["batches.tenant_id", "batches.id"]),
        CheckConstraint("status IN ('active','completed','on_hold','withdrawn')", name="status_valid"),
        Index("ix_enrollments_tenant_student", "tenant_id", "student_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64))
    batch_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    enrolled_on: Mapped[date] = mapped_column(Date)


class ClassSession(Base):
    """One scheduled class of a batch (past or future)."""

    __tablename__ = "class_sessions"
    __table_args__ = (ForeignKeyConstraint(["tenant_id", "batch_id"], ["batches.tenant_id", "batches.id"]),)

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_date: Mapped[date] = mapped_column(Date, primary_key=True)
    topic: Mapped[str] = mapped_column(String(200))


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "enrollment_id"], ["enrollments.tenant_id", "enrollments.id"]),
        Index("ix_attendance_tenant_enrollment", "tenant_id", "enrollment_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enrollment_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_date: Mapped[date] = mapped_column(Date, primary_key=True)
    present: Mapped[bool] = mapped_column(Boolean)


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "batch_id"], ["batches.tenant_id", "batches.id"]),
        CheckConstraint("kind IN ('quiz','assignment','project','mock_interview')", name="kind_valid"),
        CheckConstraint("max_score > 0", name="max_score_positive"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    due_date: Mapped[date] = mapped_column(Date)
    max_score: Mapped[int] = mapped_column(Integer)


class AssessmentResult(Base):
    """One student's result for one assessment; absent row = not submitted yet and not past due."""

    __tablename__ = "assessment_results"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "assessment_id"], ["assessments.tenant_id", "assessments.id"]),
        ForeignKeyConstraint(["tenant_id", "enrollment_id"], ["enrollments.tenant_id", "enrollments.id"]),
        CheckConstraint("status IN ('submitted','graded','missing')", name="status_valid"),
        # A score exists exactly when the work has been graded.
        CheckConstraint("(status = 'graded' AND score IS NOT NULL) OR (status <> 'graded' AND score IS NULL)",
                        name="score_matches_status"),
        CheckConstraint("score IS NULL OR score >= 0", name="score_non_negative"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enrollment_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    score: Mapped[int | None] = mapped_column(Integer)


class Document(Base):
    """One customer-facing help article that has been ingested for retrieval (build step 2).

    SECURITY: `audience` may only ever be 'public'. The CHECK makes the database itself refuse an internal
    document, so an ingestion bug or a mistakenly placed file cannot put staff-only policy into customer answers.
    Widening this constraint (e.g. for a future agent-console assistant) is a deliberate security decision that
    belongs in its own migration and its own table or retrieval path, not a quiet edit.
    """

    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        CheckConstraint("audience = 'public'", name="audience_public_only"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    audience: Mapped[str] = mapped_column(String(16))
    source_path: Mapped[str] = mapped_column(String(300))  # relative to the tenant folder, e.g. docs/returns.md
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 of the file: unchanged files are not re-embedded
    last_reviewed: Mapped[date | None] = mapped_column(Date)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    """A passage of a document plus its embedding. Keyed (tenant_id, doc_id, chunk_index); the foreign key
    includes tenant_id, so a chunk cannot point at another tenant's document."""

    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "doc_id"], ["documents.tenant_id", "documents.doc_id"], ondelete="CASCADE"),
        CheckConstraint("audience = 'public'", name="audience_public_only"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        Index("ix_chunks_tenant_model", "tenant_id", "embedding_model"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    heading: Mapped[str] = mapped_column(String(400))  # "Returns and Refunds > Return window"
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)  # approximate (words); for sizing, not billing
    audience: Mapped[str] = mapped_column(String(16))  # denormalised so retrieval can filter on it directly
    # Which model produced the vector. Retrieval only compares vectors from the CURRENT model: mixing models
    # silently returns meaningless similarities.
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding = mapped_column(EmbeddingColumn, nullable=False)


# ----------------------------------------------------------------------------------------------------------------------
# Build step 4: cases, escalations, write actions and the audit trail. Same rule as above: every key and foreign key
# carries tenant_id. Nothing here is ever readable by the LLM: these tables are touched only by code (app/actions,
# app/escalation, app/db/write_repo.py).
# ----------------------------------------------------------------------------------------------------------------------
TICKET_STATUSES = ("open", "awaiting_approval", "escalated", "resolved")
ESCALATION_STATUSES = ("open", "claimed", "resolved")
MAKEUP_STATUSES = ("pending", "approved", "rejected")


class Ticket(Base):
    """One support case (one chat thread). `student_id` is NULL for an anonymous prospect."""

    __tablename__ = "tickets"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(["tenant_id", "student_id"], ["students.tenant_id", "students.id"]),
        CheckConstraint("status IN ('open','awaiting_approval','escalated','resolved')", name="status_valid"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_id: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16), default="chat")
    category: Mapped[str] = mapped_column(String(32), default="other")
    priority: Mapped[str] = mapped_column(String(16), default="low")
    status: Mapped[str] = mapped_column(String(24), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Escalation(Base):
    """A handoff to a human team, with the brief that saves them re-reading the chat. At most one per ticket."""

    __tablename__ = "escalations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "ticket_id"], ["tickets.tenant_id", "tickets.id"]),
        CheckConstraint("status IN ('open','claimed','resolved')", name="status_valid"),
        Index("ix_escalations_queue", "tenant_id", "status", "team"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64))
    team: Mapped[str] = mapped_column(String(64))
    priority: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64))
    brief: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="open")
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MakeupRequest(Base):
    """A student's request for a make-up class. Created `pending`; only a staff decision moves it to approved/rejected."""

    __tablename__ = "makeup_requests"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "student_id"], ["students.tenant_id", "students.id"]),
        ForeignKeyConstraint(["tenant_id", "batch_id"], ["batches.tenant_id", "batches.id"]),
        CheckConstraint("status IN ('pending','approved','rejected')", name="status_valid"),
        # The same ticket can never create the same request twice (resume twice, retry after a crash).
        Index("uq_makeup_idempotency", "tenant_id", "idempotency_key", unique=True),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64))
    batch_id: Mapped[str] = mapped_column(String(32))
    session_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    ticket_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Append-only record of every write and every approval decision. No code path updates or deletes a row."""

    __tablename__ = "audit_log"
    __table_args__ = (ForeignKeyConstraint(["tenant_id"], ["tenants.id"]), Index("ix_audit_tenant_ticket", "tenant_id", "ticket_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    ticket_id: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(80))      # "student:stu_asha", "staff:s_1", "system"
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
