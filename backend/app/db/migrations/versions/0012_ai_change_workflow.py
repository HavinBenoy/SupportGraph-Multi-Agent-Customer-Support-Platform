"""Part 2: AI-assisted change requests, the extended request state machine and the integrated process-and-publish release

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-10 09:00:00.000000

Additive columns on kb_change_requests and kb_change_items (all nullable or with constant defaults, so existing rows need no rewrite). ONE existing object is
replaced: the request `status` column is widened (VARCHAR(12) -> VARCHAR(24)) and its CHECK constraint is re-created with the larger set of states, because the
state machine grew from 6 states to 15. Every old value stays valid. No data is deleted.

Downgrade maps the new states back onto the Part 1 ones (so the old CHECK holds) and drops the new columns; AI suggestions, staff/admin decisions and release
records are lost by that downgrade (documents, versions and audit events are not touched).
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

NEW = ("draft", "analysing", "awaiting_clarification", "awaiting_decisions", "submitted", "under_review", "needs_revision", "rejected", "approved", "processing",
       "publishing", "published", "failed", "cancelled", "applied")
OLD = ("draft", "submitted", "approved", "rejected", "cancelled", "applied")
_IN = lambda vals: "status IN (" + ",".join(f"'{v}'" for v in vals) + ")"          # noqa: E731

REQ_COLS = [
    sa.Column("reference_doc_id", sa.String(128), nullable=True), sa.Column("analysis", sa.JSON(), nullable=True),
    sa.Column("analysis_attempt", sa.Integer(), nullable=False, server_default="0"), sa.Column("analysis_started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("analysis_completed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("return_note", sa.String(500), nullable=True),
    sa.Column("release_attempt", sa.Integer(), nullable=False, server_default="0"), sa.Column("phase_started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("release", sa.JSON(), nullable=True), sa.Column("failure", sa.JSON(), nullable=True), sa.Column("published_by", sa.String(64), nullable=True),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
]
ITEM_COLS = [
    sa.Column("classification", sa.String(20), nullable=True), sa.Column("confidence", sa.Float(), nullable=True), sa.Column("ai_suggestion", sa.JSON(), nullable=True),
    sa.Column("final_edits", sa.JSON(), nullable=True), sa.Column("decided_by", sa.String(64), nullable=True), sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("admin_decision", sa.String(10), nullable=True), sa.Column("admin_note", sa.String(500), nullable=True), sa.Column("admin_by", sa.String(64), nullable=True),
    sa.Column("admin_at", sa.DateTime(timezone=True), nullable=True), sa.Column("admin_edited", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("process_status", sa.String(12), nullable=True), sa.Column("publish_status", sa.String(14), nullable=True), sa.Column("process_error", sa.String(200), nullable=True),
]
ITEM_CHECKS = {
    "ck_kb_change_items_classification_valid": "classification IS NULL OR classification IN ('definite','potential','related_no_change')",
    "ck_kb_change_items_admin_decision_valid": "admin_decision IS NULL OR admin_decision IN ('confirmed','excluded')",
    "ck_kb_change_items_process_status_valid": "process_status IS NULL OR process_status IN ('pending','processing','ready','failed')",
    "ck_kb_change_items_publish_status_valid": "publish_status IS NULL OR publish_status IN ('pending','published','not_published','failed')",
}


def upgrade() -> None:
    with op.batch_alter_table("kb_change_requests") as b:
        b.drop_constraint("ck_kb_change_requests_status_valid", type_="check")
        b.alter_column("status", existing_type=sa.String(12), type_=sa.String(24), existing_nullable=False, existing_server_default="draft")
        for c in REQ_COLS:
            b.add_column(c)
        b.create_check_constraint("ck_kb_change_requests_status_valid", _IN(NEW))
    with op.batch_alter_table("kb_change_items") as b:
        for c in ITEM_COLS:
            b.add_column(c)
        for name, expr in ITEM_CHECKS.items():
            b.create_check_constraint(name, expr)


def downgrade() -> None:
    op.execute("UPDATE kb_change_requests SET status = CASE status "
               "WHEN 'analysing' THEN 'draft' WHEN 'awaiting_clarification' THEN 'draft' WHEN 'awaiting_decisions' THEN 'draft' WHEN 'needs_revision' THEN 'draft' "
               "WHEN 'under_review' THEN 'submitted' WHEN 'processing' THEN 'approved' WHEN 'publishing' THEN 'approved' WHEN 'failed' THEN 'approved' "
               "WHEN 'published' THEN 'applied' ELSE status END")
    with op.batch_alter_table("kb_change_items") as b:
        for name in ITEM_CHECKS:
            b.drop_constraint(name, type_="check")
        for c in ITEM_COLS:
            b.drop_column(c.name)
    with op.batch_alter_table("kb_change_requests") as b:
        b.drop_constraint("ck_kb_change_requests_status_valid", type_="check")
        for c in REQ_COLS:
            b.drop_column(c.name)
        b.alter_column("status", existing_type=sa.String(24), type_=sa.String(12), existing_nullable=False, existing_server_default="draft")
        b.create_check_constraint("ck_kb_change_requests_status_valid", _IN(OLD))
