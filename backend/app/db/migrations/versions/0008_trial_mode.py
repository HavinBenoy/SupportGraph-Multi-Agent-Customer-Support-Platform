"""M5: Trial Verification Mode (settings, audit events, recording consent, recording metadata) and calls.trial_mode

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-07 21:00:00.000000

Purely additive: one column with a default on `calls` and four new tables. Nothing existing is read, changed or deleted. Recording audio is
never stored in the database (call_recordings holds metadata and an opaque storage reference). Downgrade removes exactly what this adds.
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("trial_mode", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "trial_settings",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recording_offered", sa.Boolean(), nullable=False),
        sa.Column("disclosure_text", sa.Text(), nullable=False),
        sa.Column("recording_notice", sa.Text(), nullable=False),
        sa.Column("consent_policy_version", sa.Integer(), nullable=False),
        sa.Column("retention_recording_days", sa.Integer(), nullable=False),
        sa.Column("retention_consent_days", sa.Integer(), nullable=False),
        sa.Column("retention_feedback_days", sa.Integer(), nullable=False),
        sa.Column("withdrawal_policy", sa.String(20), nullable=False),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_trial_settings_tenant_id_tenants")),
        sa.CheckConstraint("retention_recording_days BETWEEN 1 AND 365", name=op.f("ck_trial_settings_rec_days_range")),
        sa.CheckConstraint("retention_consent_days BETWEEN 1 AND 3650", name=op.f("ck_trial_settings_consent_days_range")),
        sa.CheckConstraint("retention_feedback_days BETWEEN 1 AND 3650", name=op.f("ck_trial_settings_feedback_days_range")),
        sa.CheckConstraint("withdrawal_policy IN ('delete','keep_until_expiry')", name=op.f("ck_trial_settings_withdrawal_valid")),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_trial_settings")),
    )
    op.create_table(
        "trial_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_trial_events_tenant_id_tenants")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trial_events")),
    )
    op.create_index("ix_trial_events_tenant", "trial_events", ["tenant_id", "id"])
    op.create_table(
        "call_consents",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("call_id", sa.String(40), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("offered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id", "call_id"], ["calls.tenant_id", "calls.id"], name=op.f("fk_call_consents_tenant_id_calls")),
        sa.CheckConstraint("status IN ('offered','granted','declined','withdrawn')", name=op.f("ck_call_consents_status_valid")),
        sa.PrimaryKeyConstraint("tenant_id", "call_id", name=op.f("pk_call_consents")),
    )
    op.create_table(
        "call_recordings",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("call_id", sa.String(40), nullable=False),
        sa.Column("consent_policy_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("storage_ref", sa.String(120)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("stop_reason", sa.String(32)),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hold_reason", sa.String(120)),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.Column("purge_attempts", sa.Integer(), nullable=False),
        sa.Column("purge_error", sa.String(80)),
        sa.ForeignKeyConstraint(["tenant_id", "call_id"], ["calls.tenant_id", "calls.id"], name=op.f("fk_call_recordings_tenant_id_calls")),
        sa.CheckConstraint("status IN ('recording','completed','failed','purged')", name=op.f("ck_call_recordings_status_valid")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_call_recordings")),
    )
    op.create_index("ix_call_recordings_expiry", "call_recordings", ["tenant_id", "status", "expires_at"])


def downgrade() -> None:
    op.drop_table("call_recordings")
    op.drop_table("call_consents")
    op.drop_table("trial_events")
    op.drop_table("trial_settings")
    with op.batch_alter_table("calls") as b:
        b.drop_column("trial_mode")
