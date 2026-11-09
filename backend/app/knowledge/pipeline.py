"""Processing and publication of knowledge versions.

  process_version  chunk + embed ONE version into STAGED chunks (kb_version_chunks). Retrieval never reads them. Slow (embedding), so it runs in three short
                   transactions with the embedding call outside any of them. Idempotent: running it again replaces the staged rows, never adds to them.
  publish_version  ONE transaction: copy that version's staged chunks into the live `documents`/`chunks` tables (the existing RAG index), mark the version
                   published and the old one superseded. If anything fails the transaction rolls back and the previous live version is exactly as it was.
  unpublish / archive / unarchive   take a document out of (or back toward) retrieval; versions are never deleted.

Invariant: staged chunks exist exactly while a version is `ready`. The live index is only ever written by publish (and unpublish/archive deleting from it)."""

import logging
from datetime import date, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Chunk, Document, KbProposal, KbVersion, KbVersionChunk
from app.db.session import session_scope
from app.knowledge import audit
from app.knowledge import store
from app.knowledge.errors import Conflict, KbError
from app.rag.chunking import DEFAULT_MAX_CHARS, chunk_markdown
from app.rag.ingest import _embed

log = logging.getLogger("supportgraph.knowledge")


def _staged(db: Session, tenant_id: str, doc_id: str, version_no: int) -> list[KbVersionChunk]:
    return list(db.execute(select(KbVersionChunk).where(KbVersionChunk.tenant_id == tenant_id, KbVersionChunk.doc_id == doc_id,
                                                        KbVersionChunk.version_no == version_no).order_by(KbVersionChunk.chunk_index)).scalars())


def _drop_staged(db: Session, tenant_id: str, doc_id: str, version_no: int) -> None:
    db.execute(delete(KbVersionChunk).where(KbVersionChunk.tenant_id == tenant_id, KbVersionChunk.doc_id == doc_id, KbVersionChunk.version_no == version_no))


def _drop_live(db: Session, tenant_id: str, doc_id: str) -> None:
    db.execute(delete(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.doc_id == doc_id))
    db.execute(delete(Document).where(Document.tenant_id == tenant_id, Document.doc_id == doc_id))


# ------------------------------------------------------------------------------------------------------------------------------ processing
def process_version(tenant_id: str, actor: str, doc_id: str, version_no: int, embedder, s: Settings, *, force: bool = False) -> dict:
    """Chunk and embed one version. Returns {"status", "chunks", "model", "unchanged"}. Raises KbError for refusals (404/409); an embedding or chunking
    failure is NOT raised: it is recorded (status `failed`, audit event) and returned as {"status": "failed", "error": ...}, leaving the live version untouched.

    Ownership (fencing). Every claim increments `kb_versions.processing_attempt` and the worker keeps that number. Embedding runs with no transaction held, so by
    the time a worker finishes, a newer worker may have taken over its (stale) claim. Therefore EVERY write that completes or fails an attempt is a
    compare-and-set on (status = 'processing' AND processing_attempt = <my number>), performed FIRST in its transaction (on PostgreSQL that UPDATE takes the row
    lock), and everything else the transaction does (staged-row writes or deletes, the audit event) happens only if it matched exactly one row. A worker whose
    attempt is no longer current gets Conflict, changes nothing, and leaves one `ingestion_superseded` audit event naming its attempt."""
    cutoff = store.now() - timedelta(seconds=s.kb_processing_stale_s)
    key = (KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc_id, KbVersion.version_no == version_no)
    ref = f"{doc_id}@{version_no}"

    # ---- 1. look, decide, claim (short transaction) ----
    with session_scope() as db:
        doc = store.get_doc(db, tenant_id, doc_id)
        ver = store.get_version(db, tenant_id, doc_id, version_no)
        if doc.status == "archived":
            raise Conflict("this document is archived")
        title, body = ver.title, ver.content
        chunks = chunk_markdown(body, doc_title=title, max_chars=DEFAULT_MAX_CHARS)
        old = ver.status
        if old in ("published", "superseded", "archived"):
            raise Conflict(f"version {version_no} is {old}: it cannot be processed (create a new version to change content)")
        if old == "ready" and not force:
            have = _staged(db, tenant_id, doc_id, version_no)
            if (ver.embedding_model == embedder.name and len(have) == len(chunks)
                    and all(h.chunk_index == c.index and h.heading == c.heading and h.text == c.text and h.embedding_model == embedder.name for h, c in zip(have, chunks))):
                return {"status": "ready", "chunks": len(chunks), "model": embedder.name, "unchanged": True}
        cond = [*key]
        if old == "processing":
            cond += [KbVersion.status == "processing", KbVersion.processing_started_at < cutoff]
        else:
            cond += [KbVersion.status == old]
        claimed = db.execute(update(KbVersion).where(*cond).values(status="processing", processing_started_at=store.now(), processing_error=None,
                                                                    processing_attempt=KbVersion.processing_attempt + 1)
                             .execution_options(synchronize_session=False)).rowcount
        if claimed != 1:
            raise Conflict("this version is already being processed")
        # Read back inside the same transaction: the UPDATE above holds the row, so this is exactly the number this claim produced.
        attempt = db.execute(select(KbVersion.processing_attempt).where(*key)).scalar_one()
        audit.record(db, tenant_id, actor, "ingestion_started", "version", ref, doc_id=doc_id, version_no=version_no,
                     from_status=old, model=embedder.name, stale_takeover=(old == "processing"), attempt=attempt)

    mine = [*key, KbVersion.status == "processing", KbVersion.processing_attempt == attempt]          # "I still own the current attempt"

    def superseded(db: Session) -> None:
        current = db.execute(select(KbVersion.processing_attempt).where(*key)).scalar_one_or_none()
        audit.record(db, tenant_id, actor, "ingestion_superseded", "version", ref, doc_id=doc_id, version_no=version_no, attempt=attempt, current_attempt=current or 0)

    def lost() -> Conflict:
        return Conflict("processing was superseded by a newer attempt: this result was discarded")

    # ---- 2. embed (no transaction held; may be slow or fail) ----
    err: str | None = None
    vectors: list = []
    try:
        if not chunks:
            raise ValueError("nothing to index")
        if len(chunks) > s.kb_max_chunks:
            raise ValueError("too many passages")
        vectors = _embed(embedder, chunks)
        if len(vectors) != len(chunks):
            raise ValueError("embedding count mismatch")
    except Exception as e:  # noqa: BLE001 - recorded, never raised: the previous published version is untouched
        err = _safe_error(e)
        log.warning("knowledge: processing %s@%s attempt %s failed (%s)", doc_id, version_no, attempt, err)

    # ---- 3. fail or complete the attempt (one transaction; the compare-and-set comes first) ----
    owned = True
    with session_scope() as db:
        doc = store.get_doc(db, tenant_id, doc_id)
        if err is None and doc.status == "archived":
            err = "Conflict: the document was archived while it was being processed"       # the attempt ends cleanly instead of stranding the version in `processing`
        if err is not None:
            if db.execute(update(KbVersion).where(*mine).values(status="failed", processing_error=err, chunk_count=None, embedding_model=None)
                          .execution_options(synchronize_session=False)).rowcount == 1:
                _drop_staged(db, tenant_id, doc_id, version_no)                              # only the owner of the attempt may touch the staged rows
                audit.record(db, tenant_id, actor, "ingestion_failed", "version", ref, doc_id=doc_id, version_no=version_no, error=err, attempt=attempt)
            else:
                owned = False
                superseded(db)
        elif db.execute(update(KbVersion).where(*mine).values(status="ready", processed_at=store.now(), processing_error=None, chunk_count=len(chunks),
                                                              embedding_model=embedder.name).execution_options(synchronize_session=False)).rowcount == 1:
            _drop_staged(db, tenant_id, doc_id, version_no)
            db.flush()
            db.add_all(KbVersionChunk(tenant_id=tenant_id, doc_id=doc_id, version_no=version_no, chunk_index=c.index, heading=c.heading, text=c.text,
                                      token_count=c.token_count, embedding_model=embedder.name, embedding=vec) for c, vec in zip(chunks, vectors))
            audit.record(db, tenant_id, actor, "ingestion_completed", "version", ref, doc_id=doc_id, version_no=version_no,
                         chunks=len(chunks), model=embedder.name, unchanged=False, attempt=attempt)
        else:
            owned = False
            superseded(db)                                                                  # nothing else was written; only this audit row is committed
    if not owned:
        raise lost()
    if err is not None:
        return {"status": "failed", "error": err, "chunks": 0, "model": embedder.name, "unchanged": False}
    return {"status": "ready", "chunks": len(chunks), "model": embedder.name, "unchanged": False}


def _safe_error(e: Exception) -> str:
    """A short, non-sensitive reason (the exception class, plus our own message for our own refusals). Never the text that was being embedded."""
    if isinstance(e, (ValueError, KbError)):
        return f"{type(e).__name__}: {e}"[:190]
    return f"{type(e).__name__}"[:190]


# ----------------------------------------------------------------------------------------------------------------------------- publication
def publish_version(db: Session, tenant_id: str, actor: str, doc_id: str, version_no: int, embedder_name: str, *, expected_row_version: int | None = None) -> dict:
    """Make `version_no` the live, retrievable version. Atomic: the caller's transaction commits or rolls back as a whole."""
    doc = store.get_doc(db, tenant_id, doc_id)
    ver = store.get_version(db, tenant_id, doc_id, version_no)
    if doc.status == "archived":
        raise Conflict("this document is archived: unarchive it first")
    if ver.status == "published":
        return {"doc_id": doc_id, "version": version_no, "unchanged": True, "chunks": ver.chunk_count or 0}
    if ver.status != "ready":
        raise Conflict(f"version {version_no} is {store.LIFECYCLE[ver.status]}: only a processed version that is ready for review can be published")
    prop = db.execute(select(KbProposal).where(KbProposal.tenant_id == tenant_id, KbProposal.doc_id == doc_id, KbProposal.version_no == version_no)).scalar_one_or_none()
    if prop is not None and prop.status != "approved":
        raise Conflict(f"this version comes from a feedback proposal that is {prop.status}: an administrator must approve the proposal first"
                       if prop.status == "proposed" else f"this version comes from a feedback proposal that is {prop.status}: it cannot be published")
    staged = _staged(db, tenant_id, doc_id, version_no)
    if not staged:
        raise Conflict("the processed passages are missing: process the version again")
    if ver.embedding_model != embedder_name or any(c.embedding_model != embedder_name for c in staged):
        raise Conflict("the embedding model changed since this version was processed: process it again before publishing")

    previous = doc.published_version
    # The compare-and-set comes FIRST: it takes the row lock (PostgreSQL) / loses the race (everywhere), so two concurrent publishes cannot both continue.
    store.bump(db, doc, actor, expected=doc.row_version if expected_row_version is None else expected_row_version,
               status="published", published_version=version_no, title=ver.title)

    _drop_live(db, tenant_id, doc_id)
    db.flush()
    db.add(Document(tenant_id=tenant_id, doc_id=doc_id, title=ver.title, audience="public", source_path=f"kb/{doc_id}/v{version_no}"[:300],
                    content_hash=ver.checksum, last_reviewed=date.today(), kb_version=version_no))
    db.flush()
    db.add_all(Chunk(tenant_id=tenant_id, doc_id=doc_id, chunk_index=c.chunk_index, heading=c.heading, text=c.text, token_count=c.token_count,
                     audience="public", embedding_model=c.embedding_model, embedding=list(c.embedding)) for c in staged)
    t = store.now()
    if previous is not None and previous != version_no:
        db.execute(update(KbVersion).where(KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc_id, KbVersion.version_no == previous,
                                           KbVersion.status == "published").values(status="superseded").execution_options(synchronize_session=False))
        _drop_staged(db, tenant_id, doc_id, previous)
    db.execute(update(KbVersion).where(KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc_id, KbVersion.version_no == version_no)
               .values(status="published", published_at=t, published_by=actor[:64]).execution_options(synchronize_session=False))
    _drop_staged(db, tenant_id, doc_id, version_no)              # the passages now live in `chunks`; the staged copy is no longer needed
    if prop is not None:
        db.execute(update(KbProposal).where(KbProposal.tenant_id == tenant_id, KbProposal.call_id == prop.call_id, KbProposal.status == "approved")
                   .values(status="published", published_by=actor[:64], published_at=t).execution_options(synchronize_session=False))
    audit.record(db, tenant_id, actor, "version_published", "version", f"{doc_id}@{version_no}", doc_id=doc_id, version_no=version_no,
                 from_version=previous if previous is not None else 0, to_version=version_no, chunks=len(staged), model=embedder_name,
                 call_id=prop.call_id if prop is not None else "")
    return {"doc_id": doc_id, "version": version_no, "previous_version": previous, "chunks": len(staged), "unchanged": False}


def unpublish_document(db: Session, tenant_id: str, actor: str, doc_id: str, *, expected_row_version: int | None = None) -> dict:
    doc = store.get_doc(db, tenant_id, doc_id)
    if doc.status != "published":
        raise Conflict("this document is not published")
    n = doc.published_version
    store.bump(db, doc, actor, expected=doc.row_version if expected_row_version is None else expected_row_version, status="draft", published_version=None)
    _drop_live(db, tenant_id, doc_id)
    db.execute(update(KbVersion).where(KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc_id, KbVersion.version_no == n, KbVersion.status == "published")
               .values(status="draft", chunk_count=None, embedding_model=None, processed_at=None).execution_options(synchronize_session=False))
    audit.record(db, tenant_id, actor, "version_unpublished", "version", f"{doc_id}@{n}", doc_id=doc_id, version_no=n, from_version=n)
    return {"doc_id": doc_id, "unpublished_version": n}


def archive_document(db: Session, tenant_id: str, actor: str, doc_id: str, *, expected_row_version: int | None = None) -> dict:
    doc = store.get_doc(db, tenant_id, doc_id)
    if doc.status == "archived":
        return {"doc_id": doc_id, "status": "archived", "unchanged": True}
    n = doc.published_version
    store.bump(db, doc, actor, expected=doc.row_version if expected_row_version is None else expected_row_version,
               status="archived", published_version=None, archived_by=actor[:64], archived_at=store.now())
    _drop_live(db, tenant_id, doc_id)                            # the archived knowledge can no longer be retrieved
    if n is not None:
        db.execute(update(KbVersion).where(KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc_id, KbVersion.version_no == n, KbVersion.status == "published")
                   .values(status="archived").execution_options(synchronize_session=False))
    audit.record(db, tenant_id, actor, "document_archived", "document", doc_id, doc_id=doc_id, version_no=n, from_status="published" if n else "draft")
    return {"doc_id": doc_id, "status": "archived", "unchanged": False}


def unarchive_document(db: Session, tenant_id: str, actor: str, doc_id: str, *, expected_row_version: int | None = None) -> dict:
    """Back to `draft`: nothing becomes retrievable until a ready version is published again (the archived version is `draft` and needs processing)."""
    doc = store.get_doc(db, tenant_id, doc_id)
    if doc.status != "archived":
        raise Conflict("this document is not archived")
    store.bump(db, doc, actor, expected=doc.row_version if expected_row_version is None else expected_row_version, status="draft", archived_by=None, archived_at=None)
    db.execute(update(KbVersion).where(KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc_id, KbVersion.status == "archived")
               .values(status="draft", chunk_count=None, embedding_model=None, processed_at=None).execution_options(synchronize_session=False))
    audit.record(db, tenant_id, actor, "document_unarchived", "document", doc_id, doc_id=doc_id, from_status="archived", to_status="draft")
    return {"doc_id": doc_id, "status": "draft"}
