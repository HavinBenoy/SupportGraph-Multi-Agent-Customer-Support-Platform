"""Change requests (Part 1): staff propose edits to published documents; an administrator reviews, applies and publishes them.

State machine (Part 2; every status change goes through `_touch`/`check_transition`, so an invalid transition is refused in one place):
  draft -> analysing -> awaiting_clarification | awaiting_decisions   the AI-assisted staff phase (private to the author; analysis runs in the background)
  draft | awaiting_decisions | needs_revision -> submitted            staff submit (every AI suggestion must have a decision)
  submitted -> under_review -> approved | rejected | needs_revision   the administrator's decision; approval publishes NOTHING
  approved | failed -> processing -> publishing -> published | failed  the integrated release (see release.py); retry is safe
  cancelled                                                           withdrawn (staff before the decision, administrators until publication starts)
  applied    Part 1's "create DRAFT versions only" action, kept for compatibility (the Change Requests screen no longer uses it).

Guarantees: nothing here writes `documents`/`chunks`, a published version or its staged passages; the proposed text lives only in kb_change_items. Every item
remembers the published version it was based on (an immutable row), and the structured diff is recomputed by the server from that base on every save.
Concurrency: items and requests are compare-and-set on their own `row_version`; submit and apply refuse a document whose published version moved on since the
edit began, so nobody's published change is silently overwritten."""

import difflib
import secrets
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import KbChangeItem, KbChangeRequest, KbDocument, KbEvent, KbVersion
from app.knowledge import audit, diffing, store
from app.knowledge import validation as v
from app.knowledge.errors import Conflict, Forbidden, Invalid, NotFound
from app.knowledge.impact.evaluate import apply_edits

MAX_ITEMS = 25
_CONTROL = v._CONTROL
PRIVATE = ("draft", "analysing", "awaiting_clarification", "awaiting_decisions")           # visible to the author only
EDITABLE = ("draft", "awaiting_clarification", "awaiting_decisions", "needs_revision")      # the author may still change it
ACTIVE_ELSEWHERE = ("submitted", "under_review", "needs_revision", "approved", "processing", "publishing")
TRANSITIONS: dict[str, set[str]] = {
    "draft": {"analysing", "submitted", "cancelled"},
    "analysing": {"awaiting_clarification", "awaiting_decisions", "draft", "cancelled"},
    "awaiting_clarification": {"analysing", "cancelled"},
    "awaiting_decisions": {"submitted", "analysing", "cancelled"},
    "submitted": {"under_review", "approved", "rejected", "needs_revision", "cancelled"},
    "under_review": {"approved", "rejected", "needs_revision", "cancelled"},
    "needs_revision": {"submitted", "analysing", "cancelled"},
    "approved": {"processing", "applied", "cancelled"},
    "processing": {"publishing", "failed"},
    "publishing": {"published", "failed"},
    "failed": {"processing", "under_review", "cancelled"},
    "rejected": set(), "published": set(), "cancelled": set(), "applied": set(),
}


def check_transition(frm: str, to: str) -> None:
    if to not in TRANSITIONS.get(frm, set()):
        raise Conflict(f"a request that is {frm.replace('_', ' ')} cannot become {to.replace('_', ' ')}")


def included(it: KbChangeItem) -> bool:
    """Does this item take part in publication? An AI suggestion needs a staff decision to accept or edit it; a manual item always counts; an administrator can
    exclude any item. An item whose text equals the published text changes nothing and never counts."""
    if it.admin_decision == "excluded":
        return False
    if (it.stats or {}).get("delete"):
        return True                                                     # a deletion request: the "change" is the removal of the document
    if it.proposed_checksum == it.base_checksum:
        return False
    return it.origin == "manual" or it.decision in ("accepted", "manual")


def staff_included(it: KbChangeItem) -> bool:
    """As `included`, ignoring the administrator's own exclusion (what the staff member put forward)."""
    if (it.stats or {}).get("delete"):
        return True
    return it.proposed_checksum != it.base_checksum and (it.origin == "manual" or it.decision in ("accepted", "manual"))


def actionable(it: KbChangeItem) -> bool:
    return it.origin == "ai" and it.classification in ("definite", "potential")


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
def item_view(db: Session, it: KbChangeItem, *, detail: bool, overlaps: list | None = None) -> dict:
    doc = db.get(KbDocument, (it.tenant_id, it.doc_id))
    cur = doc.published_version if doc is not None and doc.status == "published" else None
    ai = it.ai_suggestion or {}
    out = {"doc_id": it.doc_id, "doc_title": doc.title if doc else it.doc_id, "base_version": it.base_version, "current_published_version": cur,
           "base_stale": cur != it.base_version, "proposed_title": it.proposed_title, "stats": it.stats, "origin": it.origin, "decision": it.decision,
           "rationale": it.rationale, "row_version": it.row_version, "updated_at": store._iso(it.updated_at or it.created_at), "result_version": it.result_version,
           "base_checksum": it.base_checksum[:16], "proposed_checksum": it.proposed_checksum[:16],
           "classification": it.classification, "confidence": it.confidence, "decided_by": it.decided_by, "decided_at": store._iso(it.decided_at),
           "admin_decision": it.admin_decision, "admin_note": it.admin_note, "admin_by": it.admin_by, "admin_at": store._iso(it.admin_at), "admin_edited": bool(it.admin_edited),
           "included": included(it), "staff_included": staff_included(it), "actionable": actionable(it), "process_status": it.process_status,
           "publish_status": it.publish_status, "process_error": it.process_error,
           "ai": {"reason": ai.get("reason"), "signals": ai.get("signals"), "uncertainty": ai.get("uncertainty"), "flags": ai.get("flags"), "path": ai.get("path"),
                  "passages": ai.get("passages") if detail else None, "edits": ai.get("edits") if detail else None,
                  "tasks": ai.get("tasks") if detail else None} if ai else None}
    if detail:
        out["diff"] = it.diff
        out["proposed_content"] = it.proposed_content
        out["final_edits"] = it.final_edits
        out["overlapping_requests"] = overlaps or []
    return out


def _overlaps(db: Session, r: KbChangeRequest, items: list[KbChangeItem]) -> dict[str, list]:
    if not items:
        return {}
    rows = db.execute(select(KbChangeItem.doc_id, KbChangeRequest.request_id, KbChangeRequest.status, KbChangeRequest.created_by)
                      .join(KbChangeRequest, and_(KbChangeRequest.tenant_id == KbChangeItem.tenant_id, KbChangeRequest.request_id == KbChangeItem.request_id))
                      .where(KbChangeItem.tenant_id == r.tenant_id, KbChangeItem.doc_id.in_([i.doc_id for i in items]), KbChangeRequest.request_id != r.request_id,
                             KbChangeRequest.status.in_(ACTIVE_ELSEWHERE))).all()
    out: dict[str, list] = {}
    for doc_id, rid, st, by in rows:
        out.setdefault(doc_id, []).append({"request_id": rid, "status": st, "created_by": by})
    return out


def request_view(db: Session, r: KbChangeRequest, *, detail: bool = False, admin: bool = False) -> dict:
    items = _items(db, r.tenant_id, r.request_id)
    titles = {d.doc_id: d.title for d in db.execute(select(KbDocument).where(KbDocument.tenant_id == r.tenant_id, KbDocument.doc_id.in_([i.doc_id for i in items]))).scalars()} if items else {}
    ai_items = [i for i in items if actionable(i)]
    an = r.analysis or {}
    out = {"request_id": r.request_id, "status": r.status, "source": r.source, "description": r.description, "row_version": r.row_version,
           "created_by": r.created_by, "created_at": store._iso(r.created_at), "updated_at": store._iso(r.updated_at or r.created_at),
           "submitted_by": r.submitted_by, "submitted_at": store._iso(r.submitted_at), "reviewed_by": r.reviewed_by, "reviewed_at": store._iso(r.reviewed_at),
           "review_note": r.review_note, "applied_by": r.applied_by, "applied_at": store._iso(r.applied_at), "return_note": r.return_note,
           "reference_doc_id": r.reference_doc_id, "failure": r.failure, "published_by": r.published_by, "published_at": store._iso(r.published_at),
           "release_attempt": r.release_attempt, "release": r.release, "phase_started_at": store._iso(r.phase_started_at),
           "document_count": len(items), "documents": [{"doc_id": i.doc_id, "title": titles.get(i.doc_id, i.doc_id)} for i in items],
           "added_chars": sum(int(i.stats.get("added_chars", 0)) for i in items if included(i)), "deleted_chars": sum(int(i.stats.get("deleted_chars", 0)) for i in items if included(i)),
           "counts": {"accepted": sum(1 for i in items if i.decision == "accepted"), "edited": sum(1 for i in items if i.decision == "manual"),
                      "discarded": sum(1 for i in items if i.decision == "discarded"), "pending": sum(1 for i in ai_items if i.decision is None),
                      "manual_items": sum(1 for i in items if i.origin == "manual"), "related": sum(1 for i in items if i.classification == "related_no_change"),
                      "included": sum(1 for i in items if included(i)), "excluded_by_admin": sum(1 for i in items if i.admin_decision == "excluded")},
           "analysis": {"stage": (an.get("progress") or {}).get("stage"), "done": (an.get("progress") or {}).get("done"), "total": (an.get("progress") or {}).get("total"),
                        "summary": an.get("summary"), "questions": an.get("questions") or [], "answers": an.get("answers") or {}, "error": an.get("error"), "model": an.get("model"),
                        "spec": an.get("spec"), "completed_at": store._iso(r.analysis_completed_at), "started_at": store._iso(r.analysis_started_at),
                        "attempt": r.analysis_attempt, "trace": an.get("trace") if detail else None,
                        "kind": an.get("kind", "change"), "deletion_reason": an.get("reason") if an.get("kind") == "deletion" else None,
                        "intents": an.get("intents") or [], "decomposition": an.get("decomposition"), "final_check": an.get("final_check") or []}
             if (r.analysis or r.analysis_attempt) else None}
    if detail:
        ov = _overlaps(db, r, items) if admin else {}
        out["items"] = [item_view(db, i, detail=True, overlaps=ov.get(i.doc_id)) for i in items]
    return out


def list_requests(db: Session, tenant_id: str, *, owner: str | None = None, status: str | None = None, include_drafts: bool = True, page: int = 1,
                  page_size: int = 25) -> dict:
    conds = [KbChangeRequest.tenant_id == tenant_id]
    if not include_drafts:
        conds.append(KbChangeRequest.status.notin_(PRIVATE))      # a request is private to its author until it is submitted
        conds.append(or_(KbChangeRequest.status != "cancelled", KbChangeRequest.submitted_at.isnot(None)))
    if owner is not None:
        conds.append(KbChangeRequest.created_by == owner)
    if status:
        conds.append(KbChangeRequest.status == status)
    total = db.execute(select(func.count()).select_from(KbChangeRequest).where(*conds)).scalar_one()
    rows = db.execute(select(KbChangeRequest).where(*conds).order_by(func.coalesce(KbChangeRequest.updated_at, KbChangeRequest.created_at).desc(), KbChangeRequest.request_id)
                      .limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return {"items": [request_view(db, r, admin=not include_drafts) for r in rows], "total": int(total), "page": page, "page_size": page_size}


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
    """Compare-and-set the request row. A status change must be a legal transition of the state machine."""
    if "status" in fields and not (fields["status"] == r.status and r.status in ("analysing", "processing", "publishing")):    # same-status = stale takeover
        check_transition(r.status, fields["status"])
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
    _no_edit_of_deletion(r)
    if r.status not in EDITABLE:
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: it can no longer be edited")
    doc = store.get_doc(db, tenant_id, doc_id)
    it = db.get(KbChangeItem, (tenant_id, request_id, doc_id))
    if it is not None and it.origin == "ai":
        raise Conflict("this document was suggested by the AI: record a decision on it instead of saving a draft over it")
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
    _no_edit_of_deletion(r)
    if r.status not in EDITABLE:
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: it can no longer be edited")
    cur = db.get(KbChangeItem, (tenant_id, request_id, doc_id))
    if cur is not None and cur.origin == "ai":
        raise Conflict("an AI suggestion cannot be removed: discard it instead so the reviewer can see why")
    n = db.execute(delete(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == doc_id)).rowcount
    if n != 1:
        raise NotFound("document is not part of this request")
    audit.record(db, tenant_id, actor, "change_draft_saved", "request", request_id, doc_id=doc_id, request_id=request_id, status="item_removed")
    return request_view(db, get_request(db, tenant_id, request_id), detail=True)


def update_description(db: Session, tenant_id: str, actor: str, request_id: str, description, *, expected_row_version: int | None = None) -> dict:
    r = get_request(db, tenant_id, request_id, owner=actor)
    if r.status not in EDITABLE:
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: it can no longer be edited")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    _touch(db, r, actor, description=_description(description, required=False))
    return request_view(db, get_request(db, tenant_id, request_id), detail=True)


def get_for_owner(db: Session, tenant_id: str, actor: str, request_id: str) -> dict:
    return request_view(db, get_request(db, tenant_id, request_id, owner=actor), detail=True)


def request_deletion(db: Session, tenant_id: str, actor: str, s: Settings, doc_id: str, *, reason: str, expected_version: int) -> dict:
    """A request to DELETE a published document (staff or admin). Nothing is removed now: the request goes to the administrators' queue ("submitted") like any change; only
    an administrator's approval and the publish step archive the document (it leaves retrieval; its versions and audit stay). The version is pinned: if the document is
    published again before approval, the deletion is refused as stale and must be requested again. One open deletion per document."""
    reason = " ".join((reason or "").split())
    if len(reason) < 5:
        raise Invalid("say why the document should be deleted (at least a few words)")
    doc = store.get_doc(db, tenant_id, doc_id)
    if doc.status != "published" or doc.published_version is None:
        raise Conflict("only a published document can be proposed for deletion")
    if doc.published_version != expected_version:
        raise Conflict("the document was published again: reload it before requesting its deletion")
    open_ids = db.execute(select(KbChangeRequest.request_id, KbChangeRequest.analysis).join(KbChangeItem, (KbChangeItem.tenant_id == KbChangeRequest.tenant_id)
                                                                                           & (KbChangeItem.request_id == KbChangeRequest.request_id))
                          .where(KbChangeRequest.tenant_id == tenant_id, KbChangeItem.doc_id == doc_id, KbChangeRequest.status.in_(ACTIVE_ELSEWHERE))).all()
    if any((an or {}).get("kind") == "deletion" for _, an in open_ids):
        raise Conflict("a deletion of this document is already waiting for an administrator")
    now = store.now()
    r = KbChangeRequest(tenant_id=tenant_id, request_id="cr_" + secrets.token_hex(8), status="submitted", source="manual",
                        description=f"Delete “{doc.title}”: {reason}"[:1000], row_version=0, created_by=actor[:64], submitted_by=actor[:64], submitted_at=now, updated_at=now,
                        analysis={"kind": "deletion", "reason": reason[:500], "doc_id": doc_id, "version": doc.published_version})
    db.add(r)
    db.flush()
    it = _make_item(db, tenant_id, r, actor, s, doc, content=None, title=None)
    it.decision, it.rationale, it.stats = "accepted", reason[:1000], {**(it.stats or {}), "delete": True}
    db.add(it)
    db.flush()
    audit.record(db, tenant_id, actor, "document_deletion_requested", "request", r.request_id, request_id=r.request_id, doc_id=doc_id, version_no=doc.published_version)
    return request_view(db, r, detail=True)


def is_deletion(r: KbChangeRequest) -> bool:
    return (r.analysis or {}).get("kind") == "deletion"


def _no_edit_of_deletion(r: KbChangeRequest) -> None:
    if is_deletion(r):
        raise Conflict("this is a document-deletion request: it can be approved, rejected or cancelled, not edited")


def _stale_docs(db: Session, tenant_id: str, items: list[KbChangeItem]) -> list[str]:
    out = []
    for it in items:
        doc = db.get(KbDocument, (tenant_id, it.doc_id))
        if doc is None or doc.status != "published" or doc.published_version != it.base_version:
            out.append(it.doc_id)
    return sorted(out)


def submit(db: Session, tenant_id: str, actor: str, request_id: str, *, description=None, expected_row_version: int | None = None) -> dict:
    """draft | awaiting_decisions | needs_revision -> submitted. Every AI suggestion must have a decision; manual items that changed nothing are dropped (AI items are kept so the
    reviewer sees what was discarded); at least one real change must remain; every included document must still be at the published version the change was based on.
    Publishes nothing and touches no version."""
    r = get_request(db, tenant_id, request_id, owner=actor)
    if r.status not in ("draft", "awaiting_decisions", "needs_revision"):
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: it cannot be submitted now")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    desc = _description(description if description is not None else r.description, required=True)
    items = _items(db, tenant_id, request_id)
    pending = [i.doc_id for i in items if actionable(i) and i.decision is None]
    if pending:
        raise Invalid(f"decide every suggested document first (accept, edit or discard): {len(pending)} still need a decision")
    stale = _stale_docs(db, tenant_id, [i for i in items if staff_included(i)])
    if stale:
        raise Conflict("these documents were published again (or unpublished) after the change was prepared, so it no longer applies to the live text: "
                       + ", ".join(stale) + ". Run the analysis again (or discard this draft and make the change again on the current version)")
    dropped = [i for i in items if i.origin == "manual" and i.proposed_checksum == i.base_checksum]
    for i in dropped:
        db.delete(i)
    db.flush()
    kept = [i for i in items if i not in dropped]
    if not any(staff_included(i) for i in kept):
        raise Invalid("there is nothing to submit: no document has an accepted or edited change that differs from the published text")
    db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.admin_decision == "confirmed")
               .values(admin_decision=None).execution_options(synchronize_session=False))            # a resubmission is reviewed afresh (exclusions stay)
    _touch(db, r, actor, status="submitted", description=desc, submitted_by=actor[:64], submitted_at=store.now(), return_note=None)
    audit.record(db, tenant_id, actor, "change_request_submitted", "request", request_id, request_id=request_id, items=len(kept), status="submitted",
                 workflow=r.source, count=sum(1 for i in kept if staff_included(i)))
    return request_view(db, get_request(db, tenant_id, request_id), detail=True)


STAFF_CANCEL = ("draft", "analysing", "awaiting_clarification", "awaiting_decisions", "needs_revision", "submitted")
ADMIN_CANCEL = ("submitted", "under_review", "needs_revision", "approved", "failed")


def cancel(db: Session, tenant_id: str, actor: str, request_id: str, *, admin: bool = False) -> dict:
    """Withdraw a request. Staff may withdraw their own request until an administrator has started reviewing it; an administrator until publication starts."""
    r = get_request(db, tenant_id, request_id, owner=None if admin else actor)
    if admin and r.status in PRIVATE:
        raise NotFound("change request not found")
    if r.status not in (ADMIN_CANCEL if admin else STAFF_CANCEL):
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: it cannot be cancelled")
    prior = r.status
    _touch(db, r, actor, status="cancelled")
    audit.record(db, tenant_id, actor, "change_request_cancelled", "request", request_id, request_id=request_id, from_status=prior, status="cancelled")
    return request_view(db, get_request(db, tenant_id, request_id), detail=True, admin=admin)


# ------------------------------------------------------------------------------------------------------------------------------ AI-assisted
def start_ai(db: Session, tenant_id: str, actor: str, s: Settings, *, description, reference_doc_id: str | None = None, request_id: str | None = None) -> dict:
    """Workflow B (no `request_id`): a new AI-assisted request from a description and an optional reference document. Workflow A (`request_id`): turn the author's own
    request (with the manual edit already saved) into an AI-assisted one. Nothing is analysed yet: `begin_analysis` does that."""
    desc = _description(description, required=True)
    if reference_doc_id is not None:
        store.get_doc(db, tenant_id, reference_doc_id)
    if request_id is None:
        r = KbChangeRequest(tenant_id=tenant_id, request_id="cr_" + secrets.token_hex(8), status="draft", source="ai_assisted", description=desc, row_version=0,
                            created_by=actor[:64], reference_doc_id=reference_doc_id)
        db.add(r)
        db.flush()
        audit.record(db, tenant_id, actor, "change_request_created", "request", r.request_id, request_id=r.request_id, status="draft", workflow="ai_assisted",
                     doc_id=reference_doc_id)
    else:
        r = get_request(db, tenant_id, request_id, owner=actor)
        if r.status not in EDITABLE:
            raise Conflict(f"this request is {r.status.replace('_', ' ')}: it can no longer be edited")
        _touch(db, r, actor, source="ai_assisted", description=desc, **({"reference_doc_id": reference_doc_id} if reference_doc_id else {}))
    return request_view(db, get_request(db, tenant_id, request_id or r.request_id), detail=True)


def _stale_phase(r: KbChangeRequest, seconds: int) -> bool:
    t = r.analysis_started_at
    return t is not None and (store.now() - (t if t.tzinfo else t.replace(tzinfo=store.now().tzinfo))) > timedelta(seconds=seconds)


def begin_analysis(db: Session, tenant_id: str, actor: str, s: Settings, request_id: str, *, answers: dict | None = None, description=None,
                   expected_row_version: int | None = None) -> tuple[dict, int]:
    """-> analysing (attempt + 1). Returns (view, attempt). The attempt number fences the background job: only the job holding the current attempt may write results,
    so a retried, cancelled or superseded run can never overwrite newer work. An analysis that has been 'running' longer than `kb_ai_analysis_stale_s` (the worker
    died) may be taken over; a live one gets 409."""
    r = get_request(db, tenant_id, request_id, owner=actor)
    _no_edit_of_deletion(r)
    if r.source != "ai_assisted":
        raise Conflict("this request is not an AI-assisted request")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    if r.status == "analysing" and not _stale_phase(r, s.kb_ai_analysis_stale_s):
        raise Conflict("the analysis is already running")
    if r.status not in ("draft", "awaiting_clarification", "awaiting_decisions", "needs_revision", "analysing"):
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: it cannot be analysed")
    prior = dict(r.analysis or {})
    given = dict(prior.get("answers") or {})
    if answers:
        valid = {q["id"]: q for q in prior.get("questions") or []}
        for k, val in answers.items():
            if k not in valid:
                raise Invalid(f"unknown question: {str(k)[:40]}")
            t = " ".join(str(val or "").split())
            if not t or len(t) > 200 or _CONTROL.search(t):
                raise Invalid("an answer must be 1 to 200 characters")
            given[k] = t
        audit.record(db, tenant_id, actor, "change_clarification_answered", "request", request_id, request_id=request_id, count=len(answers))
    elif r.status == "awaiting_clarification":
        raise Invalid("answer the clarification questions first")
    desc = _description(description if description is not None else r.description, required=True)
    attempt = r.analysis_attempt + 1
    was = r.status
    _touch(db, r, actor, status="analysing", description=desc, analysis_attempt=attempt, analysis_started_at=store.now(), analysis_completed_at=None,
           analysis={"answers": given, "questions": [], "progress": {"stage": "queued", "done": 0, "total": 0}, "summary": None, "error": None})
    audit.record(db, tenant_id, actor, "change_analysis_started", "request", request_id, request_id=request_id, attempt=attempt, from_status=was,
                 stale_takeover=was == "analysing")
    return request_view(db, get_request(db, tenant_id, request_id), detail=True), attempt


def _lines_edits(base: str, new: str) -> list[dict]:
    """A whole-line patch [{start,end,original,replacement}] turning `base` into `new` (so a free-form edit still renders as orange/blue and round-trips through
    `apply_edits`). A pure insertion or deletion is anchored on a neighbouring unchanged line so no span is ever empty; overlapping anchors are merged."""
    a, b = base.split("\n"), new.split("\n")
    pos = [0]
    for x in a:
        pos.append(pos[-1] + len(x) + 1)
    blocks: list[list[int]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if i1 == i2 or j1 == j2:
            if i1 > 0:
                i1, j1 = i1 - 1, j1 - 1
            else:
                i2, j2 = i2 + 1, j2 + 1
        if blocks and i1 < blocks[-1][1]:                      # shares an anchor line with the previous block
            blocks[-1][1], blocks[-1][3] = max(blocks[-1][1], i2), max(blocks[-1][3], j2)
        else:
            blocks.append([i1, i2, j1, j2])
    return [{"start": pos[i1], "end": pos[i2] - 1, "original": "\n".join(a[i1:i2]), "replacement": "\n".join(b[j1:j2])} for i1, i2, j1, j2 in blocks]


def _set_proposal(db: Session, tenant_id: str, s: Settings, it: KbChangeItem, base: KbVersion, edits: list[dict]) -> dict:
    """Rebuild the item's proposed text, diff and stats from the IMMUTABLE base version plus a patch. The server derives all of it; the client never supplies a diff."""
    try:
        body = apply_edits(base.content, edits)
    except ValueError:
        raise Conflict("the suggestion no longer matches the document: run the analysis again") from None
    body, _ = v.content(body, it.proposed_title, s)
    diff, st = diffing.build(base.title, it.proposed_title, base.content, body)
    return {"proposed_content": body, "proposed_checksum": v.checksum(it.proposed_title, body), "diff": diff, "stats": st, "final_edits": edits}


def _edits_from_input(db: Session, tenant_id: str, s: Settings, it: KbChangeItem, base: KbVersion, *, replacements, content) -> list[dict]:
    ai_edits = (it.ai_suggestion or {}).get("edits") or []
    if content is not None:
        body, _ = v.content(content, it.proposed_title, s)
        return _lines_edits(base.content, body)
    if not replacements:
        raise Invalid("provide the replacement text (or the full edited content)")
    cur = {i: dict(e) for i, e in enumerate(it.final_edits or ai_edits)}
    for rep in replacements:
        try:
            i, text = int(rep["index"]), str(rep["replacement"])
        except (KeyError, TypeError, ValueError):
            raise Invalid("each replacement needs an index and a text") from None
        if i not in cur:
            raise Invalid(f"there is no suggested passage number {i}")
        text = text.replace("\r", "").strip("\n")
        if not text.strip() or len(text) > 4000 or _CONTROL.search(text.replace("\n", " ")):
            raise Invalid("a replacement must be 1 to 4000 characters of text")
        cur[i]["replacement"] = text
    return [{k: e[k] for k in ("start", "end", "original", "replacement")} for _, e in sorted(cur.items())]


def decide_item(db: Session, tenant_id: str, actor: str, s: Settings, request_id: str, doc_id: str, decision: str, *, replacements: list | None = None,
                content: str | None = None, reason=None, expected_row_version: int | None = None) -> dict:
    """Record the staff decision on one AI-suggested document: accepted (the AI patch as proposed), manual (the staff member's own text; the diff is rebuilt), or
    discarded (with an optional reason; the published text stays as it is). Decisions are only possible while the request is editable, never on a stale document
    (accept/edit), and never on a document the AI judged to need no change."""
    if decision not in ("accepted", "manual", "discarded"):
        raise Invalid("decision must be accepted, manual or discarded")
    r = get_request(db, tenant_id, request_id, owner=actor)
    _no_edit_of_deletion(r)
    if r.status not in ("awaiting_decisions", "needs_revision"):
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: decisions cannot be recorded now")
    it = db.get(KbChangeItem, (tenant_id, request_id, doc_id))
    if it is None or not actionable(it):
        raise NotFound("this document has no suggestion to decide on")
    if expected_row_version is not None and it.row_version != expected_row_version:
        raise Conflict("this decision was changed from another window: reload it first")
    why = " ".join(str(reason or "").split())
    if len(why) > 500 or _CONTROL.search(why):
        raise Invalid("the reason must be at most 500 characters")
    base = store.get_version(db, tenant_id, doc_id, it.base_version)
    vals: dict = {"decision": decision, "rationale": why or None, "decided_by": actor[:64], "decided_at": store.now(), "updated_by": actor[:64], "updated_at": store.now()}
    if decision == "discarded":
        diff, st = diffing.build(base.title, base.title, base.content, base.content)
        vals.update(proposed_content=base.content, proposed_checksum=base.checksum, diff=diff, stats=st, final_edits=[], proposed_title=base.title)
    else:
        if _stale_docs(db, tenant_id, [it]):
            raise Conflict("this document was published again after the suggestion was made: run the analysis again, or discard the suggestion")
        if decision == "accepted":
            edits = [{k: e[k] for k in ("start", "end", "original", "replacement")} for e in (it.ai_suggestion or {}).get("edits") or []]
            if not edits:
                raise Invalid("the AI could not propose a replacement for this document: edit it manually or discard it")
        else:
            edits = _edits_from_input(db, tenant_id, s, it, base, replacements=replacements, content=content)
        vals.update(_set_proposal(db, tenant_id, s, it, base, edits))
        if vals["proposed_checksum"] == base.checksum:
            raise Invalid("this would not change the document: discard the suggestion instead")
    res = db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == doc_id,
                                                KbChangeItem.row_version == it.row_version).values(row_version=KbChangeItem.row_version + 1, **vals)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise Conflict("this decision was changed from another window: reload it first")
    db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id).values(updated_at=store.now())
               .execution_options(synchronize_session=False))
    db.expire(it)
    audit.record(db, tenant_id, actor, "change_decision_recorded", "request", request_id, doc_id=doc_id, request_id=request_id, decision=decision,
                 base_version=it.base_version, classification=it.classification, confidence=it.confidence)
    return item_view(db, db.get(KbChangeItem, (tenant_id, request_id, doc_id)), detail=True)


# ------------------------------------------------------------------------------------------------------------------------------ administrators
def admin_get(db: Session, tenant_id: str, request_id: str) -> dict:
    r = get_request(db, tenant_id, request_id)
    if r.status in PRIVATE or (r.status == "cancelled" and r.submitted_at is None):
        raise NotFound("change request not found")          # a request is private to its author until it is submitted
    return request_view(db, r, detail=True, admin=True)


def _admin_request(db: Session, tenant_id: str, request_id: str, *, expected_row_version: int | None = None) -> KbChangeRequest:
    r = get_request(db, tenant_id, request_id)
    if r.status in PRIVATE or (r.status == "cancelled" and r.submitted_at is None):
        raise NotFound("change request not found")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    return r


def start_review(db: Session, tenant_id: str, actor: str, request_id: str) -> dict:
    """submitted -> under_review (idempotent: opening a request that is already under review changes nothing)."""
    r = _admin_request(db, tenant_id, request_id)
    if r.status == "under_review":
        return request_view(db, r, detail=True, admin=True)
    if r.status != "submitted":
        return request_view(db, r, detail=True, admin=True)          # viewing a finished request is allowed; only a submitted one starts a review
    _touch(db, r, actor, status="under_review")
    audit.record(db, tenant_id, actor, "change_request_review_started", "request", request_id, request_id=request_id, status="under_review")
    return request_view(db, get_request(db, tenant_id, request_id), detail=True, admin=True)


def reopen(db: Session, tenant_id: str, actor: str, request_id: str, note=None) -> dict:
    """failed -> under_review: after a failed release (nothing was published) the administrator may go back to review, for example to exclude a document that became
    stale, and approve again. Nothing live changed, so nothing needs undoing; draft versions made by the failed release are reused or replaced on the next attempt."""
    r = _admin_request(db, tenant_id, request_id)
    if r.status != "failed":
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: only a failed release can be reopened for review")
    _touch(db, r, actor, status="under_review", failure=None)
    audit.record(db, tenant_id, actor, "change_request_review_started", "request", request_id, request_id=request_id, status="under_review", workflow="reopen")
    return request_view(db, get_request(db, tenant_id, request_id), detail=True, admin=True)


def _reviewable(r: KbChangeRequest) -> None:
    if r.status not in ("submitted", "under_review"):
        raise Conflict(f"this request is {r.status.replace('_', ' ')}: it can no longer be reviewed")


def _note(note, limit: int = 500) -> str | None:
    n = " ".join(str(note or "").split())
    if len(n) > limit or _CONTROL.search(n):
        raise Invalid(f"note must be at most {limit} characters")
    return n or None


def admin_edit_item(db: Session, tenant_id: str, actor: str, s: Settings, request_id: str, doc_id: str, *, replacements: list | None = None, content: str | None = None,
                    note=None, expected_row_version: int | None = None) -> dict:
    """The administrator's final manual edit of one document before approval. The patch is rebuilt from the immutable base version; the item is marked admin-edited and
    confirmed. An administrator can only edit what staff put forward: a document staff discarded stays discarded."""
    r = _admin_request(db, tenant_id, request_id)
    _no_edit_of_deletion(r)
    _reviewable(r)
    it = db.get(KbChangeItem, (tenant_id, request_id, doc_id))
    if it is None:
        raise NotFound("document is not part of this request")
    if not staff_included(it) and it.proposed_checksum == it.base_checksum:
        raise Conflict("staff discarded this document: it cannot be edited into the request")
    if expected_row_version is not None and it.row_version != expected_row_version:
        raise Conflict("this item was changed by someone else: reload it first")
    if _stale_docs(db, tenant_id, [it]):
        raise Conflict("this document was published again after the change was prepared: the request must be returned for a new analysis")
    base = store.get_version(db, tenant_id, doc_id, it.base_version)
    if replacements is None and content is None:
        raise Invalid("provide the replacement text (or the full edited content)")
    if it.origin == "ai" and content is None:
        edits = _edits_from_input(db, tenant_id, s, it, base, replacements=replacements, content=None)
    else:
        if content is None:
            raise Invalid("provide the full edited content")
        edits = _edits_from_input(db, tenant_id, s, it, base, replacements=None, content=content)
    vals = _set_proposal(db, tenant_id, s, it, base, edits)
    if vals["proposed_checksum"] == base.checksum:
        raise Invalid("this would not change the document: exclude it instead")
    res = db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == doc_id,
                                                KbChangeItem.row_version == it.row_version)
                     .values(row_version=KbChangeItem.row_version + 1, admin_edited=True, admin_decision="confirmed", admin_by=actor[:64], admin_at=store.now(),
                             admin_note=_note(note), **vals).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise Conflict("this item was changed by someone else: reload it first")
    db.expire(it)
    audit.record(db, tenant_id, actor, "change_item_edited", "request", request_id, doc_id=doc_id, request_id=request_id, admin_edited=True, base_version=it.base_version)
    return item_view(db, db.get(KbChangeItem, (tenant_id, request_id, doc_id)), detail=True, overlaps=_overlaps(db, r, [it]).get(doc_id))


def admin_item_decision(db: Session, tenant_id: str, actor: str, request_id: str, doc_id: str, decision: str, note=None, *, expected_row_version: int | None = None) -> dict:
    """Confirm or exclude one document. An excluded document is not published (the request can still be approved without it); staff-discarded and AI-only items are
    shown to the administrator but cannot be confirmed into the request."""
    if decision not in ("confirmed", "excluded"):
        raise Invalid("decision must be confirmed or excluded")
    r = _admin_request(db, tenant_id, request_id)
    _reviewable(r)
    it = db.get(KbChangeItem, (tenant_id, request_id, doc_id))
    if it is None:
        raise NotFound("document is not part of this request")
    if expected_row_version is not None and it.row_version != expected_row_version:
        raise Conflict("this item was changed by someone else: reload it first")
    if decision == "confirmed" and not (it.origin == "manual" or it.decision in ("accepted", "manual")):
        raise Conflict("staff did not put this document forward: it cannot be confirmed")
    if decision == "confirmed" and it.proposed_checksum == it.base_checksum:
        raise Conflict("this document would not change")
    if decision == "excluded" and not staff_included(it):
        raise Conflict("this document is not part of the request to publish")
    n = _note(note)
    if decision == "excluded" and not n:
        raise Invalid("say why the document is excluded")
    res = db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == doc_id,
                                                KbChangeItem.row_version == it.row_version)
                     .values(row_version=KbChangeItem.row_version + 1, admin_decision=decision, admin_by=actor[:64], admin_at=store.now(), admin_note=n)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise Conflict("this item was changed by someone else: reload it first")
    db.expire(it)
    audit.record(db, tenant_id, actor, "change_item_reviewed", "request", request_id, doc_id=doc_id, request_id=request_id, decision=decision)
    return item_view(db, db.get(KbChangeItem, (tenant_id, request_id, doc_id)), detail=True, overlaps=_overlaps(db, r, [it]).get(doc_id))


def review(db: Session, tenant_id: str, actor: str, s: Settings, request_id: str, decision: str, note=None, *, expected_row_version: int | None = None) -> dict:
    """The request-level decision. approve: publishes NOTHING (it only makes the request eligible for `release`); reject: ends it; return: sends it back to the author
    with a required note."""
    if decision not in ("approve", "reject", "return"):
        raise Invalid("decision must be approve, reject or return")
    r = _admin_request(db, tenant_id, request_id, expected_row_version=expected_row_version)
    _reviewable(r)
    if decision == "approve" and s.kb_require_separate_reviewer and r.submitted_by == actor:
        raise Forbidden("a different administrator must review a request that you submitted")
    n = _note(note)
    if decision == "return" and not n:
        raise Invalid("say what needs to change when returning a request")
    if decision == "return" and is_deletion(r):
        raise Invalid("a deletion request cannot be returned for editing: approve or reject it")
    if decision == "approve":
        items = _items(db, tenant_id, request_id)
        inc = [i for i in items if included(i)]
        if not inc:
            raise Invalid("nothing is left to publish: every document was discarded or excluded")
        stale = _stale_docs(db, tenant_id, inc)
        if stale:
            raise Conflict("these documents were published again after the change was prepared, so it cannot be approved as it stands: " + ", ".join(stale)
                           + ". Exclude them or return the request for a new analysis")
        for i in inc:
            if i.admin_decision is None:                                       # approving the request confirms every remaining included document
                db.execute(update(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.doc_id == i.doc_id)
                           .values(admin_decision="confirmed", admin_by=actor[:64], admin_at=store.now()).execution_options(synchronize_session=False))
    new = {"approve": "approved", "reject": "rejected", "return": "needs_revision"}[decision]
    fields = {"status": new, "reviewed_by": actor[:64], "reviewed_at": store.now(), "review_note": n}
    if decision == "return":
        fields["return_note"] = n
    _touch(db, r, actor, **fields)
    audit.record(db, tenant_id, actor, {"approve": "change_request_approved", "reject": "change_request_rejected", "return": "change_request_returned"}[decision], "request",
                 request_id, request_id=request_id, decision=decision, status=new)
    return request_view(db, get_request(db, tenant_id, request_id), detail=True, admin=True)


def apply(db: Session, tenant_id: str, actor: str, s: Settings, request_id: str, *, expected_row_version: int | None = None) -> dict:
    """approved -> applied: create one ordinary DRAFT version per item (authored by the staff member who proposed it). All or nothing. The versions still have to be
    processed and published through the normal M6 pipeline; nothing becomes live here."""
    r = get_request(db, tenant_id, request_id)
    _no_edit_of_deletion(r)
    if r.status != "approved":
        raise Conflict(f"this request is {r.status}: only an approved request can be applied")
    if expected_row_version is not None and r.row_version != expected_row_version:
        raise Conflict("the request was changed by someone else: reload it and try again")
    items = [i for i in _items(db, tenant_id, request_id) if included(i)]
    if not items:
        raise Invalid("the request has no changes")
    stale = _stale_docs(db, tenant_id, items)
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


def request_audit(db: Session, tenant_id: str, request_id: str, *, admin: bool, owner: str | None = None, limit: int = 300) -> dict:
    """The audit history of one request, oldest first: the request's own events plus the version events (created, processed, published) of the versions it made.
    Events are ids, counts and status words only (audit.SAFE_KEYS): never document text, descriptions or notes."""
    r = get_request(db, tenant_id, request_id, owner=owner)
    if admin and (r.status in PRIVATE or (r.status == "cancelled" and r.submitted_at is None)):
        raise NotFound("change request not found")
    made = [(i.doc_id, i.result_version) for i in _items(db, tenant_id, request_id) if i.result_version is not None]
    conds = [and_(KbEvent.resource_type == "request", KbEvent.resource_id == request_id)]
    if made:
        conds.append(and_(KbEvent.resource_type == "version", KbEvent.resource_id.in_([f"{d}@{n}" for d, n in made])))
    rows = db.execute(select(KbEvent).where(KbEvent.tenant_id == tenant_id, or_(*conds)).order_by(KbEvent.id).limit(limit)).scalars().all()
    return {"request_id": request_id, "items": [{"id": e.id, "at": store._iso(e.created_at), "actor": e.actor, "action": e.action, "doc_id": e.doc_id, "version": e.version_no,
                                                 "summary": e.summary} for e in rows]}
