"""M6 refinement 2.4: a per-document legal hold for the knowledge store

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-12 09:00:00.000000

Additive only: four columns on kb_documents (a NOT NULL boolean with a constant server default, and three nullable columns), so existing rows need no rewrite
and every existing document starts with no hold. A document under legal hold can never be permanently deleted (app/knowledge/purge.py); archiving and every
other lifecycle step are unaffected. No data is deleted or changed. Downgrade drops the four columns (holds are lost; documents, versions and audit are kept).
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

COLS = [
    sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("legal_hold_reason", sa.String(300), nullable=True),
    sa.Column("legal_hold_by", sa.String(64), nullable=True),
    sa.Column("legal_hold_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    with op.batch_alter_table("kb_documents") as b:
        for c in COLS:
            b.add_column(c)


def downgrade() -> None:
    with op.batch_alter_table("kb_documents") as b:
        for c in COLS:
            b.drop_column(c.name)
