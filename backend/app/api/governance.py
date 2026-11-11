"""Knowledge-governance analytics (final M6): how change requests move from a staff request to publication, and who contributes.

Every figure is an aggregate over kb_change_requests / kb_change_items of ONE tenant (every query filters on tenant_id), restricted to requests CREATED in the
period. Nothing here reads document text, request descriptions, reasons or customer data: only statuses, decisions, counts and timestamps (staff usernames appear
in the contributions table: they are internal account names, already shown everywhere in the console). Portable SQL + Python (SQLite and PostgreSQL).

Each metric is listed in DEFINITIONS with how it is computed and from which column; a metric with no data is None ("n/a"), never 0 by guesswork."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KbChangeItem, KbChangeRequest

MAX_ROWS = 50_000
APPROVED_OR_LATER = {"approved", "processing", "publishing", "published", "failed", "applied"}
DEFINITIONS = {
    "requests": "Change requests created in the period (kb_change_requests.created_at), drafts included.",
    "ai_assisted": "Requests with source = ai_assisted (the AI analysed the knowledge base).",
    "manual": "Requests a person wrote by hand (source = manual), deletion requests excluded.",
    "deletions": "Document-deletion requests (analysis.kind = deletion).",
    "documents_examined": "Sum over AI requests of analysis.summary.discovered: documents the analysis looked at.",
    "ai_proposals": "AI items with a proposed change (origin = ai, classification definite or potential).",
    "documents_affected": "Distinct documents with an accepted, edited or manual change in a request.",
    "staff_accepted": "AI proposals the staff member accepted as written (decision = accepted).",
    "staff_edited": "AI proposals the staff member edited before submitting (decision = manual).",
    "staff_discarded": "AI proposals the staff member discarded (decision = discarded).",
    "staff_acceptance_rate": "(accepted + edited) / AI proposals with a decision.",
    "manual_edit_rate": "edited / (accepted + edited): how often staff changed the AI's wording.",
    "submitted": "Requests sent to administrators (submitted_at set).",
    "admin_approved": "Submitted requests an administrator approved (status approved or later).",
    "admin_rejected": "Submitted requests an administrator rejected (status = rejected).",
    "admin_approval_rate": "approved / (approved + rejected).",
    "published": "Requests whose change went live (status = published).",
    "published_ai_unmodified": "Published AI items accepted as written and not edited by an administrator.",
    "published_human_modified": "Published items edited by staff or an administrator, or written by hand.",
    "median_hours_to_publication": "Median of published_at − created_at over published requests, in hours.",
    "median_hours_to_review": "Median of reviewed_at − submitted_at over reviewed requests, in hours.",
    "failed": "Requests whose release failed (status = failed) or whose analysis ended with an error.",
    "incomplete": "Requests still open at the end of the period (draft, analysing, awaiting decisions or clarification, returned).",
    "funnel": "Requests → with an AI proposal or manual change → submitted → approved by an administrator → published.",
}
OPEN = {"draft", "analysing", "awaiting_clarification", "awaiting_decisions", "needs_revision"}


def _aware(d: datetime | None) -> datetime | None:
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=timezone.utc))


def _median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    return round(xs[len(xs) // 2], 2) if xs else None


def _rate(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def compute(db: Session, tenant_id: str, *, days: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    reqs = [r for r in db.execute(select(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id).limit(MAX_ROWS)).scalars()
            if (_aware(r.created_at) or now) >= since]
    ids = {r.request_id for r in reqs}
    items_by: dict[str, list[KbChangeItem]] = defaultdict(list)
    if ids:
        for it in db.execute(select(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id.in_(ids)).limit(MAX_ROWS)).scalars():
            items_by[it.request_id].append(it)

    kind = lambda r: "deletion" if (r.analysis or {}).get("kind") == "deletion" else ("ai_assisted" if r.source == "ai_assisted" else "manual")  # noqa: E731
    items = [i for r in reqs for i in items_by[r.request_id]]
    ai_items = [i for i in items if i.origin == "ai" and i.classification in ("definite", "potential")]
    acc = sum(i.decision == "accepted" for i in ai_items)
    edt = sum(i.decision == "manual" for i in ai_items)
    dis = sum(i.decision == "discarded" for i in ai_items)
    changed = lambda i: i.decision in ("accepted", "manual") or i.origin == "manual"  # noqa: E731
    submitted = [r for r in reqs if r.submitted_at is not None]
    approved = [r for r in submitted if r.status in APPROVED_OR_LATER]
    rejected = [r for r in submitted if r.status == "rejected"]
    published = [r for r in reqs if r.status == "published"]
    pub_items = [i for r in published for i in items_by[r.request_id] if i.publish_status == "published"]
    with_change = [r for r in reqs if any(changed(i) or (i.origin == "ai" and i.classification in ("definite", "potential")) for i in items_by[r.request_id])
                   or kind(r) == "deletion"]
    analysis_failed = [r for r in reqs if (r.analysis or {}).get("error")]

    gov = {
        "requests": len(reqs),
        "by_kind": dict(sorted(Counter(kind(r) for r in reqs).items())),
        "by_status": dict(sorted(Counter(r.status for r in reqs).items())),
        "documents_examined": sum(int(((r.analysis or {}).get("summary") or {}).get("discovered") or 0) for r in reqs if r.source == "ai_assisted"),
        "ai_proposals": len(ai_items),
        "documents_affected": len({i.doc_id for i in items if changed(i)}),
        "staff": {"accepted": acc, "edited": edt, "discarded": dis, "pending": sum(i.decision is None for i in ai_items),
                  "acceptance_rate": _rate(acc + edt, acc + edt + dis), "manual_edit_rate": _rate(edt, acc + edt)},
        "admin": {"submitted": len(submitted), "approved": len(approved), "rejected": len(rejected), "approval_rate": _rate(len(approved), len(approved) + len(rejected)),
                  "median_hours_to_review": _median([((_aware(r.reviewed_at) - _aware(r.submitted_at)).total_seconds() / 3600) for r in submitted if r.reviewed_at])},
        "published": len(published),
        "published_items": {"ai_unmodified": sum(i.origin == "ai" and i.decision == "accepted" and not i.admin_edited for i in pub_items),
                            "human_modified": sum(i.decision == "manual" or i.admin_edited or i.origin == "manual" for i in pub_items)},
        "median_hours_to_publication": _median([((_aware(r.published_at) - _aware(r.created_at)).total_seconds() / 3600) for r in published if r.published_at]),
        "failed": sum(r.status == "failed" for r in reqs) + len([r for r in analysis_failed if r.status != "failed"]),
        "incomplete": sum(r.status in OPEN for r in reqs),
        "funnel": [{"step": "Requested", "count": len(reqs)}, {"step": "Proposal ready", "count": len(with_change)},
                   {"step": "Submitted by staff", "count": len(submitted)}, {"step": "Approved by admin", "count": len(approved)},
                   {"step": "Published", "count": len(published)}],
    }
    per_day: Counter[str] = Counter((_aware(r.created_at) or now).date().isoformat() for r in reqs)
    gov["per_day"] = [{"date": (since + timedelta(days=i + 1)).date().isoformat(), "turns": per_day.get((since + timedelta(days=i + 1)).date().isoformat(), 0)} for i in range(days)]

    staff: dict[str, dict] = {}
    for r in reqs:
        s = staff.setdefault(r.created_by, {"staff": r.created_by, "requests": 0, "accepted": 0, "edited": 0, "submitted": 0, "approved": 0, "rejected": 0, "published": 0, "_review": []})
        s["requests"] += 1
        s["accepted"] += sum(i.decision == "accepted" for i in items_by[r.request_id] if i.origin == "ai")
        s["edited"] += sum(i.decision == "manual" for i in items_by[r.request_id] if i.origin == "ai")
        if r.submitted_at is not None:
            s["submitted"] += 1
            s["approved"] += r.status in APPROVED_OR_LATER
            s["rejected"] += r.status == "rejected"
            if r.reviewed_at:
                s["_review"].append((_aware(r.reviewed_at) - _aware(r.submitted_at)).total_seconds() / 3600)
        s["published"] += r.status == "published"
    rows = []
    for s in staff.values():
        rev = s.pop("_review")
        s["approval_rate"] = _rate(s["approved"], s["approved"] + s["rejected"])
        s["median_hours_to_review"] = _median(rev)
        rows.append(s)
    gov["contributions"] = sorted(rows, key=lambda x: (-x["requests"], x["staff"]))
    return {"tenant_id": tenant_id, "days": days, "generated_at": now.isoformat(), "knowledge": gov, "definitions": DEFINITIONS}
