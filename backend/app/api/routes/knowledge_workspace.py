"""Knowledge workspace API (Part 1): folders, placement, change requests, structured diffs.

Administrators (/api/admin/knowledge, tenant from the token):
  GET  /tree                                   folders + a light row per document (everything)
  POST /folders                                create a folder             PUT /folders/{folder_id}   rename (expected_row_version)
  PUT  /documents/{doc_id}/folder              move a document (folder_id null = root); content and versions are never touched
  GET  /documents ?folder_id&unfiled&include_subfolders   (extension of the M6 listing)
  POST /diff-preview                           structured diff of proposed text against the PUBLISHED version (writes nothing)
  GET  /changes ?status                        submitted/approved/rejected/applied/cancelled requests (drafts are private to their author)
  GET  /changes/{id}                           a request with every item's structured diff and proposed text
  POST /changes/{id}/review                    approve | reject | return   (approve publishes NOTHING)
  POST /changes/{id}/apply                     Part 1 legacy: approved -> ordinary DRAFT versions only (the Change Requests screen uses /publish instead)
  Part 2 (AI-assisted changes, completed inside the Change Requests screen):
  POST /changes/{id}/start-review              submitted -> under_review (idempotent)
  PUT  /changes/{id}/items/{doc_id}/edit       the administrator's final manual edit of one document
  POST /changes/{id}/items/{doc_id}/review     confirm | exclude one document
  POST /changes/{id}/publish                   process and publish every approved document as ONE unit (202; poll GET /changes/{id}); retry after failure is safe
  POST /changes/{id}/reopen                    failed -> under_review      POST /changes/{id}/cancel     GET /changes/{id}/audit
Staff (/api/staff/knowledge): read-only knowledge plus their OWN change requests.
  GET  /tree                                   published documents and the folders that lead to them
  GET  /documents/{id}/versions | /versions/{n} | /compare ?from&to      the published history of one document
  POST /diff-preview
  POST /changes {doc_id?, description?}        start (or reopen) my draft      GET /changes   mine      GET /changes/{id}
  PUT  /changes/{id}                           description (expected_row_version)
  PUT  /changes/{id}/items/{doc_id}            save my edit of one document (expected_row_version = the item's token)
  POST /changes/{id}/items/{doc_id}/remove     drop a document from my draft
  POST /changes/{id}/submit | /cancel
  Part 2: POST /ai-changes {description, reference_doc_id?, request_id?}   start an AI-assisted request (or turn my draft into one)
          POST /changes/{id}/analyse {answers?}  202: run (or re-run) the impact analysis in the background; poll GET /changes/{id}
          POST /changes/{id}/items/{doc_id}/decision {decision: accepted|manual|discarded, replacements?, content?, reason?}     GET /changes/{id}/audit
Nothing here writes a published version, `documents` or `chunks`."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.api.deps import require_admin, require_staff
from app.api.routes.knowledge import DOC, _guard, embedder_of
from app.auth.tokens import Principal
from app.db.models import KB_REQUEST_STATUSES, KbDocument, KbVersion
from app.knowledge import changes, diffing, release, store, workspace
from app.knowledge.impact import analyse as impact_analyse
from app.llm.gateway import get_impact_model
from app.knowledge.errors import NotFound
from sqlalchemy import select

admin_router = APIRouter(prefix="/api/admin/knowledge", tags=["knowledge-workspace"])
staff_router = APIRouter(prefix="/api/staff/knowledge", tags=["knowledge-workspace"])
FOLDER = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^f_[0-9a-f]+$")]
REQ = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^cr_[0-9a-f]+$")]
VERSION_NO = Annotated[int, Path(ge=1, le=100000)]


class FolderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=200)
    parent_id: str | None = Field(default=None, max_length=32, pattern=r"^f_[0-9a-f]+$")


class FolderRenameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=200)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: str | None = Field(max_length=32, pattern=r"^f_[0-9a-f]+$")          # required, but may be null (the root)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    content: str = Field(max_length=2_100_000)
    title: str | None = Field(default=None, max_length=300)


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str | None = Field(default=None, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str | None = Field(default=None, max_length=1000)


class DescriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(max_length=1000)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=2_100_000)
    title: str | None = Field(default=None, max_length=300)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, max_length=1000)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern=r"^(approve|reject|return)$")
    note: str | None = Field(default=None, max_length=500)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class AiStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=1000)
    reference_doc_id: str | None = Field(default=None, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    request_id: str | None = Field(default=None, max_length=32, pattern=r"^cr_[0-9a-f]+$")


class AnalyseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str] | None = Field(default=None, max_length=12)
    description: str | None = Field(default=None, max_length=1000)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class Replacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: StrictInt = Field(ge=0, le=100)
    replacement: str = Field(max_length=4000)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern=r"^(accepted|manual|discarded)$")
    replacements: list[Replacement] | None = Field(default=None, max_length=20)
    content: str | None = Field(default=None, max_length=2_100_000)
    reason: str | None = Field(default=None, max_length=500)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class AdminEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replacements: list[Replacement] | None = Field(default=None, max_length=20)
    content: str | None = Field(default=None, max_length=2_100_000)
    note: str | None = Field(default=None, max_length=500)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


class ItemReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern=r"^(confirmed|excluded)$")
    note: str | None = Field(default=None, max_length=500)
    expected_row_version: StrictInt | None = Field(default=None, ge=0)


def _status(v: str | None) -> str | None:
    if v is not None and v not in KB_REQUEST_STATUSES:
        raise HTTPException(422, "unknown status")
    return v


# ---------------------------------------------------------------------------------------------------------------------------- admin: folders
@admin_router.get("/tree")
async def admin_tree(p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: workspace.tree(db, p.tenant_id, published_only=False))


@admin_router.post("/folders", status_code=201)
async def create_folder(body: FolderIn, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: workspace.create_folder(db, p.tenant_id, p.subject, body.name, body.parent_id))


@admin_router.put("/folders/{folder_id}")
async def rename_folder(folder_id: FOLDER, body: FolderRenameIn, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: workspace.rename_folder(db, p.tenant_id, p.subject, folder_id, body.name, expected_row_version=body.expected_row_version))


@admin_router.put("/documents/{doc_id}/folder")
async def move_document(doc_id: DOC, body: MoveIn, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: workspace.move_document(db, p.tenant_id, p.subject, doc_id, body.folder_id, expected_row_version=body.expected_row_version))


# ----------------------------------------------------------------------------------------------------------------------------- diff preview
@admin_router.post("/diff-preview")
async def admin_preview(body: PreviewIn, request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    return await asyncio.to_thread(_guard, lambda db: changes.preview(db, p.tenant_id, s, body.doc_id, content=body.content, title=body.title))


@staff_router.post("/diff-preview")
async def staff_preview(body: PreviewIn, request: Request, p: Principal = Depends(require_staff)) -> dict:
    s = request.app.state.settings
    return await asyncio.to_thread(_guard, lambda db: changes.preview(db, p.tenant_id, s, body.doc_id, content=body.content, title=body.title))


# ------------------------------------------------------------------------------------------------------------------------ admin: requests
@admin_router.get("/changes")
async def admin_list_changes(status: Annotated[str | None, Query(max_length=24)] = None, page: Annotated[int, Query(ge=1, le=10000)] = 1,
                             page_size: Annotated[int, Query(ge=1, le=100)] = 25, p: Principal = Depends(require_admin)) -> dict:
    _status(status)
    if status == "draft":
        raise HTTPException(422, "drafts are private to their author")
    return await asyncio.to_thread(_guard, lambda db: changes.list_requests(db, p.tenant_id, status=status, include_drafts=False, page=page, page_size=page_size))


@admin_router.get("/changes/{request_id}")
async def admin_get_change(request_id: REQ, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.admin_get(db, p.tenant_id, request_id))


@admin_router.post("/changes/{request_id}/review")
async def admin_review(request_id: REQ, body: ReviewIn, request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    return await asyncio.to_thread(_guard, lambda db: changes.review(db, p.tenant_id, p.subject, s, request_id, body.decision, body.note, expected_row_version=body.expected_row_version))


@admin_router.post("/changes/{request_id}/apply")
async def admin_apply(request_id: REQ, request: Request, body: ApplyIn | None = None, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    rv = body.expected_row_version if body else None
    return await asyncio.to_thread(_guard, lambda db: changes.apply(db, p.tenant_id, p.subject, s, request_id, expected_row_version=rv))


@admin_router.post("/changes/{request_id}/start-review")
async def admin_start_review(request_id: REQ, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.start_review(db, p.tenant_id, p.subject, request_id))


@admin_router.put("/changes/{request_id}/items/{doc_id}/edit")
async def admin_edit_item(request_id: REQ, doc_id: DOC, body: AdminEditIn, request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    reps = [r.model_dump() for r in body.replacements] if body.replacements else None
    return await asyncio.to_thread(_guard, lambda db: changes.admin_edit_item(db, p.tenant_id, p.subject, s, request_id, doc_id, replacements=reps, content=body.content,
                                                                              note=body.note, expected_row_version=body.expected_row_version))


@admin_router.post("/changes/{request_id}/items/{doc_id}/review")
async def admin_item_review(request_id: REQ, doc_id: DOC, body: ItemReviewIn, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.admin_item_decision(db, p.tenant_id, p.subject, request_id, doc_id, body.decision, body.note,
                                                                                  expected_row_version=body.expected_row_version))


@admin_router.post("/changes/{request_id}/publish", status_code=202)
async def admin_publish(request_id: REQ, request: Request, body: ApplyIn | None = None, p: Principal = Depends(require_admin)) -> dict:
    """Process and publish every approved document of the request as one unit. Returns at once; the work continues in the background and the screen polls
    GET /changes/{id}. A repeated call is safe: a running release answers 409, a finished one answers with the published request."""
    s = request.app.state.settings
    emb = embedder_of(request)
    rv = body.expected_row_version if body else None
    out = await asyncio.to_thread(_guard, lambda db: release.begin(db, p.tenant_id, p.subject, s, request_id, expected_row_version=rv))
    if out["outcome"] == "started":
        await asyncio.to_thread(release.launch, p.tenant_id, p.subject, request_id, out["attempt"], s, emb)
    view = await asyncio.to_thread(_guard, lambda db: changes.admin_get(db, p.tenant_id, request_id))
    return {"outcome": out["outcome"], "attempt": out["attempt"], "request": view}


@admin_router.post("/changes/{request_id}/reopen")
async def admin_reopen(request_id: REQ, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.reopen(db, p.tenant_id, p.subject, request_id))


@admin_router.post("/changes/{request_id}/cancel")
async def admin_cancel(request_id: REQ, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.cancel(db, p.tenant_id, p.subject, request_id, admin=True))


@admin_router.get("/changes/{request_id}/audit")
async def admin_request_audit(request_id: REQ, p: Principal = Depends(require_admin)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.request_audit(db, p.tenant_id, request_id, admin=True))


# ------------------------------------------------------------------------------------------------------------------------------- staff: read
@staff_router.get("/tree")
async def staff_tree(p: Principal = Depends(require_staff)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: workspace.tree(db, p.tenant_id, published_only=True))


def _history(db, tenant_id: str, doc_id: str) -> tuple[KbDocument, list[KbVersion]]:
    d = store.get_doc(db, tenant_id, doc_id)
    if d.status != "published":
        raise NotFound("document not found")                         # invisible to staff unless it is live
    rows = db.execute(select(KbVersion).where(KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc_id, KbVersion.status.in_(("published", "superseded")))
                      .order_by(KbVersion.version_no.desc())).scalars().all()
    return d, list(rows)


def _hist_row(r: KbVersion, live: int | None) -> dict:
    return {"version": r.version_no, "title": r.title, "change_note": r.change_note, "authored_by": r.created_by, "published_at": store._iso(r.published_at),
            "published_by": r.published_by, "is_current": r.version_no == live, "size_bytes": r.size_bytes}


@staff_router.get("/documents/{doc_id}/versions")
async def staff_versions(doc_id: DOC, p: Principal = Depends(require_staff)) -> dict:
    def work(db):
        d, rows = _history(db, p.tenant_id, doc_id)
        return {"doc_id": doc_id, "items": [_hist_row(r, d.published_version) for r in rows], "total": len(rows)}
    return await asyncio.to_thread(_guard, work)


@staff_router.get("/documents/{doc_id}/versions/{n}")
async def staff_version(doc_id: DOC, n: VERSION_NO, p: Principal = Depends(require_staff)) -> dict:
    def work(db):
        d, rows = _history(db, p.tenant_id, doc_id)
        r = next((x for x in rows if x.version_no == n), None)
        if r is None:
            raise NotFound("version not found")                       # drafts and proposals are not part of the published history
        return _hist_row(r, d.published_version) | {"content": r.content}
    return await asyncio.to_thread(_guard, work)


@staff_router.get("/documents/{doc_id}/compare")
async def staff_compare(doc_id: DOC, from_: Annotated[int, Query(alias="from", ge=1, le=100000)], to: Annotated[int, Query(ge=1, le=100000)],
                        p: Principal = Depends(require_staff)) -> dict:
    def work(db):
        _, rows = _history(db, p.tenant_id, doc_id)
        by = {x.version_no: x for x in rows}
        if from_ not in by or to not in by:
            raise NotFound("version not found")
        a, b = by[from_], by[to]
        diff, st = diffing.build(a.title, b.title, a.content, b.content)
        return {"doc_id": doc_id, "from": from_, "to": to, "diff": diff, "stats": st}
    return await asyncio.to_thread(_guard, work)


# ------------------------------------------------------------------------------------------------------------------------ staff: own requests
@staff_router.post("/changes", status_code=201)
async def staff_start(body: StartIn, request: Request, p: Principal = Depends(require_staff)) -> dict:
    s = request.app.state.settings

    def work(db):
        view, existing = changes.start(db, p.tenant_id, p.subject, s, doc_id=body.doc_id, description=body.description)
        return {"request": view, "existing": existing}
    return await asyncio.to_thread(_guard, work)


@staff_router.get("/changes")
async def staff_list_changes(status: Annotated[str | None, Query(max_length=24)] = None, page: Annotated[int, Query(ge=1, le=10000)] = 1,
                             page_size: Annotated[int, Query(ge=1, le=100)] = 25, p: Principal = Depends(require_staff)) -> dict:
    _status(status)
    return await asyncio.to_thread(_guard, lambda db: changes.list_requests(db, p.tenant_id, owner=p.subject, status=status, page=page, page_size=page_size))


@staff_router.get("/changes/{request_id}")
async def staff_get_change(request_id: REQ, p: Principal = Depends(require_staff)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.get_for_owner(db, p.tenant_id, p.subject, request_id))


@staff_router.put("/changes/{request_id}")
async def staff_update_change(request_id: REQ, body: DescriptionIn, p: Principal = Depends(require_staff)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.update_description(db, p.tenant_id, p.subject, request_id, body.description, expected_row_version=body.expected_row_version))


@staff_router.put("/changes/{request_id}/items/{doc_id}")
async def staff_save_item(request_id: REQ, doc_id: DOC, body: ItemIn, request: Request, p: Principal = Depends(require_staff)) -> dict:
    s = request.app.state.settings
    return await asyncio.to_thread(_guard, lambda db: changes.save_item(db, p.tenant_id, p.subject, s, request_id, doc_id, content=body.content, title=body.title,
                                                                        expected_row_version=body.expected_row_version))


@staff_router.post("/changes/{request_id}/items/{doc_id}/remove")
async def staff_remove_item(request_id: REQ, doc_id: DOC, p: Principal = Depends(require_staff)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.remove_item(db, p.tenant_id, p.subject, request_id, doc_id))


@staff_router.post("/changes/{request_id}/submit")
async def staff_submit(request_id: REQ, body: SubmitIn | None = None, p: Principal = Depends(require_staff)) -> dict:
    d, rv = (body.description if body else None), (body.expected_row_version if body else None)
    return await asyncio.to_thread(_guard, lambda db: changes.submit(db, p.tenant_id, p.subject, request_id, description=d, expected_row_version=rv))


@staff_router.post("/changes/{request_id}/cancel")
async def staff_cancel(request_id: REQ, p: Principal = Depends(require_staff)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.cancel(db, p.tenant_id, p.subject, request_id))


@staff_router.get("/changes/{request_id}/audit")
async def staff_request_audit(request_id: REQ, p: Principal = Depends(require_staff)) -> dict:
    return await asyncio.to_thread(_guard, lambda db: changes.request_audit(db, p.tenant_id, request_id, admin=False, owner=p.subject))


@staff_router.post("/ai-changes", status_code=201)
async def staff_start_ai(body: AiStartIn, request: Request, p: Principal = Depends(require_staff)) -> dict:
    s = request.app.state.settings
    return await asyncio.to_thread(_guard, lambda db: changes.start_ai(db, p.tenant_id, p.subject, s, description=body.description, reference_doc_id=body.reference_doc_id,
                                                                       request_id=body.request_id))


@staff_router.post("/changes/{request_id}/analyse", status_code=202)
async def staff_analyse(request_id: REQ, request: Request, body: AnalyseIn | None = None, p: Principal = Depends(require_staff)) -> dict:
    """Start (or re-run) the impact analysis. It runs in the background under a fenced attempt number; poll GET /changes/{id} for progress, questions and results."""
    s = request.app.state.settings
    b = body or AnalyseIn()
    view, attempt = await asyncio.to_thread(_guard, lambda db: changes.begin_analysis(db, p.tenant_id, p.subject, s, request_id, answers=b.answers, description=b.description,
                                                                                      expected_row_version=b.expected_row_version))
    model = get_impact_model(s)
    await asyncio.to_thread(impact_analyse.launch, p.tenant_id, request_id, attempt, s, model, embedder_of(request))
    return {"attempt": attempt, "request": view}


@staff_router.post("/changes/{request_id}/items/{doc_id}/decision")
async def staff_decide(request_id: REQ, doc_id: DOC, body: DecisionIn, request: Request, p: Principal = Depends(require_staff)) -> dict:
    s = request.app.state.settings
    reps = [r.model_dump() for r in body.replacements] if body.replacements else None
    return await asyncio.to_thread(_guard, lambda db: changes.decide_item(db, p.tenant_id, p.subject, s, request_id, doc_id, body.decision, replacements=reps, content=body.content,
                                                                          reason=body.reason, expected_row_version=body.expected_row_version))
