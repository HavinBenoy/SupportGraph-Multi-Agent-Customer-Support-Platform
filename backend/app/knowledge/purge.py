"""Permanent deletion of a knowledge document (final M6, refinement 2.4) — a separate administrative operation, distinct from archiving.

WHAT THE LIFECYCLE ALREADY HAD (unchanged):
  * unpublish   — the live version stops answering at once; the document returns to draft; nothing is deleted.
  * archive     — the document leaves retrieval; every version and the audit trail are kept; it can be unarchived.
  * deletion request -> administrator approval -> publish — the approval-based route to ARCHIVE a published document (no hard delete).
PERMANENT DELETION (this module) comes AFTER that: it removes what archiving keeps. It is never reachable from Archive, and never for a live document:
  * only a document that was never published (a pure draft) or one that is ARCHIVED can be deleted permanently — a published document must first go through
    the approval-based deletion request, so the two human approvals in front of removing live knowledge stay in place;
  * refused while a legal hold is set (kb_documents.legal_hold, migration 0013), while the configured retention period after archiving has not passed
    (SG_KB_PURGE_MIN_ARCHIVED_DAYS), while any change request that is still open references the document, while a feedback proposal for it awaits review, while a
    linked call recording is under legal hold, or while a version is being processed; and when the feature is switched off (SG_KB_PURGE_ENABLED=false);
  * the administrator must type the document id AND its exact title, and send the row_version the page was showing: a stale page, a different document or a
    concurrent change is refused (compare-and-set on kb_documents.row_version; on PostgreSQL that UPDATE also locks the row, so concurrent purges serialise).

WHAT IS REMOVED, in ONE transaction (all or nothing): live passages + their embeddings (`chunks`) and the live `documents` row, staged passages + embeddings
(`kb_version_chunks`), every version (`kb_versions`: the text), the document's items in closed change requests (`kb_change_items`: proposed text, AI suggestion,
diffs; the requests themselves stay, marked with the purged id), finished feedback proposals (`kb_proposals`), the legacy folder placement and the document row.
References inside change-request analyses (the "not relevant" list and processing-log events naming it) are removed, and a deletion request's description is
replaced by a neutral sentence (it quoted the title).

WHAT IS KEPT: the append-only audit trail (`kb_events`), which by design holds ids, counts and field names only (never text), plus ONE new event
`document_purged` (doc id, actor, time, counts). That event is the tombstone: disk ingest, the import wizard and document creation refuse the purged id, so a
file that still lies in the tenant's docs folder cannot silently bring the deleted knowledge back. Conversations that once quoted the document (chat
transcripts) are customer records and are not touched — see docs/M6_REFINEMENTS.md.

Idempotent: a repeated request for a document that is already gone answers `already_deleted` (from the tombstone) instead of failing."""

from datetime import timedelta

from sqlalchemy import Text, cast, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (CallRecording, Chunk, Document, KbChangeItem, KbChangeRequest, KbDocPlacement, KbDocument, KbEvent, KbProposal, KbVersion,
                           KbVersionChunk)
from app.knowledge import audit, store
from app.knowledge.errors import Conflict, Forbidden, Invalid, NotFound

TERMINAL = ("published", "rejected", "cancelled", "applied")           # a request in any other state may still change the document
OPEN_PROPOSALS = ("proposed", "approved")


def purged_ids(db: Session, tenant_id: str) -> set[str]:
    return set(db.execute(select(KbEvent.doc_id).where(KbEvent.tenant_id == tenant_id, KbEvent.action == "document_purged")).scalars())


def is_purged(db: Session, tenant_id: str, doc_id: str) -> bool:
    return db.execute(select(KbEvent.id).where(KbEvent.tenant_id == tenant_id, KbEvent.action == "document_purged", KbEvent.doc_id == doc_id).limit(1)).first() is not None


def _count(db, model, *conds) -> int:
    return int(db.execute(select(func.count()).select_from(model).where(*conds)).scalar_one())


def _blockers(db: Session, tenant_id: str, doc: KbDocument, s: Settings) -> list[str]:
    out: list[str] = []
    if not s.kb_purge_enabled:
        out.append("Permanent deletion is switched off on this server (SG_KB_PURGE_ENABLED=false).")
    if doc.legal_hold:
        out.append("The document is under legal hold" + (f" ({doc.legal_hold_reason})" if doc.legal_hold_reason else "") + ": release the hold first.")
    ever_published = _count(db, KbVersion, KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc.doc_id, KbVersion.published_at.isnot(None)) > 0
    if doc.status == "published":
        out.append("The document is live. Archive it first through a deletion request that an administrator approves; only then can it be deleted permanently.")
    elif ever_published and doc.status != "archived":
        out.append("This document was published before. Archive it first; only an archived document can be deleted permanently.")
    if doc.status == "archived" and s.kb_purge_min_archived_days > 0 and doc.archived_at is not None:
        at = doc.archived_at if doc.archived_at.tzinfo else doc.archived_at.replace(tzinfo=store.now().tzinfo)
        until = at + timedelta(days=s.kb_purge_min_archived_days)
        if store.now() < until:
            out.append(f"The retention period has not passed: archived documents are kept for {s.kb_purge_min_archived_days} day(s) (until {until.date().isoformat()}).")
    open_reqs = db.execute(select(KbChangeRequest.request_id).join(KbChangeItem, (KbChangeItem.tenant_id == KbChangeRequest.tenant_id)
                                                                   & (KbChangeItem.request_id == KbChangeRequest.request_id))
                           .where(KbChangeRequest.tenant_id == tenant_id, KbChangeItem.doc_id == doc.doc_id, KbChangeRequest.status.notin_(TERMINAL))).scalars().all()
    if open_reqs:
        out.append("Open change request(s) still reference it: " + ", ".join(sorted(set(open_reqs))[:5]) + ". Finish, reject or cancel them first.")
    props = db.execute(select(KbProposal).where(KbProposal.tenant_id == tenant_id, KbProposal.doc_id == doc.doc_id)).scalars().all()
    if any(p.status in OPEN_PROPOSALS for p in props):
        out.append("A feedback proposal for this document is still waiting for review.")
    calls = [p.call_id for p in props]
    if calls and db.execute(select(CallRecording.call_id).where(CallRecording.tenant_id == tenant_id, CallRecording.call_id.in_(calls),
                                                                CallRecording.legal_hold.is_(True)).limit(1)).first():
        out.append("A call recording linked to this document's feedback is under legal hold.")
    if _count(db, KbVersion, KbVersion.tenant_id == tenant_id, KbVersion.doc_id == doc.doc_id, KbVersion.status == "processing"):
        out.append("A version is being processed right now: wait for it to finish.")
    return out


def _removes(db: Session, t: str, d: str) -> dict:
    items = db.execute(select(KbChangeItem.request_id).where(KbChangeItem.tenant_id == t, KbChangeItem.doc_id == d)).scalars().all()
    return {"versions": _count(db, KbVersion, KbVersion.tenant_id == t, KbVersion.doc_id == d),
            "staged_chunks": _count(db, KbVersionChunk, KbVersionChunk.tenant_id == t, KbVersionChunk.doc_id == d),
            "live_chunks": _count(db, Chunk, Chunk.tenant_id == t, Chunk.doc_id == d),
            "change_items": len(items), "change_requests": len(set(items)),
            "proposals": _count(db, KbProposal, KbProposal.tenant_id == t, KbProposal.doc_id == d)}


def preview(db: Session, tenant_id: str, doc_id: str, s: Settings) -> dict:
    """What a permanent deletion would remove (counts only, no text) and what currently prevents it."""
    doc = store.get_doc(db, tenant_id, doc_id)
    t, d = tenant_id, doc_id
    return {"doc_id": d, "title": doc.title, "status": doc.status, "row_version": doc.row_version, "legal_hold": bool(doc.legal_hold),
            "legal_hold_reason": doc.legal_hold_reason, "archived_at": store._iso(doc.archived_at), "origin": doc.origin,
            "removes": _removes(db, t, d),
            "keeps": ["audit trail (ids, counts and actions only, no text)", "the change requests themselves (without this document's text)"],
            "blockers": _blockers(db, t, doc, s),
            "disk_file_note": "This document was imported from the tenant's docs folder; the file stays on disk (its id is blocked from re-import)." if doc.origin == "imported" else None}


def _scrub_analysis(an: dict | None, doc_id: str) -> dict | None:
    if not an:
        return an
    an = dict(an)
    sm = dict(an.get("summary") or {})
    if sm.get("not_relevant"):
        sm["not_relevant"] = [n for n in sm["not_relevant"] if (n or {}).get("doc_id") != doc_id]
        an["summary"] = sm
    tr = an.get("trace")
    if isinstance(tr, dict) and isinstance(tr.get("events"), list):
        an["trace"] = {**tr, "events": [e for e in tr["events"] if (e or {}).get("doc") != doc_id]}
    elif isinstance(tr, list):
        an["trace"] = [e for e in tr if (e or {}).get("doc") != doc_id]
    an["purged_docs"] = sorted(set(an.get("purged_docs") or []) | {doc_id})
    return an


def purge_document(db: Session, tenant_id: str, actor: str, s: Settings, doc_id: str, *, confirm_doc_id: str, confirm_title: str, expected_row_version: int) -> dict:
    doc = db.get(KbDocument, (tenant_id, doc_id))
    if doc is None:
        if is_purged(db, tenant_id, doc_id):
            return {"doc_id": doc_id, "outcome": "already_deleted"}
        raise NotFound("document not found")
    if confirm_doc_id != doc_id:
        raise Invalid("the confirmation does not match this document's id: type the id exactly as shown")
    if " ".join((confirm_title or "").split()) != " ".join(doc.title.split()):
        raise Invalid("the confirmation does not match this document's title: type the title exactly as shown")
    if doc.row_version != expected_row_version:
        raise Conflict("the document changed since this page was loaded: reload it and check again before deleting")
    problems = _blockers(db, tenant_id, doc, s)
    if problems:
        if not s.kb_purge_enabled:
            raise Forbidden(problems[0])
        raise Conflict(problems[0])

    # 1. Claim the row (compare-and-set). On PostgreSQL this UPDATE takes the row lock: a concurrent purge, publish, restore or new version waits for this
    #    transaction and then finds the row gone (or the version changed) and is refused.
    origin = doc.origin
    store.bump(db, doc, actor, expected=expected_row_version)
    t, d = tenant_id, doc_id
    counts = _removes(db, t, d)
    # 2. Remove everything that holds the document's text or vectors. Bulk statements on purpose: the ORM guard that forbids deleting versions protects every
    #    other code path; this is the one deliberate, audited exception.
    db.execute(delete(Chunk).where(Chunk.tenant_id == t, Chunk.doc_id == d))
    db.execute(delete(Document).where(Document.tenant_id == t, Document.doc_id == d))
    db.execute(delete(KbVersionChunk).where(KbVersionChunk.tenant_id == t, KbVersionChunk.doc_id == d))
    req_ids = sorted(set(db.execute(select(KbChangeItem.request_id).where(KbChangeItem.tenant_id == t, KbChangeItem.doc_id == d)).scalars()))
    db.execute(delete(KbChangeItem).where(KbChangeItem.tenant_id == t, KbChangeItem.doc_id == d))
    db.execute(delete(KbProposal).where(KbProposal.tenant_id == t, KbProposal.doc_id == d))
    db.execute(delete(KbVersion).where(KbVersion.tenant_id == t, KbVersion.doc_id == d))
    db.execute(delete(KbDocPlacement).where(KbDocPlacement.tenant_id == t, KbDocPlacement.doc_id == d))
    # 3. References in requests (closed ones, and AI analyses that merely examined it): no text of the deleted document stays behind.
    # Candidates only (indexed id list, the reference column, or the id appearing in the analysis JSON); each is then scrubbed precisely.
    cand = select(KbChangeRequest).where(KbChangeRequest.tenant_id == t, or_(KbChangeRequest.request_id.in_(req_ids or [""]), KbChangeRequest.reference_doc_id == d,
                                                                             cast(KbChangeRequest.analysis, Text).like(f'%"{d}"%')))
    for r in db.execute(cand).scalars().all():
        an = r.analysis or {}
        vals = {"analysis": _scrub_analysis(an, d)}
        if an.get("kind") == "deletion" and an.get("doc_id") == d:
            vals["description"] = f"Delete document {d} (since deleted permanently)"
        if r.reference_doc_id == d:
            vals["reference_doc_id"] = None
        db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == t, KbChangeRequest.request_id == r.request_id).values(**vals)
                   .execution_options(synchronize_session=False))
    db.execute(delete(KbDocument).where(KbDocument.tenant_id == t, KbDocument.doc_id == d))
    # 4. The tombstone: who, when, how much — never the title or the text.
    audit.record(db, t, actor, "document_purged", "document", d, doc_id=d, count=counts["versions"], chunks=counts["live_chunks"] + counts["staged_chunks"],
                 items=counts["change_items"], documents=len(req_ids), origin=origin)
    db.flush()
    return {"doc_id": d, "outcome": "deleted", "removed": counts, "requests_touched": len(req_ids)}


def set_legal_hold(db: Session, tenant_id: str, actor: str, doc_id: str, *, hold: bool, reason: str | None, expected_row_version: int) -> dict:
    doc = store.get_doc(db, tenant_id, doc_id)
    reason = " ".join((reason or "").split())[:300] or None
    if hold and not reason:
        raise Invalid("say why the document is placed under legal hold")
    if bool(doc.legal_hold) == hold:
        return store.doc_view(db, doc) | {"unchanged": True}
    store.bump(db, doc, actor, expected=expected_row_version, legal_hold=hold, legal_hold_reason=reason if hold else None,
               legal_hold_by=actor[:64] if hold else None, legal_hold_at=store.now() if hold else None)
    audit.record(db, tenant_id, actor, "document_legal_hold_set" if hold else "document_legal_hold_released", "document", doc_id, doc_id=doc_id)
    return store.doc_view(db, store.get_doc(db, tenant_id, doc_id))
