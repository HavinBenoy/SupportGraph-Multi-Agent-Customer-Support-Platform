"""Post-publication verification: after a change is published, search the LIVE knowledge base again for references that still say what the change replaced.

The analysis looks for affected passages BEFORE publishing, with the knowledge it has then. A second, independent look afterwards is the cheapest safeguard against an incomplete
update: the published base is scanned for the old value (and, for a change that gave none, for the value the documents stated), and anything that still states it is reported.
It does not change anything: it writes a report (`release.verification`) for people to act on. A reference the reviewer chose to leave alone is reported as such, not as a miss;
historical records are counted but never listed as outdated; a line about a different subject (the same person teaching another course) is not a finding.

Inputs are the stored, validated spec of the request and the values learned during the analysis, never text the author typed afterwards. The scan is the same deterministic
code as the analysis (scan.py), so the two cannot disagree about what a line says."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import Settings
from app.db.models import KbChangeItem, KbChangeRequest
from app.db.session import session_scope
from app.knowledge import audit, store
from app.knowledge.impact import discover as disc
from app.knowledge.impact.aliases import build_aliases
from app.knowledge.impact.scan import scan_corpus
from app.knowledge.impact.scope import is_historical
from app.knowledge.impact.spec import ChangeSpec

log = logging.getLogger(__name__)
MAX_LISTED = 30


def _facts_to_check(spec: ChangeSpec, learned: list[dict]) -> ChangeSpec:
    """A change that named an old value is searched for that value. One that did not is searched for each value the analysis learned from the documents (or, if none, for any
    other value the subject's attribute states)."""
    facts = []
    for i, f in enumerate(spec.actionable()):
        if f.old_value:
            facts.append(f)
            continue
        vals = [e["value"] for e in learned if e.get("fact") == i]
        facts += [f.model_copy(update={"old_value": v}) for v in vals] or [f]
    return spec.model_copy(update={"facts": facts})


def compute(db, tenant_id: str, spec_dict: dict, learned: list[dict], left_alone: set[str]) -> dict:
    spec = ChangeSpec.model_validate(spec_dict)
    spec = _facts_to_check(spec, learned)
    corpus = disc.published_corpus(db, tenant_id)
    spec.alias_pairs = build_aliases(spec, disc._tenant_groups(tenant_id), [c.content for c in corpus])
    spec.key_terms = disc.key_terms(spec.entity_terms, corpus)
    report = scan_corpus(corpus, spec, max_rounds=1)
    by_doc = {c.doc_id: c for c in corpus}
    remaining, historical, confirmed, other_subject = [], 0, 0, 0
    for doc_id, hits in report.hits.items():
        c = by_doc[doc_id]
        for h in hits:
            if h.how == "already":
                confirmed += 1
                continue
            if h.how == "role":
                continue
            if h.subject == "none":
                other_subject += 1
                continue
            if is_historical(h.passage.heading, h.passage.text):
                historical += 1
                continue
            kind = "outdated" if (h.how in ("old", "old_core", "slot") and h.subject == "line" and not h.conflict) else "possible"
            remaining.append({"doc_id": doc_id, "title": c.title, "version": c.version, "heading": h.passage.heading, "text": h.passage.text[:300], "kind": kind,
                              "evidence": h.explain()[:300], "left_by_reviewer": doc_id in left_alone})
    remaining.sort(key=lambda r: (r["left_by_reviewer"], r["kind"] != "outdated", r["doc_id"], r["text"]))
    return {"checked_at": store._iso(datetime.now(timezone.utc)), "documents_scanned": len(corpus), "clean": not remaining, "unreviewed": len([r for r in remaining if not r["left_by_reviewer"]]),
            "remaining": remaining[:MAX_LISTED], "remaining_total": len(remaining), "historical_skipped": historical, "confirmed_new": confirmed, "other_subject": other_subject,
            "values_searched": sorted({f.old_value for f in spec.actionable() if f.old_value})[:6]}


def verify_request(tenant_id: str, request_id: str, s: Settings, actor: str = "system") -> dict | None:
    """Run the check for a published request and store the report in `release.verification`. Never raises: a failed check must not undo or hide a successful publication."""
    try:
        with session_scope() as db:
            r = db.get(KbChangeRequest, (tenant_id, request_id))
            if r is None or r.status != "published" or not (r.analysis or {}).get("spec"):
                return None
            an = r.analysis or {}
            left = {i.doc_id for i in db.execute(select(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id)).scalars()
                    if i.decision == "discarded" or i.admin_decision == "excluded"}
            out = compute(db, tenant_id, an["spec"], (an.get("summary") or {}).get("learned") or [], left)
            rel = dict(r.release or {})
            rel["verification"] = out
            r.release = rel
            audit.record(db, tenant_id, actor, "change_verification_completed", "request", request_id, request_id=request_id, remaining=out["remaining_total"],
                         scanned=out["documents_scanned"], clean=out["clean"])
            return out
    except Exception:                                                              # noqa: BLE001
        log.exception("post-publication verification of %s failed", request_id)
        return None
