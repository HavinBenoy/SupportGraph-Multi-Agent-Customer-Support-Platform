"""Ingestion: user documents on disk -> chunks + embeddings in Postgres.

Idempotent by design: running it twice does nothing the second time. A document is re-chunked and re-embedded only
when something that affects its vectors changed:
  * the file's content (sha256),
  * how it is chunked (the chunk texts are recomputed and compared with what is stored), or
  * the embedding model (the model name is stored with every vector).
Embedding is the slow, sometimes paid step, so skipping unchanged documents matters.
Example: editing one sentence in returns-and-refunds.md re-embeds that one article, not all six.

Documents that disappeared from disk are removed, so a withdrawn policy stops being quoted to users.

SECURITY: only app.rag.documents.load_public_documents() is used to read files. It reads `docs/` only and refuses
anything not marked audience: public, so staff-only policy in `internal/` has no path into this table.
Everything here is scoped to ONE tenant_id; it never reads or deletes another tenant's rows.
"""

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, KbDocument, Tenant
from app.llm.embeddings import EmbeddingClient
from app.rag.chunking import DEFAULT_MAX_CHARS, TextChunk, chunk_markdown
from app.rag.documents import IngestError, SourceDoc, load_public_documents
from app.tenants.loader import TENANTS_DIR

EMBED_BATCH = 32  # texts per embedding call: keeps requests small for local servers and rate-limited APIs


@dataclass
class IngestReport:
    tenant_id: str
    embedding_model: str
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    managed_skipped: list[str] = field(default_factory=list)   # M6: articles owned by the knowledge store; disk ingest never touches them
    chunks_written: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)


def _is_current(db: Session, doc: SourceDoc, chunks: list[TextChunk], model_name: str, stored: Document | None) -> bool:
    """True when the stored rows already equal what ingesting would produce, so nothing needs embedding."""
    if stored is None or stored.content_hash != doc.content_hash or stored.title != doc.title:
        return False
    rows = db.execute(select(Chunk).where(Chunk.tenant_id == stored.tenant_id, Chunk.doc_id == doc.doc_id)
                      .order_by(Chunk.chunk_index)).scalars().all()
    return len(rows) == len(chunks) and all(
        r.chunk_index == c.index and r.heading == c.heading and r.text == c.text and r.embedding_model == model_name
        for r, c in zip(rows, chunks))


def _embed(embedder: EmbeddingClient, chunks: list[TextChunk]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        vectors.extend(embedder.embed_documents([c.embedding_input for c in chunks[i:i + EMBED_BATCH]]))
    return vectors


def ingest_tenant(db: Session, tenant_id: str, embedder: EmbeddingClient, *, tenants_dir=TENANTS_DIR,
                  max_chars: int = DEFAULT_MAX_CHARS, force: bool = False) -> IngestReport:
    """Bring `tenant_id`'s searchable passages in line with its docs folder. The caller commits (session_scope).

    Embeds first and writes second: if the embedding service fails midway, nothing half-written is left behind
    (and the caller's transaction rolls back anyway).
    """
    if db.get(Tenant, tenant_id) is None:
        raise IngestError(f"Tenant '{tenant_id}' is not in the database yet. Load it first: make db-seed")

    docs = load_public_documents(tenant_id, tenants_dir)  # raises on anything that is not public
    report = IngestReport(tenant_id=tenant_id, embedding_model=embedder.name)
    # M6: a document that the knowledge store manages (drafts, versions, publication, archive) is changed ONLY through the store. Disk ingest neither
    # overwrites it (that would bypass versioning and publication) nor withdraws it because it has no file.
    managed = set(db.execute(select(KbDocument.doc_id).where(KbDocument.tenant_id == tenant_id)).scalars())
    report.managed_skipped = sorted(d.doc_id for d in docs if d.doc_id in managed)
    docs = [d for d in docs if d.doc_id not in managed]
    on_disk = {d.doc_id for d in docs}

    # 1. Work out what is stale and embed it, without touching the database yet.
    pending: list[tuple[SourceDoc, list[TextChunk], list[list[float]]]] = []
    for doc in docs:
        stored = db.get(Document, (tenant_id, doc.doc_id))
        chunks = chunk_markdown(doc.body, doc_title=doc.title, max_chars=max_chars)
        if not chunks:
            raise IngestError(f"{doc.rel_path}: no content to index after the title")
        if not force and _is_current(db, doc, chunks, embedder.name, stored):
            report.unchanged.append(doc.doc_id)
            continue
        (report.updated if stored is not None else report.added).append(doc.doc_id)
        pending.append((doc, chunks, _embed(embedder, chunks)))

    # 2. Write. Delete-then-insert per document keeps its chunks consistent (a shorter article leaves no orphans).
    for doc, chunks, vectors in pending:
        db.execute(delete(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.doc_id == doc.doc_id))
        db.merge(Document(tenant_id=tenant_id, doc_id=doc.doc_id, title=doc.title, audience="public",
                          source_path=doc.rel_path, content_hash=doc.content_hash, last_reviewed=doc.last_reviewed))
        db.flush()  # the document row must exist before chunks reference it
        db.add_all(Chunk(tenant_id=tenant_id, doc_id=doc.doc_id, chunk_index=c.index, heading=c.heading, text=c.text,
                         token_count=c.token_count, audience="public", embedding_model=embedder.name, embedding=v)
                   for c, v in zip(chunks, vectors))
        report.chunks_written += len(chunks)

    # 3. Withdraw documents that no longer exist on disk (this tenant only).
    for (doc_id,) in db.execute(select(Document.doc_id).where(Document.tenant_id == tenant_id)).all():
        if doc_id not in on_disk and doc_id not in managed:
            db.execute(delete(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.doc_id == doc_id))
            db.execute(delete(Document).where(Document.tenant_id == tenant_id, Document.doc_id == doc_id))
            report.removed.append(doc_id)
    db.flush()
    return report
