"""Change requests (Part 1): staff propose edits to published documents; an administrator reviews, applies and publishes them.

  draft      the owner is editing; items can be added/saved/removed. Nothing is visible to anyone else.
  submitted  frozen; waits for an administrator.
  approved / rejected   the administrator's decision (with a note).
  applied    the approved edits were turned into ordinary DRAFT versions (store.create_version). They go live only through the existing M6 process + publish.
  cancelled  withdrawn by the owner.

Guarantees: nothing here writes `documents`/`chunks`, a published version or its staged passages; the proposed text lives only in kb_change_items. Every item
remembers the published version it was based on (an immutable row), and the structured diff is recomputed by the server from that base on every save.
Concurrency: items and requests are compare-and-set on their own `row_version`; submit and apply refuse a document whose published version moved on since the
edit began, so nobody's published change is silently overwritten."""

import secrets

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import KbChangeItem, KbChangeRequest, KbDocument, KbVersion
from app.knowledge import audit, diffing, store
from app.knowledge import validation as v
from app.knowledge.errors import Conflict, Forbidden, Invalid, NotFound

MAX_ITEMS = 25
_CONTROL = v._CONTROL


def _description(value, *, required: bool) -> str | None:
    t = " ".join(str(value or "").split())
    if not t:
        if required:
            raise Invalid("describe the change: say what changed and why (at most 1000 characters)")
        return None
    if len(t) > 1000 or _CONTROL.search(t):
        raise Invalid("description must be at most 1000 characters")
    return t


# --------------------------------------------------------------------------------------------------------------------------------- lookup
def get_request(db: Session, tenant_id: str, request_id: str, *, owner: str | None = None) -> KbChangeRequest:
    r = db.get(KbChangeRequest, (tenant_id, request_id))
    if r is None or (owner is not None and r.created_by != owner):
        raise NotFound("change request not found")           # not yours / not your tenant: the same answer as 'does not exist'
    return r


def _items(db: Session, tenant_id: str, request_id: str) -> list[KbChangeItem]:
    return list(db.execute(select(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id).order_by(KbChangeItem.doc_id)).scalars())


def _published(db: Session, tenant_id: str, doc: KbDocument) -> KbVersion:
    if doc.status != "published" or doc.published_version is None:
        raise Conflict("only a published document can be edited: nothing is live for this one")
    return store.get_version(db, tenant_id, doc.doc_id, doc.published_version)


# ----------------------------------------------------------------------------------------------------------------------------------- views
def item_view(db: Session, it: KbChangeItem, *, detail: bool) -> dict:
    doc = db.get(KbDocument, (it.tenant_id, it.doc_id))
    cur = doc.published_version if doc is not None and doc.status == "published" else None
    out = {"doc_id": it.doc_id, "doc_title": doc.title if doc else it.doc_id, "base_version": it.base_version, "current_published_version": cur,
           "base_stale": cur != it.base_version, "proposed_title": it.proposed_title, "stats": it.stats, "origin": it.origin, "decision": it.decision,
           "rationale": it.rationale, "row_version": it.row_version, "updated_at": store._iso(it.updated_at or it.created_at), "result_version": it.result_version,
           "base_checksum": it.base_checksum[:16], "proposed_checksum": it.proposed_checksum[:16]}
    if detail:
        out["diff"] = it.diff
        out["proposed_content"] = it.proposed_content
    return out


def request_view(db: Session, r: KbChangeRequest, *, detail: bool = False) -> dict:
    items = _items(db, r.tenant_id, r.request_id)
    titles = {d.doc_id: d.title for d in db.execute(select(KbDocument).where(KbDocument.tenant_id == r.tenant_id, KbDocument.doc_id.in_([i.doc_id for i in items]))).scalars()} if items else {}
    out = {"request_id": r.request_id, "status": r.status, "source": r.source, "description": r.description, "row_version": r.row_version,
           "created_by": r.created_by, "created_at": store._iso(r.created_at), "updated_at": store._iso(r.updated_at or r.created_at),
           "submitted_by": r.submitted_by, "submitted_at": store._iso(r.submitted_at), "reviewed_by": r.reviewed_by, "reviewed_at": store._iso(r.reviewed_at),
           "review_note": r.review_note, "applied_by": r.applied_by, "applied_at": store._iso(r.applied_at),
           "document_count": len(items), "documents": [{"doc_id": i.doc_id, "title": titles.get(i.doc_id, i.doc_id)} for i in items],
           "added_chars": sum(int(i.stats.get("added_chars", 0)) for i in items), "deleted_chars": sum(int(i.stats.get("deleted_chars", 0)) for i in items)}
    if detail:
        out["items"] = [item_view(db, i, detail=True) for i in items]
    return out


def list_requests(db: Session, tenant_id: str, *, owner: str | None = None, status: str | None = None, include_drafts: bool = True, page: int = 1,
                  page_size: int = 25) -> dict:
    conds = [KbChangeRequest.tenant_id == tenant_id]
    if not include_drafts:
        conds.append(KbChangeRequest.status != "draft")          # a draft is private to its author
    if owner is not None:
        conds.append(KbChangeRequest.created_by == owner)
    if status:
        conds.append(KbChangeRequest.status == status)
    total = db.execute(select(func.count()).select_from(KbChangeRequest).where(*conds)).scalar_one()
    rows = db.execute(select(KbChangeRequest).where(*conds).order_by(func.coalesce(KbChangeRequest.updated_at, KbChangeRequest.created_at).desc(), KbChangeRequest.request_id)
                      .limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return {"items": [request_view(db, r) for r in rows], "total": int(total), "page": page, "page_size": page_size}


# --------------------------------------------------------------------------------------------------------------------------------- preview
def preview(db: Session, tenant_id: str, s: Settings, doc_id: str, *, content: str, title: str | None = None) -> dict:
    """The structured diff of `content` against the document's PUBLISHED version. Writes nothing."""
    doc = store.get_doc(db, tenant_id, doc_id)
    base = _published(db, tenant_id, doc)
    new_title = v.title(title) if title else base.title
    body, size = v.content(content, new_title, s)
    diff, st = diffing.build(base.title, new_title, base.content, body)
    return {"doc_id": doc_id, "base_version": base.version_no, "diff": diff, "stats": st, "size_bytes": size, "unchanged": base.checksum == v.checksum(new_title, body)}


# ------------------------------------------------------------------------------------------------------------------------- draft lifecycle
def _touch(db: Session, r: KbChangeRequest, actor: str, **fields) -> None:
    """Compare-and-set the request row (draft only unless `fields` moves the status)."""
    res = db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == r.tenant_id, KbChangeRequest.request_id == r.request_id,
                                                   KbChangeRequest.row_version == r.row_version)
                     .values(row_version=KbChangeRequest.row_version + 1, updated_at=store.now(), **fields).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.expire(r)
        raise Conflict("the request was changed by someone else: reload it and try again")
    db.expire(r)


def _make_item(db: Session, tenant_id: str, r: KbChangeRequest, actor: str, s: Settings, doc: KbDocument, *, content: str | None, title: str | None) -> KbChangeItem:
    base = _published(db, tenant_id, doc)
    new_title = v.title(title) if title else base.title
    if content is None:
        body = base.content
    else:
        body, _ = v.content(content, new_title, s)
    diff, st = diffing.build(base.title, new_title, base.content, body)
    it = KbChangeItem(tenant_id=tenant_id, request_id=r.request_id, doc_id=doc.doc_id, base_version=base.version_no, base_checksum=base.checksum,
                      proposed_title=new_title, proposed_content=body, proposed_checksum=v.checksum(new_title, body), diff=diff, stats=st, origin="manual",
                      row_version=0, updated_by=actor[:64], updated_at=store.now())
    return it


def start(db: Session, tenant_id: str, actor: str, s: Settings, *, doc_id: str | None = None, description: str | None = None) -> tuple[dict, bool]:
    """Begin a draft request, optionally with the first document. If `actor` already has an open draft containing that document, that draft is returned
    (so pressing "Make changes" twice never creates two competing drafts). Returns (view, existing)."""
    desc = _description(description, required=False)
    doc = None
    if doc_id is not None:
        doc = store.get_doc(db, tenant_id, doc_id)
        _published(db, tenant_id, doc)
        prior = db.execute(select(KbChangeRequest).join(KbChangeItem, (KbChangeItem.tenant_id == KbChangeRequest.tenant_id) & (KbChangeItem.request_id == KbChangeRequest.request_id))
                           .where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.created_by == actor, KbChangeRequest.status == "draft", KbChangeItem.doc_id == doc_id)
                           .order_by(KbChangeRequest.created_at.desc())).scalars().first()
        if prior is not None:
            return request_view(db, prior, detail=True), True
    r = KbChangeRequest(tenant_id=tenant_id, request_id="cr_" + secrets.token_hex(8), status="draft", source="manual", description=desc, row_version=0, created_by=actor[:64])
    db.add(r)
    db.flush()
    audit.record(db, tenant_id, actor, "change_request_created", "request", r.request_id, doc_id=doc_id, request_id=r.request_id, status="draft")
    if doc is not None:
        db.add(_make_item(db, tenant_id, r, actor, s, doc, content=None, title=None))
        db.flush()
    return request_view(db, r, detail=True), False


def save_item(db: Session, tenant_id: str, actor: str, s: Settings, request_id: str, doc_id: str, *, content: str, title: str | None = None,
              expected_row_version: int | None = None) -> dict:
    """Save the owner's edit of one document (adds the document to the request on first save). The diff is recomputed from the immutable base version.
    `expected_row_version` is the item's token: a second browser tab holding an older copy gets 409 instead of silently overwriting this one."""
    r = get_request(db, tenant_id, request_id, owner=actor)
    if r.status != "draft":
        raise Conflict(f"this request is {r.status}: it can no longer be edited")
    doc = store.get_doc(db, tenant_id, doc_id)
    it = db.get(KbChangeItem, (tenant_id, request_id, doc_id))
    if it is None:
        if expected_row_version is not None:
            raise Conflict("this document is no longer part of the request: reload it")
        if len(_items(db, tenant_id, request_id)) >= MAX_ITEMS:
            raise Invalid(f"a request can change at most {MAX_ITEMS} documents")
        new = _make_item(db, tenant_id, r, actor, s, doc, content=content, title=title)
        try:
            with db.begin_nested():
                db.add(new)
                db.flush()
        except IntegrityError:
            raise Conflict("this document was just added to the request by another window: reload it") from None
        it = new
    else:
        if expected_row_version is None:
            raise Invalid("expected_row_version is required to save over an existing edit")
        base = store.get_version(db, tenant_id, doc_id, it.base_version)                    # the ORIGINAL the edit is measured against, never the live text
        new_title = v.title(title) if title else it.proposed_title
        body, _ = v.content(content, new_title, s)
        diff, st = diffing.build(base.title, new_title, base.content, body)
        res = db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == doc_id,
                                                    KbChangeItem.row_version == expected_row_version)
                         .values(proposed_title=new_title, proposed_content=body, proposed_checksum=v.checksum(new_title, body), diff=diff, stats=st,
                                 row_version=KbChangeItem.row_version + 1, updated_at=store.now(), updated_by=actor[:64]).execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise Conflict("this edit was saved from another window since you opened it: reload it so you do not overwrite that work")
        db.expire(it)
        it = db.get(KbChangeItem, (tenant_id, request_id, doc_id))
    db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id).values(updated_at=store.now())
               .execution_options(synchronize_session=False))
    audit.record(db, tenant_id, actor, "change_draft_saved", "request", request_id, doc_id=doc_id, request_id=request_id, base_version=it.base_version,
                 added=int(it.stats.get("added_chars", 0)), deleted=int(it.stats.get("deleted_chars", 0)))
    return item_view(db, it, detail=True)


def remove_item(db: Session, tenant_id: str, actor: str, request_id: str, doc_id: str) -> dict:
    r = get_request(db, tenant_id, request_id, owner=actor)
    if r.status != "draft":
        raise Conflict(f"this request is {r.status}: it can no longer be edited")
    n = db.execute(delete(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == doc_id)).rowcount
    if n != 1:
        raise NotFound("document is not part of this request")
    audit.record(db, tenant_id, actor, "change_draft_saved", "request", request_id, doc_id=doc_id, request_id=request_id, status="item_removed")
    return request_view(db, get_request(db, tenant_id, request_id), detail=True)


def update_description(db: Session, tenant_id: str, actor: str, request_id: str, description, *, expected_row_version: int | None = None) -> dict:
    r = get_request(db, tenant_id, request_id, owner=actor)
    if r.status != "draft":
        raise Conflict(f"this request is {r.status}: it can no longer be edited")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    _touch(db, r, actor, description=_description(description, required=False))
    return request_view(db, get_request(db, tenant_id, request_id), detail=True)


def get_for_owner(db: Session, tenant_id: str, actor: str, request_id: str) -> dict:
    return request_view(db, get_request(db, tenant_id, request_id, owner=actor), detail=True)


def submit(db: Session, tenant_id: str, actor: str, request_id: str, *, description=None, expected_row_version: int | None = None) -> dict:
    """draft -> submitted. Unchanged items are dropped; at least one real change must remain; every document must still be at the published version the edit
    started from. Publishes nothing and touches no version."""
    r = get_request(db, tenant_id, request_id, owner=actor)
    if r.status != "draft":
        raise Conflict(f"this request is already {r.status}")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    desc = _description(description if description is not None else r.description, required=True)
    items = _items(db, tenant_id, request_id)
    stale = []
    for it in items:
        doc = db.get(KbDocument, (tenant_id, it.doc_id))
        if doc is None or doc.status != "published" or doc.published_version != it.base_version:
            stale.append(it.doc_id)
    if stale:
        raise Conflict("these documents were published again (or unpublished) after you started editing, so your change no longer applies to the live text: "
                       + ", ".join(sorted(stale)) + ". Discard this draft and make your change again on the current version")
    unchanged = [i for i in items if i.proposed_checksum == i.base_checksum]
    for i in unchanged:
        db.delete(i)
    db.flush()
    if len(items) == len(unchanged):
        raise Invalid("there is nothing to submit: none of the documents differs from the published text")
    _touch(db, r, actor, status="submitted", description=desc, submitted_by=actor[:64], submitted_at=store.now())
    audit.record(db, tenant_id, actor, "change_request_submitted", "request", request_id, request_id=request_id, items=len(items) - len(unchanged), status="submitted")
    return request_view(db, get_request(db, tenant_id, request_id), detail=True)


def cancel(db: Session, tenant_id: str, actor: str, request_id: str) -> dict:
    r = get_request(db, tenant_id, request_id, owner=actor)
    if r.status not in ("draft", "submitted"):
        raise Conflict(f"this request is {r.status}: it cannot be cancelled")
    _touch(db, r, actor, status="cancelled")
    audit.record(db, tenant_id, actor, "change_request_cancelled", "request", request_id, request_id=request_id, from_status=r.status, status="cancelled")
    return request_view(db, get_request(db, tenant_id, request_id), detail=True)


# ------------------------------------------------------------------------------------------------------------------------------ administrators
def admin_get(db: Session, tenant_id: str, request_id: str) -> dict:
    r = get_request(db, tenant_id, request_id)
    if r.status == "draft":
        raise NotFound("change request not found")          # a draft is private to its author until it is submitted
    return request_view(db, r, detail=True)


def review(db: Session, tenant_id: str, actor: str, s: Settings, request_id: str, decision: str, note=None, *, expected_row_version: int | None = None) -> dict:
    if decision not in ("approve", "reject"):
        raise Invalid("decision must be approve or reject")
    r = get_request(db, tenant_id, request_id)
    if r.status != "submitted":
        raise Conflict(f"this request is {r.status}: only a submitted request can be reviewed")
    if s.kb_require_separate_reviewer and r.submitted_by == actor:
        raise Forbidden("a different administrator must review a request that you submitted")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    n = " ".join(str(note or "").split())
    if len(n) > 500 or _CONTROL.search(n):
        raise Invalid("note must be at most 500 characters")
    new = "approved" if decision == "approve" else "rejected"
    _touch(db, r, actor, status=new, reviewed_by=actor[:64], reviewed_at=store.now(), review_note=n or None)
    audit.record(db, tenant_id, actor, "change_request_approved" if new == "approved" else "change_request_rejected", "request", request_id, request_id=request_id,
                 decision=decision, status=new)
    return request_view(db, get_request(db, tenant_id, request_id), detail=True)


def apply(db: Session, tenant_id: str, actor: str, s: Settings, request_id: str, *, expected_row_version: int | None = None) -> dict:
    """approved -> applied: create one ordinary DRAFT version per item (authored by the staff member who proposed it). All or nothing. The versions still have to be
    processed and published through the normal M6 pipeline; nothing becomes live here."""
    r = get_request(db, tenant_id, request_id)
    if r.status != "approved":
        raise Conflict(f"this request is {r.status}: only an approved request can be applied")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    items = _items(db, tenant_id, request_id)
    if not items:
        raise Invalid("the request has no changes")
    stale = []
    for it in items:
        doc = db.get(KbDocument, (tenant_id, it.doc_id))
        if doc is None or doc.status != "published" or doc.published_version != it.base_version:
            stale.append(it.doc_id)
    if stale:
        raise Conflict("the live text of these documents changed after the edit was made, so applying it would overwrite that change: " + ", ".join(sorted(stale))
                       + ". Reject this request and ask for the change again")
    _touch(db, r, actor, status="applied", applied_by=actor[:64], applied_at=store.now())
    note = (r.description or "Change request")[:500]
    made = []
    for it in items:                                              # any Conflict here rolls the whole transaction back, including the status change above
        ver = store.create_version(db, tenant_id, actor, s, it.doc_id, content=it.proposed_content, title=it.proposed_title, change_note=note, author=r.created_by)
        db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == it.doc_id)
                   .values(result_version=ver.version_no).execution_options(synchronize_session=False))
        db.expire(it)                                              # the bulk UPDATE above bypassed the session: reload so the response shows result_version
        made.append(ver.version_no)
        audit.record(db, tenant_id, actor, "change_request_applied", "request", request_id, doc_id=it.doc_id, version_no=ver.version_no, request_id=request_id,
                     base_version=it.base_version, result_version=ver.version_no)
    db.flush()
    return request_view(db, get_request(db, tenant_id, request_id), detail=True)
