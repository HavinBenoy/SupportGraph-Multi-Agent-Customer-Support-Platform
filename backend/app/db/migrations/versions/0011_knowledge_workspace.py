"""Part 1: knowledge workspace (folders, document placement, change requests and their items)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-09 12:00:00.000000

Purely additive: four new tables, no existing table is altered, nothing is read, rewritten or deleted. Existing documents stay where they are (no placement
row = the root folder). Downgrade drops exactly these five tables.
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_folders",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("folder_id", sa.String(32), nullable=False),
        sa.Column("parent_id", sa.String(32)),
        sa.Column("parent_key", sa.String(32), nullable=False, server_default=""),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("name_key", sa.String(80), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "folder_id", name="pk_kb_folders"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_kb_folders_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "parent_id"], ["kb_folders.tenant_id", "kb_folders.folder_id"], name="fk_kb_folders_parent"),
        sa.UniqueConstraint("tenant_id", "parent_key", "name_key", name="uq_kb_folders_sibling"),
        sa.CheckConstraint("parent_key = COALESCE(parent_id, '')", name="ck_kb_folders_parent_key_matches"),
        sa.CheckConstraint("folder_id <> parent_id", name="ck_kb_folders_not_own_parent"),
    )
    op.create_table(
        "kb_doc_placement",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(128), nullable=False),
        sa.Column("folder_id", sa.String(32), nullable=False),
        sa.Column("moved_by", sa.String(64), nullable=False),
        sa.Column("moved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "doc_id", name="pk_kb_doc_placement"),
        sa.ForeignKeyConstraint(["tenant_id", "doc_id"], ["kb_documents.tenant_id", "kb_documents.doc_id"], name="fk_kb_doc_placement_doc"),
        sa.ForeignKeyConstraint(["tenant_id", "folder_id"], ["kb_folders.tenant_id", "kb_folders.folder_id"], name="fk_kb_doc_placement_folder"),
    )
    op.create_index("ix_kb_doc_placement_folder", "kb_doc_placement", ["tenant_id", "folder_id"])
    op.create_table(
        "kb_change_requests",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(12), nullable=False, server_default="manual"),
        sa.Column("description", sa.String(1000)),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_by", sa.String(64)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(64)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.String(500)),
        sa.Column("applied_by", sa.String(64)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "request_id", name="pk_kb_change_requests"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_kb_change_requests_tenant"),
        sa.CheckConstraint("status IN ('draft','submitted','approved','rejected','cancelled','applied')", name="ck_kb_change_requests_status_valid"),
        sa.CheckConstraint("source IN ('manual','ai_assisted')", name="ck_kb_change_requests_source_valid"),
    )
    op.create_index("ix_kb_change_requests_status", "kb_change_requests", ["tenant_id", "status"])
    op.create_index("ix_kb_change_requests_owner", "kb_change_requests", ["tenant_id", "created_by"])
    op.create_table(
        "kb_change_items",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(32), nullable=False),
        sa.Column("doc_id", sa.String(128), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("base_checksum", sa.String(64), nullable=False),
        sa.Column("proposed_title", sa.String(300), nullable=False),
        sa.Column("proposed_content", sa.Text(), nullable=False),
        sa.Column("proposed_checksum", sa.String(64), nullable=False),
        sa.Column("diff", sa.JSON(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(8), nullable=False, server_default="manual"),
        sa.Column("decision", sa.String(10)),
        sa.Column("rationale", sa.String(1000)),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("result_version", sa.Integer()),
        sa.PrimaryKeyConstraint("tenant_id", "request_id", "doc_id", name="pk_kb_change_items"),
        sa.ForeignKeyConstraint(["tenant_id", "request_id"], ["kb_change_requests.tenant_id", "kb_change_requests.request_id"], name="fk_kb_change_items_request"),
        sa.ForeignKeyConstraint(["tenant_id", "doc_id"], ["kb_documents.tenant_id", "kb_documents.doc_id"], name="fk_kb_change_items_doc"),
        sa.ForeignKeyConstraint(["tenant_id", "doc_id", "base_version"], ["kb_versions.tenant_id", "kb_versions.doc_id", "kb_versions.version_no"],
                                name="fk_kb_change_items_base"),
        sa.CheckConstraint("origin IN ('manual','ai')", name="ck_kb_change_items_origin_valid"),
        sa.CheckConstraint("decision IS NULL OR decision IN ('accepted','manual','discarded')", name="ck_kb_change_items_decision_valid"),
    )
    op.create_index("ix_kb_change_items_doc", "kb_change_items", ["tenant_id", "doc_id"])


def downgrade() -> None:
    op.drop_index("ix_kb_change_items_doc", table_name="kb_change_items")
    op.drop_table("kb_change_items")
    op.drop_index("ix_kb_change_requests_owner", table_name="kb_change_requests")
    op.drop_index("ix_kb_change_requests_status", table_name="kb_change_requests")
    op.drop_table("kb_change_requests")
    op.drop_index("ix_kb_doc_placement_folder", table_name="kb_doc_placement")
    op.drop_table("kb_doc_placement")
    op.drop_table("kb_folders")
