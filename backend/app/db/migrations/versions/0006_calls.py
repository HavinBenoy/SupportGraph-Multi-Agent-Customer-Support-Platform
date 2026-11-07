"""M4: calls and call_events (call sessions, staff listening, human takeover)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07 17:00:00.000000

Purely additive: two new tables. No existing row is read, changed or deleted. Downgrade drops exactly these two tables.
Rows hold identifiers, states and timestamps only: no audio, no transcripts.
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calls",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("ticket_id", sa.String(80), nullable=False),
        sa.Column("student_id", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("owner", sa.String(8), nullable=False),
        sa.Column("owner_subject", sa.String(64)),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True)),
        sa.Column("takeover_at", sa.DateTime(timezone=True)),
        sa.Column("returned_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.String(32)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_calls_tenant_id_tenants")),
        sa.CheckConstraint("status IN ('created','active_ai','staff_takeover','returning_to_ai','ended','failed')", name=op.f("ck_calls_status_valid")),
        sa.CheckConstraint("owner IN ('ai','staff','none')", name=op.f("ck_calls_owner_valid")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_calls")),
    )
    op.create_index("ix_calls_tenant_status", "calls", ["tenant_id", "status"])
    op.create_table(
        "call_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("call_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "call_id"], ["calls.tenant_id", "calls.id"], name=op.f("fk_call_events_tenant_id_calls")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_events")),
    )
    op.create_index("ix_call_events_call", "call_events", ["tenant_id", "call_id", "id"])


def downgrade() -> None:
    op.drop_table("call_events")
    op.drop_table("calls")
