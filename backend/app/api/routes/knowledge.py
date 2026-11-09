"""Knowledge store API (M6).

Administrators (tenant from the signed token; another tenant's documents are never reachable and look like 404):
  GET  /api/admin/knowledge/meta                                      categories, statuses, limits, active embedding model, disk articles not yet managed
  GET  /api/admin/knowledge/documents ?q&category&status&tag&page&page_size
  POST /api/admin/knowledge/documents                                 create a document (version 1, a draft)
  GET  /api/admin/knowledge/documents/{doc_id}
  PUT  /api/admin/knowledge/documents/{doc_id}                        metadata: category, description, tags (title changes through a new version)
  GET  /api/admin/knowledge/documents/{doc_id}/versions               history (no content)
  POST /api/admin/knowledge/documents/{doc_id}/versions               add a version
  GET  /api/admin/knowledge/documents/{doc_id}/versions/{n}           one version with its content
  GET  /api/admin/knowledge/documents/{doc_id}/compare ?from=&to=
  POST /api/admin/knowledge/documents/{doc_id}/versions/{n}/restore   a NEW version carrying version n's content
  POST /api/admin/knowledge/documents/{doc_id}/versions/{n}/process   chunk + embed into staged passages (idempotent)
  POST /api/admin/knowledge/documents/{doc_id}/versions/{n}/publish   atomic switch of the live version
  POST /api/admin/knowledge/documents/{doc_id}/unpublish | archive | unarchive
  GET  /api/admin/knowledge/audit ?doc_id&action&actor&limit&before_id
  GET  /api/admin/knowledge/feedback ?only_unprocessed                approved intervention feedback (read-only) and the proposal made from it
  POST /api/admin/knowledge/proposals                                 start a DRAFT from approved feedback
  GET  /api/admin/knowledge/proposals ?status
  POST /api/admin/knowledge/proposals/{call_id}/review                approve | reject the proposal
  POST /api/admin/knowledge/import-disk                               put the articles loaded from disk under the store (versions, audit)
Staff (read-only): published documents only, never drafts, history or audit.
  GET  /api/staff/knowledge/documents ?q&category&page&page_size      GET /api/staff/knowledge/documents/{doc_id}
Students and anonymous callers have no access. Nothing here is reachable from the chat or the LLM."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.api.deps import require_admin, require_staff
from app.auth.tokens import Principal
from app.db.models import KB_CATEGORIES, KB_DOC_STATUSES, KB_PROPOSAL_STATUSES, KbDocument, KbVersion
from app.db.session import session_scope
from app.knowledge import audit, importer, pipeline, proposals, store
from app.knowledge.errors import KbError
from app.llm.gateway import get_embedding_client

admin_router = APIRouter(prefix="/api/admin/knowledge", tags=["knowledge"])
staff_router = APIRouter(prefix="/api/staff/knowledge", tags=["knowledge"])
DOC = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")]


def embedder_of(request: Request):
    """The embedding client the RAG pipeline uses (same provider, model and width). Created once per process; tests may preset app.state.embedder."""
    e = getattr(request.app.state, "embedder", None)
    if e is None:
        e = request.app.state.embedder = get_embedding_client(request.app.state.settings)
    return e


def _guard(fn):
    """One transaction; knowledge refusals become HTTP errors. Other exceptions roll back and surface as 500 (never with document text)."""
    try:
        with session_scope() as db:
            return fn(db)
    except KbError as e:
        raise HTTPException(e.status, e.detail) from None


class DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(max_length=128)
    title: str = Field(max_length=300)
    category: str = Field(max_length=24)
    content: str = Field(max_length=2_100_000)            # the exact byte limit is SG_KB_MAX_BYTES, enforced after normalisation
    description: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = Field(default=None, max_length=10)
    change_note: str | None = Field(default=None, max_length=500)
    source_filename: str | None = Field(default=None, max_length=200)
    source_mime: str | None = Field(default=None, max_length=40)


class MetadataIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str | None = Field(default=None, max_length=24)
    description: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = Field(default=None, max_length=10)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class VersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=2_100_000)
    change_note: str = Field(max_length=500)
    title: str | None = Field(default=None, max_length=300)
    source_filename: str | None = Field(default=None, max_length=200)
    source_mime: str | None = Field(default=None, max_length=40)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_note: str | None = Field(default=None, max_length=500)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class ExpectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class ProcessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: StrictBool = False


class NewDocIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(max_length=128)
    title: str = Field(max_length=300)
    category: str = Field(max_length=24)
    description: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = Field(default=None, max_length=10)


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str = Field(min_length=1, max_length=40)
    content: str = Field(max_length=2_100_000)
    doc_id: str | None = Field(default=None, max_length=128)
    new_document: NewDocIn | None = None
    change_note: str | None = Field(default=None, max_length=500)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(max_length=10)
    note: str | None = Field(default=None, max_length=500)


# --------------------------------------------------------------------------------------------------------------------------------- admin
@admin_router.get("/meta")
async def meta(request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    unmanaged = await asyncio.to_thread(importer.unmanaged_disk_docs, p.tenant_id)
    return {"categories": list(KB_CATEGORIES), "document_statuses": list(KB_DOC_STATUSES), "proposal_statuses": list(KB_PROPOSAL_STATUSES),
            "limits": {"max_bytes": s.kb_max_bytes, "max_chunks": s.kb_max_chunks, "allowed_types": ["text/markdown", "text/plain"]},
            "embedding_model": embedder_of(request).name, "require_separate_reviewer": s.kb_require_separate_reviewer, "unmanaged_disk_documents": unmanaged}


@admin_router.get("/documents")
async def list_documents(q: Annotated[str | None, Query(max_length=100)] = None, category: Annotated[str | None, Query(max_length=24)] = None,
                         status: Annotated[str | None, Query(max_length=12)] = None, tag: Annotated[str | None, Query(max_length=30, pattern=r"^[a-z0-9-]+$")] = None,
                         page: Annotated[int, Query(ge=1, le=10000)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 25,
                         p: Principal = Depends(require_admin)) -> dict:
    if category is not None and category not in KB_CATEGORIES:
        raise HTTPException(422, "unknown category")
    if status is not None and status not in KB_DOC_STATUSES:
        raise HTTPException(422, "unknown status")
    return await asyncio.to_thread(_guard, lambda db: store.list_documents(db, p.tenant_id, q=q, category=category, status=status, tag=tag, page=page, page_size=page_size))


@admin_router.post("/documents", status_code=201)
async def create_document(body: DocumentIn, request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings

    def work(db):
        doc, ver, warnings = store.create_document(db, p.tenant_id, p.subject, s, doc_id=body.doc_id, title=body.title, category=body.category, content=body.content,
                                                   description=body.description, tags=body.tags, change_note=body.change_note,
                                                   source_filename=body.source_filename, source_mime=body.source_mime)
        return {"document": store.doc_view(db, doc), "version": store.version_view(ver), "warnings": warnings}
    return await asyncio.to_thread(_create_guard, work)


def _create_guard(fn):
    from sqlalchemy.exc import IntegrityError
    try:
        return _guard(fn)
    except IntegrityError:
        raise HTTPException(409, "a document with this doc_id already exists") from None


@admin_router.get("/documents/{doc_id}")
async def get_document(doc_id: DOC, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: store.doc_view(db, store.get_doc(db, p.tenant_id, doc_id)))


@admin_router.put("/documents/{doc_id}")
async def update_metadata(doc_id: DOC, body: MetadataIn, p: Principal = Depends(require_admin)) -> dict:
    given = body.model_dump(exclude_unset=True)
    kw = {k: given[k] for k in ("category", "description", "tags") if k in given}
    return await asyncio.to_thread(_guard, lambda db: store.doc_view(db, store.update_metadata(db, p.tenant_id, p.subject, doc_id, expected_row_version=body.expected_row_version, **kw)))


@admin_router.get("/documents/{doc_id}/versions")
async def versions(doc_id: DOC, page: Annotated[int, Query(ge=1, le=10000)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 50,
                   p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: store.list_versions(db, p.tenant_id, doc_id, page=page, page_size=page_size))


@admin_router.post("/documents/{doc_id}/versions", status_code=201)
async def add_version(doc_id: DOC, body: VersionIn, request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings

    def work(db):
        ver = store.create_version(db, p.tenant_id, p.subject, s, doc_id, content=body.content, change_note=body.change_note, title=body.title,
                                   source_filename=body.source_filename, source_mime=body.source_mime, expected_row_version=body.expected_row_version)
        return {"version": store.version_view(ver), "document": store.doc_view(db, store.get_doc(db, p.tenant_id, doc_id))}
    return await asyncio.to_thread(_guard, work)


@admin_router.get("/documents/{doc_id}/versions/{n}")
async def get_version(doc_id: DOC, n: int, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: store.version_detail(db, p.tenant_id, doc_id, _n(n)))


def _n(n: int) -> int:
    if n < 1 or n > 1_000_000:
        raise HTTPException(422, "version must be a positive number")
    return n


@admin_router.get("/documents/{doc_id}/compare")
async def compare(doc_id: DOC, from_: Annotated[int, Query(alias="from", ge=1)], to: Annotated[int, Query(ge=1)], p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: store.compare(db, p.tenant_id, doc_id, from_, to))


@admin_router.post("/documents/{doc_id}/versions/{n}/restore", status_code=201)
async def restore(doc_id: DOC, n: int, request: Request, body: RestoreIn | None = None, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    body = body or RestoreIn()

    def work(db):
        ver = store.restore_version(db, p.tenant_id, p.subject, s, doc_id, _n(n), change_note=body.change_note, expected_row_version=body.expected_row_version)
        return {"version": store.version_view(ver), "document": store.doc_view(db, store.get_doc(db, p.tenant_id, doc_id))}
    return await asyncio.to_thread(_guard, work)


@admin_router.post("/documents/{doc_id}/versions/{n}/process")
async def process(doc_id: DOC, n: int, request: Request, body: ProcessIn | None = None, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    emb = embedder_of(request)
    force = bool(body and body.force)

    def work():
        try:
            return pipeline.process_version(p.tenant_id, p.subject, doc_id, _n(n), emb, s, force=force)
        except KbError as e:
            raise HTTPException(e.status, e.detail) from None
    return await asyncio.to_thread(work)


@admin_router.post("/documents/{doc_id}/versions/{n}/publish")
async def publish(doc_id: DOC, n: int, request: Request, body: ExpectIn | None = None, p: Principal = Depends(require_admin)) -> dict:
    emb = embedder_of(request)
    exp = body.expected_row_version if body else None
    return await asyncio.to_thread(_guard, lambda db: pipeline.publish_version(db, p.tenant_id, p.subject, doc_id, _n(n), emb.name, expected_row_version=exp))


@admin_router.post("/documents/{doc_id}/unpublish")
async def unpublish(doc_id: DOC, body: ExpectIn | None = None, p: Principal = Depends(require_admin)) -> dict:
    exp = body.expected_row_version if body else None
    return await asyncio.to_thread(_guard, lambda db: pipeline.unpublish_document(db, p.tenant_id, p.subject, doc_id, expected_row_version=exp))


@admin_router.post("/documents/{doc_id}/archive")
async def archive(doc_id: DOC, body: ExpectIn | None = None, p: Principal = Depends(require_admin)) -> dict:
    exp = body.expected_row_version if body else None
    return await asyncio.to_thread(_guard, lambda db: pipeline.archive_document(db, p.tenant_id, p.subject, doc_id, expected_row_version=exp))


@admin_router.post("/documents/{doc_id}/unarchive")
async def unarchive(doc_id: DOC, body: ExpectIn | None = None, p: Principal = Depends(require_admin)) -> dict:
    exp = body.expected_row_version if body else None
    return await asyncio.to_thread(_guard, lambda db: pipeline.unarchive_document(db, p.tenant_id, p.subject, doc_id, expected_row_version=exp))


@admin_router.get("/audit")
async def audit_trail(doc_id: Annotated[str | None, Query(max_length=128)] = None, action: Annotated[str | None, Query(max_length=40)] = None,
                      actor: Annotated[str | None, Query(max_length=64)] = None, limit: Annotated[int, Query(ge=1, le=200)] = 50,
                      before_id: Annotated[int | None, Query(ge=1)] = None, p: Principal = Depends(require_admin)) -> dict:
    if action is not None and action not in audit.ACTIONS:
        raise HTTPException(422, "unknown action")
    return await asyncio.to_thread(_guard, lambda db: audit.list_events(db, p.tenant_id, doc_id=doc_id, action=action, actor=actor, limit=limit, before_id=before_id))


@admin_router.get("/feedback")
async def approved_feedback(only_unprocessed: bool = False, limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0, le=100000)] = 0,
                            p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: proposals.list_approved_feedback(db, p.tenant_id, only_unprocessed=only_unprocessed, limit=limit, offset=offset))


@admin_router.post("/proposals", status_code=201)
async def create_proposal(body: ProposalIn, request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    nd = body.new_document.model_dump() if body.new_document else None
    return await asyncio.to_thread(_create_guard, lambda db: proposals.create_proposal(db, p.tenant_id, p.subject, s, body.call_id, content=body.content,
                                                                                       change_note=body.change_note, doc_id=body.doc_id, new_document=nd))


@admin_router.get("/proposals")
async def list_proposals(status: Annotated[str | None, Query(max_length=12)] = None, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                         offset: Annotated[int, Query(ge=0, le=100000)] = 0, p: Principal = Depends(require_admin)) -> dict:
    if status is not None and status not in KB_PROPOSAL_STATUSES:
        raise HTTPException(422, "unknown status")
    return await asyncio.to_thread(_guard, lambda db: proposals.list_proposals(db, p.tenant_id, status=status, limit=limit, offset=offset))


@admin_router.post("/proposals/{call_id}/review")
async def review_proposal(call_id: Annotated[str, Path(min_length=1, max_length=40)], body: ReviewIn, request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    return await asyncio.to_thread(_guard, lambda db: proposals.review_proposal(db, p.tenant_id, p.subject, s, call_id, body.decision, body.note))


@admin_router.post("/import-disk")
async def import_disk(request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    emb = embedder_of(request)

    def work():
        try:
            return importer.import_from_disk(p.tenant_id, p.subject, emb, s)
        except KbError as e:
            raise HTTPException(e.status, e.detail) from None
        except Exception as e:  # noqa: BLE001 - IngestError and friends: the message names what to fix and holds no document text
            if type(e).__name__ in ("IngestError", "InternalDocumentError"):
                raise HTTPException(409, str(e)[:300]) from None
            raise
    return await asyncio.to_thread(work)


# --------------------------------------------------------------------------------------------------------------------------------- staff
def _published_view(db, d: KbDocument, *, content: bool) -> dict:
    ver = db.get(KbVersion, (d.tenant_id, d.doc_id, d.published_version))
    out = {"doc_id": d.doc_id, "title": ver.title, "category": d.category, "tags": list(d.tags or []), "version": ver.version_no, "published_at": store._iso(ver.published_at)}
    if content:
        out["content"] = ver.content
    return out


@staff_router.get("/documents")
async def staff_list(q: Annotated[str | None, Query(max_length=100)] = None, category: Annotated[str | None, Query(max_length=24)] = None,
                     page: Annotated[int, Query(ge=1, le=10000)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 25, p: Principal = Depends(require_staff)) -> dict:
    if category is not None and category not in KB_CATEGORIES:
        raise HTTPException(422, "unknown category")

    def work(db):
        res = store.list_documents(db, p.tenant_id, q=q, category=category, status="published", page=page, page_size=page_size)
        items = [_published_view(db, store.get_doc(db, p.tenant_id, i["doc_id"]), content=False) for i in res["items"]]
        return {"items": items, "total": res["total"], "page": page, "page_size": page_size}
    return await asyncio.to_thread(_guard, work)


@staff_router.get("/documents/{doc_id}")
async def staff_get(doc_id: DOC, p: Principal = Depends(require_staff)) -> dict:
    def work(db):
        d = store.get_doc(db, p.tenant_id, doc_id)
        if d.status != "published":
            raise store.NotFound("document not found")          # a draft or archived document is invisible to staff
        return _published_view(db, d, content=True)
    return await asyncio.to_thread(_guard, work)
