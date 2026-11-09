"""M6: knowledge store (documents, immutable versions, staged chunks, proposals, audit events) and documents.kb_version

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-08 09:00:00.000000

Purely additive: one nullable column on `documents` and five new tables. Nothing existing is read, changed or deleted (the live `documents`/`chunks`
rows keep working exactly as before; disk-ingested articles simply have kb_version NULL). Downgrade removes exactly what this adds.
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768  # frozen here, like in 0002: a migration must not change meaning if the app constant changes
CATS = ("courses", "fees", "schedules", "admissions", "placement", "attendance", "examinations", "certificates", "support", "faq", "policies", "other")


def upgrade() -> None:
    op.add_column("documents", sa.Column("kb_version", sa.Integer(), nullable=True))
    emb = sa.JSON().with_variant(Vector(EMBEDDING_DIM), "postgresql")
    cats = ",".join(f"'{c}'" for c in CATS)

    op.create_table(
        "kb_documents",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column("published_version", sa.Integer()),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("archived_by", sa.String(64)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_kb_documents_tenant_id_tenants")),
        sa.CheckConstraint("status IN ('draft','published','archived')", name=op.f("ck_kb_documents_status_valid")),
        sa.CheckConstraint(f"category IN ({cats})", name=op.f("ck_kb_documents_category_valid")),
        sa.CheckConstraint("origin IN ('store','imported')", name=op.f("ck_kb_documents_origin_valid")),
        sa.CheckConstraint("(status = 'published') = (published_version IS NOT NULL)", name=op.f("ck_kb_documents_published_has_version")),
        sa.PrimaryKeyConstraint("tenant_id", "doc_id", name=op.f("pk_kb_documents")),
    )
    op.create_index("ix_kb_documents_tenant_status", "kb_documents", ["tenant_id", "status"])

    op.create_table(
        "kb_versions",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(128), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(120)),
        sa.Column("source_mime", sa.String(40)),
        sa.Column("change_note", sa.String(500), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("restored_from", sa.Integer()),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("processing_error", sa.String(200)),
        sa.Column("chunk_count", sa.Integer()),
        sa.Column("embedding_model", sa.String(100)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published_by", sa.String(64)),
        sa.ForeignKeyConstraint(["tenant_id", "doc_id"], ["kb_documents.tenant_id", "kb_documents.doc_id"], name=op.f("fk_kb_versions_tenant_id_kb_documents")),
        sa.CheckConstraint("version_no >= 1", name=op.f("ck_kb_versions_version_positive")),
        sa.CheckConstraint("status IN ('draft','processing','ready','failed','published','superseded','archived')", name=op.f("ck_kb_versions_status_valid")),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_kb_versions_size_non_negative")),
        sa.PrimaryKeyConstraint("tenant_id", "doc_id", "version_no", name=op.f("pk_kb_versions")),
    )
    op.create_index("ix_kb_versions_checksum", "kb_versions", ["tenant_id", "checksum"])

    op.create_table(
        "kb_version_chunks",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(128), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(400), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("embedding", emb, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "doc_id", "version_no"], ["kb_versions.tenant_id", "kb_versions.doc_id", "kb_versions.version_no"],
                                name=op.f("fk_kb_version_chunks_tenant_id_kb_versions"), ondelete="CASCADE"),
        sa.CheckConstraint("chunk_index >= 0", name=op.f("ck_kb_version_chunks_chunk_index_non_negative")),
        sa.PrimaryKeyConstraint("tenant_id", "doc_id", "version_no", "chunk_index", name=op.f("pk_kb_version_chunks")),
    )

    op.create_table(
        "kb_proposals",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("call_id", sa.String(40), nullable=False),
        sa.Column("doc_id", sa.String(128), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("proposed_by", sa.String(64), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_by", sa.String(64)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.String(500)),
        sa.Column("published_by", sa.String(64)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id", "call_id"], ["intervention_feedback.tenant_id", "intervention_feedback.call_id"],
                                name=op.f("fk_kb_proposals_tenant_id_intervention_feedback")),
        sa.ForeignKeyConstraint(["tenant_id", "doc_id", "version_no"], ["kb_versions.tenant_id", "kb_versions.doc_id", "kb_versions.version_no"],
                                name=op.f("fk_kb_proposals_tenant_id_kb_versions")),
        sa.CheckConstraint("status IN ('proposed','approved','rejected','published')", name=op.f("ck_kb_proposals_status_valid")),
        sa.PrimaryKeyConstraint("tenant_id", "call_id", name=op.f("pk_kb_proposals")),
    )
    op.create_index("ix_kb_proposals_version", "kb_proposals", ["tenant_id", "doc_id", "version_no"], unique=True)

    op.create_table(
        "kb_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("resource_type", sa.String(20), nullable=False),
        sa.Column("resource_id", sa.String(160), nullable=False),
        sa.Column("doc_id", sa.String(128)),
        sa.Column("version_no", sa.Integer()),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_kb_events_tenant_id_tenants")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kb_events")),
    )
    op.create_index("ix_kb_events_tenant", "kb_events", ["tenant_id", "id"])
    op.create_index("ix_kb_events_doc", "kb_events", ["tenant_id", "doc_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_kb_events_doc", table_name="kb_events")
    op.drop_index("ix_kb_events_tenant", table_name="kb_events")
    op.drop_table("kb_events")
    op.drop_index("ix_kb_proposals_version", table_name="kb_proposals")
    op.drop_table("kb_proposals")
    op.drop_table("kb_version_chunks")
    op.drop_index("ix_kb_versions_checksum", table_name="kb_versions")
    op.drop_table("kb_versions")
    op.drop_index("ix_kb_documents_tenant_status", table_name="kb_documents")
    op.drop_table("kb_documents")
    op.drop_column("documents", "kb_version")
