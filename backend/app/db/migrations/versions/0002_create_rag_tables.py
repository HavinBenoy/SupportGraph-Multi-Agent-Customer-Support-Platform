"""create rag tables (documents, chunks)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26 12:00:00.000000

Notes
- Ensures the pgvector extension exists. docker-compose's init script only runs when the data volume is first
  created, so a database created before build step 2 might not have it yet. Needs a role allowed to CREATE EXTENSION
  (the compose default user is a superuser).
- No ANN (HNSW/IVFFlat) index on purpose: the corpus is a few hundred chunks per tenant, exact search is fast and
  exact, and an approximate index combined with a tenant filter can silently return fewer than k rows. Revisit when
  a tenant has tens of thousands of chunks.
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768  # frozen here on purpose: a migration must not change meaning if the app constant changes


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("source_path", sa.String(length=300), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("last_reviewed", sa.Date(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("audience = 'customer'", name=op.f("ck_documents_audience_customer_only")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_documents_tenant_id_tenants")),
        sa.PrimaryKeyConstraint("tenant_id", "doc_id", name=op.f("pk_documents")),
    )
    op.create_table(
        "chunks",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=400), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.CheckConstraint("audience = 'customer'", name=op.f("ck_chunks_audience_customer_only")),
        sa.CheckConstraint("chunk_index >= 0", name=op.f("ck_chunks_chunk_index_non_negative")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "doc_id"], ["documents.tenant_id", "documents.doc_id"],
            name=op.f("fk_chunks_tenant_id_documents"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "doc_id", "chunk_index", name=op.f("pk_chunks")),
    )
    op.create_index("ix_chunks_tenant_model", "chunks", ["tenant_id", "embedding_model"], unique=False)


def downgrade() -> None:
    # Drops only the derived index tables. The pgvector extension is left installed (other things may use it).
    op.drop_index("ix_chunks_tenant_model", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("documents")
