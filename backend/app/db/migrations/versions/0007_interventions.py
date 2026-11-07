"""M4.1: intervention owner on calls, intervention feedback and its review history

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07 20:00:00.000000

Purely additive: one nullable column on `calls` and two new tables. No existing row is changed or deleted. Rows that already exist keep
intervention_owner NULL (the owner of an old takeover was not persisted); they simply have no feedback form. Downgrade removes exactly
what this revision adds. The `returning_to_ai` status stays valid in the CHECK constraint (migration 0006 is not rewritten) so that
rows written by M4 remain legal; the application no longer moves any call into it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("intervention_owner", sa.String(64)))
    op.create_table(
        "intervention_feedback",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("call_id", sa.String(40), nullable=False),
        sa.Column("staff_subject", sa.String(64), nullable=False),
        sa.Column("intervened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("category", sa.String(32)),
        sa.Column("what_was_wrong", sa.Text()),
        sa.Column("suggested_correction", sa.Text()),
        sa.Column("related_message_id", sa.String(80)),
        sa.Column("related_index", sa.Integer()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("skipped_at", sa.DateTime(timezone=True)),
        sa.Column("review_status", sa.String(24)),
        sa.Column("reviewed_by", sa.String(64)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "call_id"], ["calls.tenant_id", "calls.id"], name=op.f("fk_intervention_feedback_tenant_id_calls")),
        sa.CheckConstraint("state IN ('awaiting','submitted','skipped')", name=op.f("ck_intervention_feedback_state_valid")),
        sa.CheckConstraint("category IS NULL OR category IN ('incorrect_information','outdated_knowledge','misleading_response',"
                           "'policy_compliance','customer_dissatisfaction','missing_knowledge','other')", name=op.f("ck_intervention_feedback_category_valid")),
        sa.CheckConstraint("review_status IS NULL OR review_status IN ('pending_review','reviewed','approved_for_update','rejected')",
                           name=op.f("ck_intervention_feedback_review_valid")),
        sa.CheckConstraint("(state = 'submitted') = (what_was_wrong IS NOT NULL)", name=op.f("ck_intervention_feedback_submitted_has_text")),
        sa.PrimaryKeyConstraint("tenant_id", "call_id", name=op.f("pk_intervention_feedback")),
    )
    op.create_index("ix_intervention_feedback_review", "intervention_feedback", ["tenant_id", "review_status"])
    op.create_index("ix_intervention_feedback_owner", "intervention_feedback", ["tenant_id", "staff_subject"])
    op.create_table(
        "feedback_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("call_id", sa.String(40), nullable=False),
        sa.Column("reviewer", sa.String(64), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=False),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "call_id"], ["intervention_feedback.tenant_id", "intervention_feedback.call_id"],
                                name=op.f("fk_feedback_reviews_tenant_id_intervention_feedback")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback_reviews")),
    )
    op.create_index("ix_feedback_reviews_call", "feedback_reviews", ["tenant_id", "call_id", "id"])


def downgrade() -> None:
    op.drop_table("feedback_reviews")
    op.drop_table("intervention_feedback")
    with op.batch_alter_table("calls") as b:
        b.drop_column("intervention_owner")
