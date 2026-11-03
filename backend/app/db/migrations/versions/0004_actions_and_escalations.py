"""build step 4: tickets, escalations, make-up requests, audit log, students.phone

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02 12:00:00.000000

Purely additive: new tables plus one nullable column. No existing row is read, changed or deleted.
Downgrade drops exactly the objects created here (and therefore the cases/escalations/requests stored in them).
The LangGraph checkpoint tables are NOT created here: the checkpointer library owns them (`make db-setup` calls
`setup_checkpointer`, which is idempotent).
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _fk(cols, target, name):
    return sa.ForeignKeyConstraint(cols, target, name=op.f(name))


def upgrade() -> None:
    op.add_column("students", sa.Column("phone", sa.String(length=20), nullable=True))
    op.create_table(
        "tickets",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("student_id", sa.String(64)),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        _fk(["tenant_id"], ["tenants.id"], "fk_tickets_tenant_id_tenants"),
        _fk(["tenant_id", "student_id"], ["students.tenant_id", "students.id"], "fk_tickets_tenant_id_students"),
        sa.CheckConstraint("status IN ('open','awaiting_approval','escalated','resolved')", name=op.f("ck_tickets_status_valid")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_tickets")),
    )
    op.create_table(
        "escalations",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("ticket_id", sa.String(64), nullable=False),
        sa.Column("team", sa.String(64), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("brief", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        _fk(["tenant_id", "ticket_id"], ["tickets.tenant_id", "tickets.id"], "fk_escalations_tenant_id_tickets"),
        sa.CheckConstraint("status IN ('open','claimed','resolved')", name=op.f("ck_escalations_status_valid")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_escalations")),
    )
    op.create_index("ix_escalations_queue", "escalations", ["tenant_id", "status", "team"])
    op.create_table(
        "makeup_requests",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("student_id", sa.String(64), nullable=False),
        sa.Column("batch_id", sa.String(32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("ticket_id", sa.String(64)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("decided_by", sa.String(64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        _fk(["tenant_id", "student_id"], ["students.tenant_id", "students.id"], "fk_makeup_requests_tenant_id_students"),
        _fk(["tenant_id", "batch_id"], ["batches.tenant_id", "batches.id"], "fk_makeup_requests_tenant_id_batches"),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name=op.f("ck_makeup_requests_status_valid")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_makeup_requests")),
    )
    op.create_index("uq_makeup_idempotency", "makeup_requests", ["tenant_id", "idempotency_key"], unique=True)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("ticket_id", sa.String(64)),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        _fk(["tenant_id"], ["tenants.id"], "fk_audit_log_tenant_id_tenants"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_tenant_ticket", "audit_log", ["tenant_id", "ticket_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("makeup_requests")
    op.drop_table("escalations")
    op.drop_table("tickets")
    op.drop_column("students", "phone")
