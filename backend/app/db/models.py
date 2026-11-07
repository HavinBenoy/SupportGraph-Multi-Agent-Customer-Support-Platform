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
    JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index, Integer, Numeric, String, Text, false, func,
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


# ----------------------------------------------------------------------------------------------------------------------
# Build step 5: accounts for the staff console / student sign-in, and per-turn chat events for the admin analytics.
# ----------------------------------------------------------------------------------------------------------------------
ACCOUNT_ROLES = ("student", "staff", "admin")


class Account(Base):
    """A login. Passwords are stored only as salted scrypt hashes. A student account names the student it signs in as
    (`student_id`, checked against `students` at login; deliberately not a foreign key so re-seeding the sample students
    never fails because an account exists)."""

    __tablename__ = "accounts"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        CheckConstraint("role IN ('student','staff','admin')", name="role_valid"),
        CheckConstraint("(role = 'student') = (student_id IS NOT NULL)", name="student_role_has_student_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    student_id: Mapped[str | None] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(300))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatEvent(Base):
    """One row per chat turn: what the system DID (route, outcome, latency), never what anyone said. Feeds the analytics."""

    __tablename__ = "chat_events"
    __table_args__ = (ForeignKeyConstraint(["tenant_id"], ["tenants.id"]), Index("ix_chat_events_tenant_time", "tenant_id", "created_at"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    ticket_id: Mapped[str] = mapped_column(String(80))
    signed_in: Mapped[bool] = mapped_column(Boolean)
    route: Mapped[str] = mapped_column(String(16))
    route_reason: Mapped[str] = mapped_column(String(64), default="")
    abstained: Mapped[bool] = mapped_column(Boolean, default=False)
    qa_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    awaiting: Mapped[str] = mapped_column(String(16), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


CALL_STATUSES = ("created", "active_ai", "staff_takeover", "returning_to_ai", "ended", "failed")
CALL_OWNERS = ("ai", "staff", "none")


class Call(Base):
    """M4: one voice call and who currently owns its audio. The row is the durable record; the live audio state lives in app.calls.hub.

    Ownership moves only through app.calls.state.transition (an atomic compare-and-set on `status`), and every move bumps `epoch`.
    Holds identifiers and timestamps only: never audio, never transcripts."""

    __tablename__ = "calls"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        CheckConstraint("status IN ('created','active_ai','staff_takeover','returning_to_ai','ended','failed')", name="status_valid"),
        CheckConstraint("owner IN ('ai','staff','none')", name="owner_valid"),
        Index("ix_calls_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(80))
    student_id: Mapped[str | None] = mapped_column(String(64))      # None = anonymous caller
    status: Mapped[str] = mapped_column(String(24), default="created")
    owner: Mapped[str] = mapped_column(String(8), default="none")
    owner_subject: Mapped[str | None] = mapped_column(String(64))   # the staff account that holds the call while owner = 'staff'
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    takeover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))    # the latest takeover
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))    # LEGACY (M4): a call can no longer return to the AI (M4.1); never written now
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(32))
    # M4.1: the staff account that interrupted the call. Set in the SAME transaction as the takeover and never cleared (owner_subject is
    # cleared when the call ends; this is the durable fact "who holds / held this call"). Only this account may control the call or file its feedback.
    intervention_owner: Mapped[str | None] = mapped_column(String(64))
    # M5: the call was made while Trial Verification Mode was on for the tenant (decided once, when the call is created).
    trial_mode: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class CallEvent(Base):
    """M4: the audit trail of a call (created, listen started, takeover, returned, ended ...). Ids, actors and safe metadata only."""

    __tablename__ = "call_events"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "call_id"], ["calls.tenant_id", "calls.id"]),
        Index("ix_call_events_call", "tenant_id", "call_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    call_id: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))                  # staff username, "caller", "ai" or "system"
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ----------------------------------------------------------------------------------------------------------------------
# M4.1: intervention feedback. One row per human intervention (a call can only be taken over once and never goes back to the AI).
# ----------------------------------------------------------------------------------------------------------------------
FEEDBACK_STATES = ("awaiting", "submitted", "skipped")
FEEDBACK_CATEGORIES = ("incorrect_information", "outdated_knowledge", "misleading_response", "policy_compliance", "customer_dissatisfaction",
                       "missing_knowledge", "other")
REVIEW_STATUSES = ("pending_review", "reviewed", "approved_for_update", "rejected")


class InterventionFeedback(Base):
    """What the staff member who interrupted a call says the AI got wrong. A PROPOSED correction, never verified truth: nothing here is
    written into the knowledge base; M6/M7 read the rows an administrator marked `approved_for_update`. The row is created in the same
    transaction as the takeover (so the intervention owner and time are persisted), in state `awaiting`."""

    __tablename__ = "intervention_feedback"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "call_id"], ["calls.tenant_id", "calls.id"]),
        CheckConstraint("state IN ('awaiting','submitted','skipped')", name="state_valid"),
        CheckConstraint("category IS NULL OR category IN ('incorrect_information','outdated_knowledge','misleading_response',"
                        "'policy_compliance','customer_dissatisfaction','missing_knowledge','other')", name="category_valid"),
        CheckConstraint("review_status IS NULL OR review_status IN ('pending_review','reviewed','approved_for_update','rejected')", name="review_valid"),
        CheckConstraint("(state = 'submitted') = (what_was_wrong IS NOT NULL)", name="submitted_has_text"),
        Index("ix_intervention_feedback_review", "tenant_id", "review_status"),
        Index("ix_intervention_feedback_owner", "tenant_id", "staff_subject"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    staff_subject: Mapped[str] = mapped_column(String(64))           # the intervention owner (copied from calls.intervention_owner at takeover)
    intervened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(12), default="awaiting")
    category: Mapped[str | None] = mapped_column(String(32))
    what_was_wrong: Mapped[str | None] = mapped_column(Text)
    suggested_correction: Mapped[str | None] = mapped_column(Text)
    related_message_id: Mapped[str | None] = mapped_column(String(80))   # id of the AI message in the conversation history the staff member pointed at
    related_index: Mapped[int | None] = mapped_column(Integer)           # its position in that history (a second pointer, in case ids are not kept)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_status: Mapped[str | None] = mapped_column(String(24))        # NULL until submitted
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeedbackReview(Base):
    """History of review decisions on a feedback record (who moved it from which status to which, and why)."""

    __tablename__ = "feedback_reviews"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "call_id"], ["intervention_feedback.tenant_id", "intervention_feedback.call_id"]),
        Index("ix_feedback_reviews_call", "tenant_id", "call_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    call_id: Mapped[str] = mapped_column(String(40))
    reviewer: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ----------------------------------------------------------------------------------------------------------------------
# M5: Trial Verification Mode. Settings, audit events, per-call recording consent, recording metadata.
# ----------------------------------------------------------------------------------------------------------------------
CONSENT_STATUSES = ("offered", "granted", "declined", "withdrawn")
RECORDING_STATUSES = ("recording", "completed", "failed", "purged")
WITHDRAWAL_POLICIES = ("delete", "keep_until_expiry")


class TrialSettings(Base):
    """Trial Verification Mode for one tenant (absent row = the defaults in app.trial.settings, mode OFF). `version` makes concurrent edits safe
    (compare-and-set); `consent_policy_version` is bumped whenever something a customer consented to changes."""

    __tablename__ = "trial_settings"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        CheckConstraint("retention_recording_days BETWEEN 1 AND 365", name="rec_days_range"),
        CheckConstraint("retention_consent_days BETWEEN 1 AND 3650", name="consent_days_range"),
        CheckConstraint("retention_feedback_days BETWEEN 1 AND 3650", name="feedback_days_range"),
        CheckConstraint("withdrawal_policy IN ('delete','keep_until_expiry')", name="withdrawal_valid"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    recording_offered: Mapped[bool] = mapped_column(Boolean, default=False)      # customers are asked for recording consent (needs a recording provider)
    disclosure_text: Mapped[str] = mapped_column(Text)
    recording_notice: Mapped[str] = mapped_column(Text)                          # what is recorded, why, for how long (shown with the consent buttons)
    consent_policy_version: Mapped[int] = mapped_column(Integer, default=1)
    retention_recording_days: Mapped[int] = mapped_column(Integer, default=30)
    retention_consent_days: Mapped[int] = mapped_column(Integer, default=365)
    retention_feedback_days: Mapped[int] = mapped_column(Integer, default=365)
    withdrawal_policy: Mapped[str] = mapped_column(String(20), default="delete")
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrialEvent(Base):
    """Audit trail of the trial feature (mode switched, settings changed, purge runs, legal holds, recording access). Counts and ids only."""

    __tablename__ = "trial_events"
    __table_args__ = (ForeignKeyConstraint(["tenant_id"], ["tenants.id"]), Index("ix_trial_events_tenant", "tenant_id", "id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallConsent(Base):
    """The caller's recording consent for ONE call. Granted only by an explicit action in the caller's browser (never inferred from speech or silence)."""

    __tablename__ = "call_consents"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "call_id"], ["calls.tenant_id", "calls.id"]),
        CheckConstraint("status IN ('offered','granted','declined','withdrawn')", name="status_valid"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), default="offered")
    policy_version: Mapped[int] = mapped_column(Integer)
    offered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CallRecording(Base):
    """Metadata of one recording. The audio is NOT here (never a database blob): `storage_ref` is an opaque key the recording provider resolves."""

    __tablename__ = "call_recordings"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "call_id"], ["calls.tenant_id", "calls.id"]),
        CheckConstraint("status IN ('recording','completed','failed','purged')", name="status_valid"),
        Index("ix_call_recordings_expiry", "tenant_id", "status", "expires_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(40))
    consent_policy_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="recording")
    provider: Mapped[str] = mapped_column(String(16))
    storage_ref: Mapped[str | None] = mapped_column(String(120))                 # NULL once purged
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # set when recording stops: stopped_at + the retention period
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    hold_reason: Mapped[str | None] = mapped_column(String(120))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_attempts: Mapped[int] = mapped_column(Integer, default=0)
    purge_error: Mapped[str | None] = mapped_column(String(80))
