"""M6.1: fencing token for knowledge-version processing (kb_versions.processing_attempt)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-08 12:00:00.000000

Purely additive: one NOT NULL column with a constant server default (0), so existing rows need no rewrite or backfill and the change is safe on a populated table.
Downgrade drops exactly that column.
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kb_versions", sa.Column("processing_attempt", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("kb_versions", "processing_attempt")
