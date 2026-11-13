"""M8: telephone channel, caller identity status, staff phone numbers, phone-support PINs, telephony audit

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-12 12:00:00.000000

Additive only. Nine nullable columns and one NOT NULL column with a constant server default ("web") on `calls`, so every existing call keeps its data and
reads as a web call. Three new tables. No existing row is changed or deleted. Downgrade drops exactly what this revision added (phone numbers of staff,
PINs and the telephony audit are lost; calls, call events, accounts and students are kept).
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

CALL_COLS = [
    sa.Column("channel", sa.String(16), nullable=False, server_default="web"),
    sa.Column("caller_status", sa.String(16), nullable=True),
    sa.Column("caller_role", sa.String(16), nullable=True),
    sa.Column("caller_masked", sa.String(24), nullable=True),
    sa.Column("caller_hash", sa.String(64), nullable=True),
    sa.Column("provider", sa.String(16), nullable=True),
    sa.Column("provider_call_ref", sa.String(80), nullable=True),
    sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    with op.batch_alter_table("calls") as b:
        for c in CALL_COLS:
            b.add_column(c)
    op.create_table(
        "account_phones",
        sa.Column("tenant_id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(64), primary_key=True),
        sa.Column("phone_e164", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "username"], ["accounts.tenant_id", "accounts.username"]),
    )
    op.create_index("ix_account_phones_number", "account_phones", ["tenant_id", "phone_e164"])
    op.create_table(
        "phone_pins",
        sa.Column("tenant_id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(64), primary_key=True),
        sa.Column("pin_hash", sa.String(300), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id", "username"], ["accounts.tenant_id", "accounts.username"]),
    )
    op.create_table(
        "telephony_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("subject", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telephony_audit_tenant", "telephony_audit", ["tenant_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_telephony_audit_tenant", table_name="telephony_audit")
    op.drop_table("telephony_audit")
    op.drop_table("phone_pins")
    op.drop_index("ix_account_phones_number", table_name="account_phones")
    op.drop_table("account_phones")
    with op.batch_alter_table("calls") as b:
        for c in reversed(CALL_COLS):
            b.drop_column(c.name)
