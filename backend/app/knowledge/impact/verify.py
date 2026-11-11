"""Post-publication verification: after a change is published, search the LIVE knowledge base again for references that still say what the change replaced.

The analysis looks for affected passages BEFORE publishing, with the knowledge it has then. A second, independent look afterwards is the cheapest safeguard against an incomplete
update: the published base is scanned for the old value (and, for a change that gave none, for the value the documents stated), and anything that still states it is reported.
It does not change anything: it writes a report (`release.verification`) for people to act on. A reference the reviewer chose to leave alone is reported as such, not as a miss;
historical records are counted but never listed as outdated; a line about a different subject (the same person teaching another course) is not a finding.

Inputs are the stored, validated spec of the request and the values learned during the analysis, never text the author typed afterwards. The scan is the same deterministic
code as the analysis (scan.py), so the two cannot disagree about what a line says."""

import logging
import re
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


def _canon(line: str) -> str:
    return " ".join(re.sub(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+", "", line).lower().split())


def compute_statements(corpus, edits_by_doc: dict[str, list[dict]], left_alone: set[str]) -> dict:
    """Residual check for a change that is not a value replacement (add / remove / rewrite): there is no old value to search for, so the check uses the published edits themselves.
      * applied     the lines the edit added are in the live document (it is the version that was published);
      * remaining   a statement that was REMOVED or REWRITTEN (a line of the edit's original text that is no longer in its replacement) still appears, word for word, in a live document
                    (another document, or the same one): the same outdated statement may exist elsewhere. Historical lines are counted, not listed.
    It cannot prove that a rewritten statement is semantically gone everywhere, and says so (`verifiable` = False for the part it cannot check)."""
    by_doc = {c.doc_id: c for c in corpus}
    removed: dict[str, tuple[str, str]] = {}
    applied = missing = 0
    for doc_id, edits in edits_by_doc.items():
        live = by_doc.get(doc_id)
        for e in edits:
            old_lines = [x for x in str(e.get("original", "")).split("\n")]
            new_lines = [x for x in str(e.get("replacement", "")).split("\n")]
            new_c = {_canon(x) for x in new_lines}
            added = [x for x in new_lines if _canon(x) not in {_canon(o) for o in old_lines} and len(_canon(x)) >= 6]
            if added and live is not None:
                if all(x.strip() in live.content for x in added):
                    applied += 1
                else:
                    missing += 1
            for x in old_lines:
                c = _canon(x)
                if len(c) >= 14 and c not in new_c and not x.lstrip().startswith("#") and not x.strip().startswith("|---"):
                    removed.setdefault(c, (x.strip(), doc_id))
    remaining, historical = [], 0
    for c in corpus:
        for pos, raw in enumerate(c.content.split("\n")):
            key = _canon(raw)
            if key not in removed:
                continue
            if is_historical("", raw, None):
                historical += 1
                continue
            remaining.append({"doc_id": c.doc_id, "title": c.title, "version": c.version, "heading": "", "text": raw.strip()[:300], "kind": "possible",
                              "evidence": "The same statement that this change removed or rewrote in another place still appears here, word for word.",
                              "left_by_reviewer": c.doc_id in left_alone})
    remaining.sort(key=lambda r: (r["left_by_reviewer"], r["doc_id"], r["text"]))
    return {"checked_at": store._iso(datetime.now(timezone.utc)), "documents_scanned": len(corpus), "mode": "statements", "clean": not remaining and not missing,
            "verifiable": False, "unreviewed": len([r for r in remaining if not r["left_by_reviewer"]]), "remaining": remaining[:MAX_LISTED], "remaining_total": len(remaining),
            "historical_skipped": historical, "confirmed_new": applied, "other_subject": 0, "values_searched": [], "edits_missing": missing,
            "note": "This change is not a value replacement, so there is no old value to search for. The check confirms that the new text is live and looks for the removed or rewritten "
                    "statements word for word elsewhere; it cannot prove that every related statement was updated."}


def _merge_reports(parts: list[dict]) -> dict:
    """One follow-up check per intent, shown as one: clean only when every intent is clean."""
    out = dict(parts[0])
    out["remaining"] = [r for p in parts for r in p.get("remaining", [])][:MAX_LISTED]
    for k in ("remaining_total", "unreviewed", "historical_skipped", "confirmed_new", "other_subject", "edits_missing"):
        out[k] = sum(int(p.get(k) or 0) for p in parts)
    out["clean"] = all(p.get("clean") for p in parts)
    out["values_searched"] = [v for p in parts for v in p.get("values_searched", [])]
    out["mode"] = "values" if any(p.get("mode", "values") == "values" for p in parts) else "statements"
    out["intents"] = len(parts)
    notes = [p["note"] for p in parts if p.get("note")]
    if notes:
        out["note"] = notes[0]
    return out


def compute(db, tenant_id: str, spec_dict: dict, learned: list[dict], left_alone: set[str], edits_by_doc: dict[str, list[dict]] | None = None) -> dict:
    spec = ChangeSpec.model_validate(spec_dict)
    if not spec.actionable():
        return compute_statements(disc.published_corpus(db, tenant_id), edits_by_doc or {}, left_alone)
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
            if is_historical(h.passage.heading, h.passage.text, None, h.passage.matches, h.passage.start):
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
            edits: dict[str, list[dict]] = {}
            for i in db.execute(select(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id)).scalars():
                if i.decision in ("accepted", "manual") and i.admin_decision != "excluded" and i.final_edits:
                    edits[i.doc_id] = list(i.final_edits)
            specs = an.get("specs") or [an["spec"]]
            parts = [compute(db, tenant_id, sp, (an.get("summary") or {}).get("learned") or [], left, edits) for sp in specs]
            out = parts[0] if len(parts) == 1 else _merge_reports(parts)
            rel = dict(r.release or {})
            rel["verification"] = out
            r.release = rel
            audit.record(db, tenant_id, actor, "change_verification_completed", "request", request_id, request_id=request_id, remaining=out["remaining_total"],
                         scanned=out["documents_scanned"], clean=out["clean"])
            return out
    except Exception:                                                              # noqa: BLE001
        log.exception("post-publication verification of %s failed", request_id)
        return None
