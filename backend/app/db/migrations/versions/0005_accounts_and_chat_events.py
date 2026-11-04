"""build step 5: accounts (logins) and chat_events (analytics)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03 14:00:00.000000

Purely additive: two new tables. No existing row is read, changed or deleted. Downgrade drops exactly these two tables.
No account is created here: use `make demo-accounts` (sample tenant) or `scripts/create_account.py`.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("student_id", sa.String(64)),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_accounts_tenant_id_tenants")),
        sa.CheckConstraint("role IN ('student','staff','admin')", name=op.f("ck_accounts_role_valid")),
        sa.CheckConstraint("(role = 'student') = (student_id IS NOT NULL)", name=op.f("ck_accounts_student_role_has_student_id")),
        sa.PrimaryKeyConstraint("tenant_id", "username", name=op.f("pk_accounts")),
    )
    op.create_table(
        "chat_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("ticket_id", sa.String(80), nullable=False),
        sa.Column("signed_in", sa.Boolean(), nullable=False),
        sa.Column("route", sa.String(16), nullable=False),
        sa.Column("route_reason", sa.String(64), nullable=False),
        sa.Column("abstained", sa.Boolean(), nullable=False),
        sa.Column("qa_blocked", sa.Boolean(), nullable=False),
        sa.Column("needs_human", sa.Boolean(), nullable=False),
        sa.Column("awaiting", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_chat_events_tenant_id_tenants")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_events")),
    )
    op.create_index("ix_chat_events_tenant_time", "chat_events", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_table("chat_events")
    op.drop_table("accounts")
