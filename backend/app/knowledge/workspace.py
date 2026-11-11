"""Folders and document placement (Part 1): the user-facing organisation of the knowledge base.

Folders never hold content and never affect retrieval; they are labels in the database, which remains the only source of truth. A document with no placement
row is at the root. Paths are computed from the parent chain, so renaming a folder is one row update and can never leave a descendant with a stale path.
Everything runs in the caller's transaction (session_scope) together with its audit event, and takes the tenant from the signed token."""

import re
import secrets

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import KbDocPlacement, KbDocument, KbFolder, KbVersion
from app.knowledge import audit, store
from app.knowledge.errors import Conflict, Invalid, NotFound

MAX_DEPTH = 6
MAX_FOLDERS = 500
_NAME_BAD = re.compile(r"[\x00-\x1f\x7f/\\]")
_RESERVED = {".", ".."}


def folder_name(v) -> str:
    n = " ".join(str(v or "").split())
    if not n or len(n) > 80 or _NAME_BAD.search(n) or n in _RESERVED:
        raise Invalid("folder name is required (at most 80 characters, no slashes or control characters)")
    return n


def get_folder(db: Session, tenant_id: str, folder_id: str) -> KbFolder:
    f = db.get(KbFolder, (tenant_id, folder_id))
    if f is None:
        raise NotFound("folder not found")            # also what another tenant's folder looks like
    return f


def folders_by_id(db: Session, tenant_id: str) -> dict[str, KbFolder]:
    return {f.folder_id: f for f in db.execute(select(KbFolder).where(KbFolder.tenant_id == tenant_id)).scalars()}


def crumbs(fmap: dict[str, KbFolder], folder_id: str | None) -> list[dict]:
    """[{"id", "name"}, ...] from the top-level folder down to `folder_id` (empty for the root)."""
    out: list[dict] = []
    seen: set[str] = set()
    cur = folder_id
    while cur is not None and cur in fmap and cur not in seen:
        seen.add(cur)
        out.append({"id": cur, "name": fmap[cur].name})
        cur = fmap[cur].parent_id
    return out[::-1]


def path_of(fmap: dict[str, KbFolder], folder_id: str | None) -> str:
    return "/".join(c["name"] for c in crumbs(fmap, folder_id))


def _depth(fmap, folder_id: str | None) -> int:
    return len(crumbs(fmap, folder_id))


def _folder_view(f: KbFolder, fmap: dict[str, KbFolder]) -> dict:
    return {"folder_id": f.folder_id, "parent_id": f.parent_id, "name": f.name, "path": path_of(fmap, f.folder_id), "row_version": f.row_version,
            "created_by": f.created_by, "created_at": store._iso(f.created_at), "updated_by": f.updated_by, "updated_at": store._iso(f.updated_at)}


def create_folder(db: Session, tenant_id: str, actor: str, name, parent_id: str | None = None) -> dict:
    n = folder_name(name)
    fmap = folders_by_id(db, tenant_id)
    if parent_id is not None:
        if parent_id not in fmap:
            raise NotFound("folder not found")
        if _depth(fmap, parent_id) >= MAX_DEPTH:
            raise Invalid(f"folders can be nested at most {MAX_DEPTH} levels deep")
    if len(fmap) >= MAX_FOLDERS:
        raise Invalid(f"a tenant can have at most {MAX_FOLDERS} folders")
    f = KbFolder(tenant_id=tenant_id, folder_id="f_" + secrets.token_hex(8), parent_id=parent_id, parent_key=parent_id or "", name=n, name_key=n.lower(),
                 row_version=0, created_by=actor[:64])
    try:
        with db.begin_nested():
            db.add(f)
            db.flush()                                   # the unique constraint decides a race between two creators of the same name
    except IntegrityError:
        raise Conflict("a folder with this name already exists here") from None
    fmap[f.folder_id] = f
    audit.record(db, tenant_id, actor, "folder_created", "folder", f.folder_id, folder_id=f.folder_id, parent_id=parent_id or "", name=n)
    return _folder_view(f, fmap)


def rename_folder(db: Session, tenant_id: str, actor: str, folder_id: str, name, *, expected_row_version: int | None = None) -> dict:
    n = folder_name(name)
    f = get_folder(db, tenant_id, folder_id)
    rv = f.row_version if expected_row_version is None else expected_row_version
    if f.name == n:
        raise Invalid("nothing to change")
    old = f.name
    try:
        with db.begin_nested():
            res = db.execute(KbFolder.__table__.update().where(KbFolder.tenant_id == tenant_id, KbFolder.folder_id == folder_id, KbFolder.row_version == rv)
                             .values(name=n, name_key=n.lower(), row_version=KbFolder.row_version + 1, updated_by=actor[:64], updated_at=store.now()))
            if res.rowcount != 1:
                raise Conflict("the folder was changed by someone else: reload and try again")
            db.flush()
    except IntegrityError:
        raise Conflict("a folder with this name already exists here") from None
    db.expire(f)
    audit.record(db, tenant_id, actor, "folder_renamed", "folder", folder_id, folder_id=folder_id, name=n)
    fmap = folders_by_id(db, tenant_id)
    return _folder_view(fmap[folder_id], fmap) | {"previous_name": old}


def _subtree(fmap: dict[str, KbFolder], folder_id: str) -> set[str]:
    out, todo = {folder_id}, [folder_id]
    while todo:
        cur = todo.pop()
        for f in fmap.values():
            if f.parent_id == cur and f.folder_id not in out:
                out.add(f.folder_id)
                todo.append(f.folder_id)
    return out


def _height(fmap: dict[str, KbFolder], folder_id: str) -> int:
    kids = [f.folder_id for f in fmap.values() if f.parent_id == folder_id]
    return 1 + max((_height(fmap, k) for k in kids), default=0)


def move_folder(db: Session, tenant_id: str, actor: str, folder_id: str, parent_id: str | None, *, expected_row_version: int | None = None) -> dict:
    """Move a folder (with everything inside it) under another folder, or to the top level (None). Refused: moving into itself or one of its own sub-folders (a cycle),
    nesting deeper than MAX_DEPTH, or a sibling with the same name at the destination. Compare-and-set on the folder row; audited."""
    fmap = folders_by_id(db, tenant_id)
    if folder_id not in fmap:
        raise NotFound("folder not found")
    f = fmap[folder_id]
    if parent_id is not None:
        if parent_id not in fmap:
            raise NotFound("destination folder not found")
        if parent_id in _subtree(fmap, folder_id):
            raise Invalid("a folder cannot be moved into itself or into one of its own sub-folders")
    if f.parent_id == parent_id:
        return _folder_view(f, fmap) | {"unchanged": True}
    if (_depth(fmap, parent_id) if parent_id else 0) + _height(fmap, folder_id) > MAX_DEPTH:
        raise Invalid(f"folders can be nested at most {MAX_DEPTH} levels deep")
    rv = f.row_version if expected_row_version is None else expected_row_version
    old_parent = f.parent_id
    try:
        with db.begin_nested():
            res = db.execute(KbFolder.__table__.update().where(KbFolder.tenant_id == tenant_id, KbFolder.folder_id == folder_id, KbFolder.row_version == rv)
                             .values(parent_id=parent_id, parent_key=parent_id or "", row_version=KbFolder.row_version + 1, updated_by=actor[:64], updated_at=store.now()))
            if res.rowcount != 1:
                raise Conflict("the folder was changed by someone else: reload and try again")
            db.flush()
    except IntegrityError:
        raise Conflict("a folder with this name already exists in the destination") from None
    db.expire(f)
    audit.record(db, tenant_id, actor, "folder_moved", "folder", folder_id, folder_id=folder_id, from_folder=old_parent or "", to_folder=parent_id or "")
    fmap = folders_by_id(db, tenant_id)
    return _folder_view(fmap[folder_id], fmap)


def delete_folder(db: Session, tenant_id: str, actor: str, folder_id: str, *, expected_row_version: int | None = None) -> dict:
    """Delete an EMPTY folder. A folder that still holds sub-folders or documents (in any state, archived included) is refused with what is inside, so nothing is ever
    deleted or orphaned by accident: move or archive the contents first. Folders hold no content, so deleting one never touches a document or a version."""
    fmap = folders_by_id(db, tenant_id)
    if folder_id not in fmap:
        raise NotFound("folder not found")
    f = fmap[folder_id]
    rv = f.row_version if expected_row_version is None else expected_row_version
    if f.row_version != rv:
        raise Conflict("the folder was changed by someone else: reload and try again")
    kids = [x for x in fmap.values() if x.parent_id == folder_id]
    docs = db.execute(select(KbDocPlacement.doc_id).where(KbDocPlacement.tenant_id == tenant_id, KbDocPlacement.folder_id == folder_id)).scalars().all()
    if kids or docs:
        raise Conflict(f"the folder is not empty ({len(kids)} folder(s), {len(docs)} document(s)): move them elsewhere first")
    res = db.execute(KbFolder.__table__.delete().where(KbFolder.tenant_id == tenant_id, KbFolder.folder_id == folder_id, KbFolder.row_version == rv))
    if res.rowcount != 1:
        raise Conflict("the folder was changed by someone else: reload and try again")
    audit.record(db, tenant_id, actor, "folder_deleted", "folder", folder_id, folder_id=folder_id, parent_id=f.parent_id or "", name=f.name)
    return {"folder_id": folder_id, "deleted": True, "name": f.name}


def placements(db: Session, tenant_id: str) -> dict[str, str]:
    return {r[0]: r[1] for r in db.execute(select(KbDocPlacement.doc_id, KbDocPlacement.folder_id).where(KbDocPlacement.tenant_id == tenant_id)).all()}


def location(db: Session, tenant_id: str, doc_id: str) -> dict:
    row = db.get(KbDocPlacement, (tenant_id, doc_id))
    fid = row.folder_id if row else None
    fmap = folders_by_id(db, tenant_id) if fid else {}
    return {"folder_id": fid, "breadcrumbs": crumbs(fmap, fid), "path": path_of(fmap, fid)}


def move_document(db: Session, tenant_id: str, actor: str, doc_id: str, folder_id: str | None, *, expected_row_version: int | None = None) -> dict:
    """Move a document to a folder (None = the root). Content and versions are never touched; the move is a compare-and-set on the document row, so it
    cannot slip past a concurrent publish/archive/edit, and it is audited."""
    doc = store.get_doc(db, tenant_id, doc_id)
    if folder_id is not None:
        get_folder(db, tenant_id, folder_id)
    row = db.get(KbDocPlacement, (tenant_id, doc_id))
    current = row.folder_id if row else None
    if current == folder_id:
        return {"doc_id": doc_id, "folder_id": folder_id, "unchanged": True, "row_version": doc.row_version}
    store.bump(db, doc, actor, expected=doc.row_version if expected_row_version is None else expected_row_version)
    if folder_id is None:
        db.execute(delete(KbDocPlacement).where(KbDocPlacement.tenant_id == tenant_id, KbDocPlacement.doc_id == doc_id))
    elif row is None:
        db.add(KbDocPlacement(tenant_id=tenant_id, doc_id=doc_id, folder_id=folder_id, moved_by=actor[:64]))
    else:
        row.folder_id, row.moved_by, row.moved_at = folder_id, actor[:64], store.now()
    db.flush()
    audit.record(db, tenant_id, actor, "document_moved", "document", doc_id, doc_id=doc_id, from_folder=current or "", to_folder=folder_id or "")
    return {"doc_id": doc_id, "folder_id": folder_id, "unchanged": False, "row_version": get_row_version(db, tenant_id, doc_id)}


def get_row_version(db: Session, tenant_id: str, doc_id: str) -> int:
    return db.execute(select(KbDocument.row_version).where(KbDocument.tenant_id == tenant_id, KbDocument.doc_id == doc_id)).scalar_one()


def doc_folder_ids(db: Session, tenant_id: str, folder_id: str) -> set[str]:
    """`folder_id` and all of its descendants."""
    fmap = folders_by_id(db, tenant_id)
    if folder_id not in fmap:
        raise NotFound("folder not found")
    out, frontier = {folder_id}, [folder_id]
    while frontier:
        nxt = [f.folder_id for f in fmap.values() if f.parent_id in frontier and f.folder_id not in out]
        out.update(nxt)
        frontier = nxt
    return out


def tree(db: Session, tenant_id: str, *, published_only: bool) -> dict:
    """The whole folder tree plus a light row per document, in one call. Staff see published documents only (and only the folders that lead to one);
    administrators see everything."""
    fmap = folders_by_id(db, tenant_id)
    place = placements(db, tenant_id)
    q = select(KbDocument).where(KbDocument.tenant_id == tenant_id)
    if published_only:
        q = q.where(KbDocument.status == "published")
    docs = db.execute(q.order_by(KbDocument.title)).scalars().all()
    titles: dict[str, str] = {}
    if published_only and docs:
        titles = {r[0]: r[1] for r in db.execute(select(KbVersion.doc_id, KbVersion.title).join(
            KbDocument, (KbDocument.tenant_id == KbVersion.tenant_id) & (KbDocument.doc_id == KbVersion.doc_id) & (KbDocument.published_version == KbVersion.version_no))
            .where(KbVersion.tenant_id == tenant_id, KbDocument.status == "published")).all()}
    items = []
    for d in docs:
        fid = place.get(d.doc_id)
        if fid is not None and fid not in fmap:
            fid = None
        items.append({"doc_id": d.doc_id, "title": titles.get(d.doc_id, d.title), "category": d.category, "folder_id": fid, "status": d.status,
                      "published_version": d.published_version, "latest_version": d.latest_version, "updated_at": store._iso(d.updated_at or d.created_at)})
    keep = set(fmap)
    if published_only:                                   # prune branches that lead to no published document
        keep = set()
        for it in items:
            cur = it["folder_id"]
            while cur is not None and cur not in keep:
                keep.add(cur)
                cur = fmap[cur].parent_id
    counts: dict[str, int] = {}
    for it in items:
        if it["folder_id"]:
            counts[it["folder_id"]] = counts.get(it["folder_id"], 0) + 1
    folders = [_folder_view(f, fmap) | {"document_count": counts.get(f.folder_id, 0)} for f in sorted(fmap.values(), key=lambda f: (path_of(fmap, f.folder_id).lower())) if f.folder_id in keep]
    return {"folders": folders, "documents": items, "root_document_count": sum(1 for it in items if it["folder_id"] is None)}
