"""Append-only audit trail of knowledge operations (kb_events). Only whitelisted scalar keys are stored: ids, counts, checksums prefixes, field NAMES, status
words. Document text, feedback text and customer data can never reach it, whatever a caller passes."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KbEvent

ACTIONS = (
    "document_created", "metadata_updated", "version_created", "version_restored", "ingestion_started", "ingestion_completed", "ingestion_failed", "ingestion_superseded",
    "version_published", "version_unpublished", "document_archived", "document_unarchived", "proposal_created", "proposal_approved", "proposal_rejected",
    "disk_import", "document_deletion_requested", "document_deletion_published", "folder_created", "folder_renamed", "folder_moved", "folder_deleted", "document_moved", "change_request_created", "change_draft_saved", "change_request_submitted",
    "change_request_cancelled", "change_request_approved", "change_request_rejected", "change_request_applied",
    "change_analysis_started", "change_analysis_completed", "change_analysis_failed", "change_clarification_requested", "change_clarification_answered",
    "change_suggestions_generated", "change_decision_recorded", "change_request_review_started", "change_request_returned", "change_item_edited", "change_item_reviewed",
    "change_release_started", "change_item_processed", "change_release_failed", "change_release_published", "change_release_retried", "change_verification_completed",
)
SAFE_KEYS = {"fields", "category", "status", "from_status", "to_status", "chunks", "model", "from_version", "to_version", "restored_from", "bytes", "checksum",
             "call_id", "error", "decision", "unchanged", "origin", "count", "imported", "skipped", "failed", "tags", "stale_takeover", "attempt", "current_attempt",
             "folder_id", "parent_id", "from_folder", "to_folder", "name", "request_id", "items", "added", "deleted", "base_version", "result_version",
             "classification", "confidence", "candidates", "definite", "potential", "related", "unsupported", "historical", "questions", "documents", "retry", "stage",
             "workflow", "admin_edited", "remaining", "scanned", "clean"}
MAX_STR = 200


def record(db: Session, tenant_id: str, actor: str, action: str, resource_type: str, resource_id: str, *, doc_id: str | None = None,
           version_no: int | None = None, **summary) -> None:
    assert action in ACTIONS, action
    safe = {}
    for k, v in summary.items():
        if k not in SAFE_KEYS:
            continue
        if isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        if isinstance(v, str):
            v = v[:MAX_STR]
        if isinstance(v, (str, int, float, bool)):
            safe[k] = v
    db.add(KbEvent(tenant_id=tenant_id, actor=actor[:64], action=action, resource_type=resource_type, resource_id=resource_id[:160], doc_id=doc_id,
                   version_no=version_no, summary=safe))


def _iso(dt):
    if dt is None:
        return None
    return (dt.replace(tzinfo=None).isoformat() + "Z") if dt.tzinfo is None else dt.isoformat()


def list_events(db: Session, tenant_id: str, *, doc_id: str | None = None, action: str | None = None, actor: str | None = None,
                limit: int = 50, before_id: int | None = None) -> dict:
    q = select(KbEvent).where(KbEvent.tenant_id == tenant_id)
    if doc_id:
        q = q.where(KbEvent.doc_id == doc_id)
    if action:
        q = q.where(KbEvent.action == action)
    if actor:
        q = q.where(KbEvent.actor == actor)
    if before_id:
        q = q.where(KbEvent.id < before_id)
    rows = db.execute(q.order_by(KbEvent.id.desc()).limit(limit + 1)).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [{"id": r.id, "at": _iso(r.created_at), "actor": r.actor, "action": r.action, "resource_type": r.resource_type, "resource_id": r.resource_id,
              "doc_id": r.doc_id, "version": r.version_no, "summary": r.summary} for r in rows]
    return {"items": items, "next_before_id": rows[-1].id if (more and rows) else None}
