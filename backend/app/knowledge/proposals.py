"""Intervention feedback -> knowledge proposals.

An approved feedback record (M4.1/M5 review status `approved_for_update`) is PERMISSION TO START an update, nothing more. An administrator turns it into a
DRAFT version (a new document, or a new version of an existing one) written by that administrator; the version then goes through the ordinary
process -> proposal review -> publish path. Nothing in this module touches the live index, and the feedback and review rows are only ever read."""

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import InterventionFeedback, KbProposal
from app.knowledge import audit, store
from app.knowledge import validation as v
from app.knowledge.errors import Conflict, Forbidden, Invalid, NotFound


def _iso(dt):
    return store._iso(dt)


def proposal_view(p: KbProposal) -> dict:
    return {"call_id": p.call_id, "doc_id": p.doc_id, "version": p.version_no, "status": p.status, "proposed_by": p.proposed_by, "proposed_at": _iso(p.proposed_at),
            "reviewed_by": p.reviewed_by, "reviewed_at": _iso(p.reviewed_at), "review_note": p.review_note, "published_by": p.published_by, "published_at": _iso(p.published_at)}


def list_approved_feedback(db: Session, tenant_id: str, *, only_unprocessed: bool = False, limit: int = 50, offset: int = 0) -> dict:
    """Approved feedback of THIS tenant with the proposal (if any) made from it. Read-only."""
    on = and_(KbProposal.tenant_id == InterventionFeedback.tenant_id, KbProposal.call_id == InterventionFeedback.call_id)
    conds = [InterventionFeedback.tenant_id == tenant_id, InterventionFeedback.review_status == "approved_for_update"]
    if only_unprocessed:
        conds.append(KbProposal.call_id.is_(None))
    total = db.execute(select(func.count()).select_from(InterventionFeedback).outerjoin(KbProposal, on).where(*conds)).scalar_one()
    rows = db.execute(select(InterventionFeedback, KbProposal).outerjoin(KbProposal, on).where(*conds)
                      .order_by(InterventionFeedback.reviewed_at.desc(), InterventionFeedback.call_id).limit(limit).offset(offset)).all()
    items = [{"call_id": f.call_id, "category": f.category, "what_was_wrong": f.what_was_wrong, "suggested_correction": f.suggested_correction,
              "written_by": f.staff_subject, "submitted_at": _iso(f.submitted_at), "reviewed_by": f.reviewed_by, "reviewed_at": _iso(f.reviewed_at), "review_note": f.review_note,
              "proposal": None if p is None else proposal_view(p)} for f, p in rows]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


def create_proposal(db: Session, tenant_id: str, actor: str, s: Settings, call_id: str, *, content: str, change_note=None, doc_id: str | None = None,
                    new_document: dict | None = None) -> dict:
    """Create the draft for one approved feedback record. Exactly one of `doc_id` (new version of an existing document) or `new_document`
    ({doc_id, title, category, description?, tags?}) must be given. The content is written by the administrator; the feedback text is only their reference."""
    if (doc_id is None) == (new_document is None):
        raise Invalid("give either doc_id (a new version of an existing document) or new_document")
    fb = db.get(InterventionFeedback, (tenant_id, call_id))
    if fb is None:
        raise NotFound("feedback not found")
    if fb.review_status != "approved_for_update":
        raise Conflict(f"this feedback is {fb.review_status or 'not submitted'}: only feedback approved for a knowledge update can start a proposal")
    if db.get(KbProposal, (tenant_id, call_id)) is not None:
        raise Conflict("a proposal already exists for this feedback")
    note = v.change_note(change_note, required=False, default=f"Proposed from intervention feedback on call {call_id}")
    if new_document is not None:
        nd = dict(new_document)
        doc, ver, _ = store.create_document(db, tenant_id, actor, s, doc_id=nd.get("doc_id"), title=nd.get("title"), category=nd.get("category"), content=content,
                                            description=nd.get("description"), tags=nd.get("tags"), change_note=note)
    else:
        ver = store.create_version(db, tenant_id, actor, s, doc_id, content=content, change_note=note)
    prop = KbProposal(tenant_id=tenant_id, call_id=call_id, doc_id=ver.doc_id, version_no=ver.version_no, status="proposed", proposed_by=actor[:64])
    db.add(prop)
    try:
        db.flush()                                       # a concurrent second proposal for the same feedback loses on the primary key
    except IntegrityError:
        raise Conflict("a proposal already exists for this feedback") from None
    audit.record(db, tenant_id, actor, "proposal_created", "proposal", call_id, doc_id=ver.doc_id, version_no=ver.version_no, call_id=call_id, status="proposed")
    return {"proposal": proposal_view(prop), "version": store.version_view(ver)}


def review_proposal(db: Session, tenant_id: str, actor: str, s: Settings, call_id: str, decision: str, note: str | None = None) -> dict:
    if decision not in ("approve", "reject"):
        raise Invalid("decision must be 'approve' or 'reject'")
    prop = db.get(KbProposal, (tenant_id, call_id))
    if prop is None:
        raise NotFound("proposal not found")
    if prop.status != "proposed":
        raise Conflict(f"this proposal is already {prop.status}")
    if s.kb_require_separate_reviewer and prop.proposed_by == actor:
        raise Forbidden("the administrator who proposed this change cannot review it: another administrator must")
    to = "approved" if decision == "approve" else "rejected"
    res = db.execute(update(KbProposal).where(KbProposal.tenant_id == tenant_id, KbProposal.call_id == call_id, KbProposal.status == "proposed")
                     .values(status=to, reviewed_by=actor[:64], reviewed_at=store.now(), review_note=(note or "").strip()[:500] or None)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise Conflict("this proposal was just reviewed by someone else")
    audit.record(db, tenant_id, actor, "proposal_approved" if to == "approved" else "proposal_rejected", "proposal", call_id, doc_id=prop.doc_id,
                 version_no=prop.version_no, call_id=call_id, decision=decision, from_status="proposed", to_status=to)
    db.expire(prop)
    return proposal_view(db.get(KbProposal, (tenant_id, call_id)))


def list_proposals(db: Session, tenant_id: str, *, status: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    conds = [KbProposal.tenant_id == tenant_id]
    if status:
        conds.append(KbProposal.status == status)
    total = db.execute(select(func.count()).select_from(KbProposal).where(*conds)).scalar_one()
    rows = db.execute(select(KbProposal).where(*conds).order_by(KbProposal.proposed_at.desc(), KbProposal.call_id).limit(limit).offset(offset)).scalars().all()
    return {"items": [proposal_view(p) for p in rows], "total": int(total), "limit": limit, "offset": offset}
