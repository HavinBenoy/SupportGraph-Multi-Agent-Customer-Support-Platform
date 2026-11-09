"""The knowledge store: logical documents, immutable versions, metadata, history, comparison, restore.

All functions take an open session and the tenant (always from the signed token) and let the caller commit (session_scope), so an operation and its audit
event are one transaction. Concurrency: every state change on a document is a compare-and-set on `kb_documents.row_version`, so two writers can never both
believe they were first (this works the same on PostgreSQL and SQLite). Creating a version also derives its number from that same guarded row, so numbers
are consecutive and unique."""

import difflib
from datetime import datetime, timezone

from sqlalchemy import String, and_, cast, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import KbDocPlacement, KbDocument, KbProposal, KbVersion
from app.knowledge import audit
from app.knowledge import validation as v
from app.knowledge.errors import Conflict, NotFound, Invalid

MAX_DIFF_LINES = 2000
LIFECYCLE = {"draft": "draft", "processing": "processing", "ready": "ready_for_review", "failed": "failed", "published": "published",
             "superseded": "superseded", "archived": "archived"}
_RETRIES = 5
_UNSET = object()


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt):
    if dt is None:
        return None
    return (dt.replace(tzinfo=None).isoformat() + "Z") if dt.tzinfo is None else dt.isoformat()


def get_doc(db: Session, tenant_id: str, doc_id: str) -> KbDocument:
    d = db.get(KbDocument, (tenant_id, doc_id))
    if d is None:
        raise NotFound("document not found")          # the same answer for 'does not exist' and 'belongs to another tenant'
    return d


def get_version(db: Session, tenant_id: str, doc_id: str, version_no: int) -> KbVersion:
    row = db.get(KbVersion, (tenant_id, doc_id, version_no))
    if row is None:
        raise NotFound("version not found")
    return row


def bump(db: Session, doc: KbDocument, actor: str, *, expected: int | None = None, **fields) -> None:
    """Compare-and-set the document row. Raises Conflict when somebody changed it since `doc` (or `expected`) was read. The row is expired afterwards."""
    rv = doc.row_version if expected is None else expected
    res = db.execute(update(KbDocument).where(KbDocument.tenant_id == doc.tenant_id, KbDocument.doc_id == doc.doc_id, KbDocument.row_version == rv)
                     .values(row_version=KbDocument.row_version + 1, updated_by=actor[:64], updated_at=now(), **fields).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.expire(doc)
        raise Conflict("the document was changed by someone else: reload it and try again")
    db.expire(doc)


def _check_expected(doc: KbDocument, expected: int | None) -> None:
    if expected is not None and doc.row_version != expected:
        raise Conflict("the document was changed by someone else: reload it and try again")


# ---------------------------------------------------------------------------------------------------------------------------------- views
def version_view(r: KbVersion, *, content: bool = False, proposal: KbProposal | None = None) -> dict:
    out = {"version": r.version_no, "title": r.title, "status": r.status, "lifecycle": LIFECYCLE[r.status], "checksum": r.checksum, "size_bytes": r.size_bytes,
           "change_note": r.change_note, "created_by": r.created_by, "created_at": _iso(r.created_at), "restored_from": r.restored_from,
           "source_filename": r.source_filename, "source_mime": r.source_mime, "chunk_count": r.chunk_count, "embedding_model": r.embedding_model,
           "processed_at": _iso(r.processed_at), "processing_error": r.processing_error, "published_at": _iso(r.published_at), "published_by": r.published_by,
           "proposal": None if proposal is None else {"call_id": proposal.call_id, "status": proposal.status}}
    if content:
        out["content"] = r.content
    return out


def doc_view(db: Session, d: KbDocument) -> dict:
    latest = db.get(KbVersion, (d.tenant_id, d.doc_id, d.latest_version)) if d.latest_version else None
    live = db.get(KbVersion, (d.tenant_id, d.doc_id, d.published_version)) if d.published_version else None
    place = db.get(KbDocPlacement, (d.tenant_id, d.doc_id))
    return {"doc_id": d.doc_id, "folder_id": place.folder_id if place else None, "title": d.title, "category": d.category, "description": d.description, "tags": list(d.tags or []), "status": d.status,
            "origin": d.origin, "latest_version": d.latest_version, "published_version": d.published_version, "row_version": d.row_version,
            "latest_status": None if latest is None else LIFECYCLE[latest.status], "published_at": None if live is None else _iso(live.published_at),
            "published_by": None if live is None else live.published_by, "created_by": d.created_by, "created_at": _iso(d.created_at),
            "updated_by": d.updated_by, "updated_at": _iso(d.updated_at), "archived_by": d.archived_by, "archived_at": _iso(d.archived_at)}


# ----------------------------------------------------------------------------------------------------------------------------------- create
def same_content_elsewhere(db: Session, tenant_id: str, checksum: str, exclude_doc: str) -> list[str]:
    rows = db.execute(select(KbVersion.doc_id).where(KbVersion.tenant_id == tenant_id, KbVersion.checksum == checksum, KbVersion.doc_id != exclude_doc).distinct()).all()
    return sorted(r[0] for r in rows)


def create_document(db: Session, tenant_id: str, actor: str, s: Settings, *, doc_id: str, title: str, category: str, content: str, description=None, tags=None,
                    change_note=None, source_filename=None, source_mime=None, origin: str = "store") -> tuple[KbDocument, KbVersion, list[dict]]:
    doc_id, title, category = v.doc_id(doc_id), v.title(title), v.category(category)
    description, tags = v.description(description), v.tags(tags)
    note = v.change_note(change_note, required=False, default="Initial version")
    fname, mime = v.source(source_filename, source_mime)
    body, size = v.content(content, title, s)
    if db.get(KbDocument, (tenant_id, doc_id)) is not None:
        raise Conflict("a document with this doc_id already exists")
    chk = v.checksum(title, body)
    doc = KbDocument(tenant_id=tenant_id, doc_id=doc_id, title=title, category=category, description=description, tags=tags, status="draft", origin=origin,
                     latest_version=1, published_version=None, row_version=0, created_by=actor[:64])
    ver = KbVersion(tenant_id=tenant_id, doc_id=doc_id, version_no=1, title=title, content=body, checksum=chk, size_bytes=size, source_filename=fname,
                    source_mime=mime, change_note=note, created_by=actor[:64], status="draft")
    db.add(doc)
    db.flush()                                         # a concurrent create of the same doc_id fails here on the primary key (the caller maps it to 409)
    db.add(ver)
    db.flush()
    audit.record(db, tenant_id, actor, "document_created", "document", doc_id, doc_id=doc_id, version_no=1, category=category, bytes=size, checksum=chk[:16], origin=origin)
    audit.record(db, tenant_id, actor, "version_created", "version", f"{doc_id}@1", doc_id=doc_id, version_no=1, bytes=size, checksum=chk[:16])
    warnings = [{"type": "same_content_as", "doc_id": other} for other in same_content_elsewhere(db, tenant_id, chk, doc_id)]
    return doc, ver, warnings


def create_version(db: Session, tenant_id: str, actor: str, s: Settings, doc_id: str, *, content: str, change_note, title: str | None = None,
                   source_filename=None, source_mime=None, expected_row_version: int | None = None, restored_from: int | None = None, author: str | None = None) -> KbVersion:
    """Append version N+1. Never touches an older version. Retries a few times when another writer got in first (unless the caller pinned a row_version)."""
    note = v.change_note(change_note, required=True)
    fname, mime = v.source(source_filename, source_mime)
    for attempt in range(_RETRIES):
        doc = get_doc(db, tenant_id, doc_id)
        _check_expected(doc, expected_row_version)
        if doc.status == "archived":
            raise Conflict("this document is archived: restore it (unarchive) before adding versions")
        new_title = v.title(title) if title else doc.title
        body, size = v.content(content, new_title, s)
        chk = v.checksum(new_title, body)
        latest = db.get(KbVersion, (tenant_id, doc_id, doc.latest_version))
        if latest is not None and latest.checksum == chk:
            raise Conflict(f"identical to the latest version ({latest.version_no}): nothing to add")
        n = doc.latest_version + 1
        try:
            bump(db, doc, actor, expected=doc.row_version, latest_version=n, title=new_title)
        except Conflict:
            if expected_row_version is not None or attempt == _RETRIES - 1:
                raise
            continue
        ver = KbVersion(tenant_id=tenant_id, doc_id=doc_id, version_no=n, title=new_title, content=body, checksum=chk, size_bytes=size, source_filename=fname,
                        source_mime=mime, change_note=note, created_by=(author or actor)[:64], restored_from=restored_from, status="draft")
        db.add(ver)
        db.flush()
        if restored_from is None:
            audit.record(db, tenant_id, actor, "version_created", "version", f"{doc_id}@{n}", doc_id=doc_id, version_no=n, bytes=size, checksum=chk[:16])
        else:
            audit.record(db, tenant_id, actor, "version_restored", "version", f"{doc_id}@{n}", doc_id=doc_id, version_no=n, restored_from=restored_from, bytes=size, checksum=chk[:16])
        return ver
    raise Conflict("the document is being changed by someone else: try again")        # pragma: no cover (loop always returns or raises)


def restore_version(db: Session, tenant_id: str, actor: str, s: Settings, doc_id: str, version_no: int, *, change_note=None,
                    expected_row_version: int | None = None) -> KbVersion:
    """Restoring is a NEW version carrying the old content; every existing version stays as it was."""
    src = get_version(db, tenant_id, doc_id, version_no)
    note = v.change_note(change_note, required=False, default=f"Restored from version {version_no}")
    return create_version(db, tenant_id, actor, s, doc_id, content=src.content, change_note=note, title=src.title, source_filename=src.source_filename,
                          source_mime=src.source_mime, expected_row_version=expected_row_version, restored_from=version_no)


# ------------------------------------------------------------------------------------------------------------------------------- metadata
def update_metadata(db: Session, tenant_id: str, actor: str, doc_id: str, *, category=_UNSET, description=_UNSET, tags=_UNSET,
                    expected_row_version: int | None = None) -> KbDocument:
    """Category, description and tags. The title is part of a version (it is embedded in the passages), so it changes through a new version."""
    doc = get_doc(db, tenant_id, doc_id)
    _check_expected(doc, expected_row_version)
    changes: dict = {}
    if category is not _UNSET:
        changes["category"] = v.category(category)
    if description is not _UNSET:
        changes["description"] = v.description(description)
    if tags is not _UNSET:
        changes["tags"] = v.tags(tags)
    changes = {k: val for k, val in changes.items() if getattr(doc, k) != val}
    if not changes:
        raise Invalid("nothing to change")
    bump(db, doc, actor, expected=doc.row_version, **changes)
    audit.record(db, tenant_id, actor, "metadata_updated", "document", doc_id, doc_id=doc_id, fields=sorted(changes), category=changes.get("category", ""))
    return get_doc(db, tenant_id, doc_id)


# ---------------------------------------------------------------------------------------------------------------------------- read/search
def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_documents(db: Session, tenant_id: str, *, q: str | None = None, category: str | None = None, status: str | None = None, tag: str | None = None,
                   page: int = 1, page_size: int = 25, folder_ids: set[str] | None = None, unfiled: bool = False, published_only: bool = False) -> dict:
    """`published_only` (staff) matches text only inside the PUBLISHED version, so a search can never reveal what an unpublished draft says.
    `folder_ids` limits to documents placed in those folders; `unfiled` to documents at the root."""
    conds = [KbDocument.tenant_id == tenant_id]
    if folder_ids is not None:
        conds.append(exists().where(and_(KbDocPlacement.tenant_id == KbDocument.tenant_id, KbDocPlacement.doc_id == KbDocument.doc_id, KbDocPlacement.folder_id.in_(folder_ids))))
    if unfiled:
        conds.append(~exists().where(and_(KbDocPlacement.tenant_id == KbDocument.tenant_id, KbDocPlacement.doc_id == KbDocument.doc_id)))
    if category:
        conds.append(KbDocument.category == category)
    if status:
        conds.append(KbDocument.status == status)
    if tag:
        conds.append(cast(KbDocument.tags, String).like(f'%"{tag}"%'))
    if q and q.strip():
        pat = _like(q.strip())
        body = exists().where(and_(KbVersion.tenant_id == KbDocument.tenant_id, KbVersion.doc_id == KbDocument.doc_id,
                                   (KbVersion.version_no == KbDocument.published_version) if published_only else or_(KbVersion.version_no == KbDocument.latest_version, KbVersion.version_no == KbDocument.published_version),
                                   KbVersion.content.ilike(pat, escape="\\")))
        if published_only:
            ptitle = exists().where(and_(KbVersion.tenant_id == KbDocument.tenant_id, KbVersion.doc_id == KbDocument.doc_id, KbVersion.version_no == KbDocument.published_version,
                                         KbVersion.title.ilike(pat, escape="\\")))
            conds.append(or_(ptitle, KbDocument.doc_id.ilike(pat, escape="\\"), body))
        else:
            conds.append(or_(KbDocument.title.ilike(pat, escape="\\"), KbDocument.doc_id.ilike(pat, escape="\\"), KbDocument.description.ilike(pat, escape="\\"), body))
    total = db.execute(select(func.count()).select_from(KbDocument).where(*conds)).scalar_one()
    rows = db.execute(select(KbDocument).where(*conds).order_by(func.coalesce(KbDocument.updated_at, KbDocument.created_at).desc(), KbDocument.doc_id)
                      .limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return {"items": [doc_view(db, d) for d in rows], "total": int(total), "page": page, "page_size": page_size}


def list_versions(db: Session, tenant_id: str, doc_id: str, *, page: int = 1, page_size: int = 50) -> dict:
    get_doc(db, tenant_id, doc_id)
    total = db.execute(select(func.count()).select_from(KbVersion).where(KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc_id)).scalar_one()
    rows = db.execute(select(KbVersion).where(KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc_id).order_by(KbVersion.version_no.desc())
                      .limit(page_size).offset((page - 1) * page_size)).scalars().all()
    props = {p.version_no: p for p in db.execute(select(KbProposal).where(KbProposal.tenant_id == tenant_id, KbProposal.doc_id == doc_id)).scalars()}
    return {"items": [version_view(r, proposal=props.get(r.version_no)) for r in rows], "total": int(total), "page": page, "page_size": page_size}


def version_detail(db: Session, tenant_id: str, doc_id: str, version_no: int) -> dict:
    r = get_version(db, tenant_id, doc_id, version_no)
    prop = db.execute(select(KbProposal).where(KbProposal.tenant_id == tenant_id, KbProposal.doc_id == doc_id, KbProposal.version_no == version_no)).scalar_one_or_none()
    return version_view(r, content=True, proposal=prop)


def compare(db: Session, tenant_id: str, doc_id: str, a: int, b: int) -> dict:
    ra, rb = get_version(db, tenant_id, doc_id, a), get_version(db, tenant_id, doc_id, b)
    lines = list(difflib.unified_diff(ra.content.splitlines(), rb.content.splitlines(), fromfile=f"v{a}", tofile=f"v{b}", lineterm="", n=3))
    added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    truncated = len(lines) > MAX_DIFF_LINES
    return {"from": version_view(ra), "to": version_view(rb), "identical": ra.checksum == rb.checksum, "title_changed": ra.title != rb.title,
            "stats": {"added_lines": added, "removed_lines": removed, "size_delta_bytes": rb.size_bytes - ra.size_bytes},
            "diff": lines[:MAX_DIFF_LINES], "truncated": truncated}
