"""The impact-analysis job (Part 2). It never publishes and never edits a document: it only WRITES SUGGESTIONS (kb_change_items with origin='ai') that staff must
decide on and an administrator must approve.

Pipeline: parse the change -> (ask for clarification when the description cannot pick between equally good targets) -> discover candidate documents -> retrieve the
passages that hold the old value -> let the model judge each passage -> validate every proposal with deterministic guards -> persist suggestions.

Concurrency and failure: the job runs in a background thread (or inline in tests) under a fenced attempt number. Every write checks `status='analysing' AND
analysis_attempt=<mine>` in the UPDATE itself, so a cancelled, retried or taken-over run can never overwrite newer work: it notices and stops quietly. The model is
called OUTSIDE any database transaction. Any error returns the request to `draft` with a short error (never document text) so staff can retry."""

import hashlib
import logging
import threading
from dataclasses import asdict, dataclass, field

from sqlalchemy import delete, select, update

from app.config import Settings
from app.db.models import KbChangeItem, KbChangeRequest
from app.db.session import session_scope
from app.knowledge import audit, diffing, store
from app.knowledge import validation as v
from app.knowledge.impact import discover as disc
from app.knowledge.impact.discover import Candidate
from app.knowledge.impact.aliases import expand
from app.knowledge.impact.evaluate import (DocResult, Evaluation, ImpactModel, apply_edits, context_of, gather, validate)
from app.knowledge.impact.spec import ChangeSpec, Question, are_siblings, entity_terms_from, spec_from_edit

log = logging.getLogger(__name__)
ALL = "All of them"
RESTATE = "restate"


class Superseded(Exception):
    """This run no longer owns the request (cancelled, retried or taken over): stop without writing anything."""


@dataclass
class Input:
    description: str
    answers: dict
    reference: Candidate | None
    primary: list[dict] = field(default_factory=list)          # workflow A: the staff member's own manual edits [{doc_id, title, path, diff_body}]
    primary_ids: set = field(default_factory=set)


def _qid(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha1('|'.join(sorted(parts)).encode()).hexdigest()[:8]}"


def _fenced(tenant_id: str, request_id: str, attempt: int, **values) -> bool:
    with session_scope() as db:
        n = db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id,
                                                     KbChangeRequest.status == "analysing", KbChangeRequest.analysis_attempt == attempt)
                       .values(**values).execution_options(synchronize_session=False)).rowcount
    return n == 1


def _load(tenant_id: str, request_id: str, attempt: int) -> tuple[Input, dict]:
    with session_scope() as db:
        r = db.get(KbChangeRequest, (tenant_id, request_id))
        if r is None or r.status != "analysing" or r.analysis_attempt != attempt:
            raise Superseded()
        an = dict(r.analysis or {})
        corpus = {c.doc_id: c for c in disc.published_corpus(db, tenant_id)}
        inp = Input(r.description or "", dict(an.get("answers") or {}), corpus.get(r.reference_doc_id) if r.reference_doc_id else None)
        for it in db.execute(select(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.origin == "manual")).scalars():
            inp.primary_ids.add(it.doc_id)
            inp.primary.append({"doc_id": it.doc_id, "title": it.proposed_title, "path": corpus[it.doc_id].path if it.doc_id in corpus else "",
                                "diff_body": (it.diff or {}).get("body") or []})
        return inp, an


def _progress(tenant_id: str, request_id: str, attempt: int, an: dict, stage: str, done: int = 0, total: int = 0) -> None:
    an["progress"] = {"stage": stage, "done": done, "total": total}
    if not _fenced(tenant_id, request_id, attempt, analysis=dict(an)):
        raise Superseded()


def build_spec(inp: Input, model: ImpactModel) -> ChangeSpec:
    """Workflow A derives the facts from the staff member's own edit (what they actually changed); workflow B from the description. Answers to 'restate' refine it."""
    spec: ChangeSpec | None = None
    if inp.primary:
        facts, terms, summary, wants_all = [], [], inp.description, False
        for p in inp.primary:
            sp = spec_from_edit(p["title"], p["path"], p["diff_body"], inp.description)
            facts += sp.facts
            terms += sp.entity_terms
            wants_all = wants_all or sp.wants_all
        if facts:
            spec = ChangeSpec(summary=summary[:300], facts=facts[:5], entity_terms=list(dict.fromkeys(terms))[:12], wants_all=wants_all)
    if spec is None:
        spec = model.parse(inp.description)
    restated = inp.answers.get(RESTATE)
    if restated and not spec.actionable():
        again = model.parse(restated)
        if again.actionable():
            spec = ChangeSpec(summary=(inp.description + " | " + restated)[:300], facts=again.facts, entity_terms=list(dict.fromkeys(spec.entity_terms + again.entity_terms))[:12],
                              wants_all=spec.wants_all or again.wants_all)
    if inp.reference is not None:
        extra = entity_terms_from(f"{inp.reference.title} {inp.reference.path}")
        spec = spec.model_copy(update={"entity_terms": list(dict.fromkeys(spec.entity_terms + extra))[:12]})
    return spec


def _heading_questions(spec: ChangeSpec, cand: Candidate, ctx: list, answers: dict) -> tuple[list | None, Question | None]:
    """Inside ONE document, when several different sections hold the old value and the description does not tell them apart, ask which one(s)."""
    qid = _qid("scope", cand.doc_id)
    if qid in answers:
        a = answers[qid]
        return (None if a == ALL else [a]), None
    if spec.wants_all or not spec.entity_terms:
        return None, None
    best: dict[str, float] = {}
    for c in ctx:
        if c.has_value:
            best[c.passage.heading] = max(best.get(c.passage.heading, 0.0), disc.scope_fraction(spec.entity_terms, expand(context_of(cand, c.passage), spec.alias_pairs)))
    if len(best) < 2:
        return None, None
    top = max(best.values())
    tied = sorted(h for h, sc in best.items() if sc == top)
    if len(tied) < 2 or not any(are_siblings(a, b) for i, a in enumerate(tied) for b in tied[i + 1:]):
        return None, None                                                  # the same fact repeated under unrelated sections of one document: no need to ask
    names = [h or "(top of the document)" for h in tied]
    return None, Question(id=qid, doc_id=cand.doc_id, text=f"“{cand.title}” has {len(tied)} sections that match. Which one does this change apply to?", options=names + [ALL])


def _siblings(results: list[tuple[Candidate, DocResult]]) -> list[list[int]]:
    """Affected documents that are alternatives of one thing (several weekend batches of one course, morning vs evening ...) in the same folder are siblings."""
    groups: list[list[int]] = []
    for i, (ci, _) in enumerate(results):
        for g in groups:
            cj = results[g[0]][0]
            if ci.path == cj.path and are_siblings(ci.title, cj.title):
                g.append(i)
                break
        else:
            groups.append([i])
    return [g for g in groups if len(g) > 1]


def _resolve_siblings(spec: ChangeSpec, results: list[tuple[Candidate, DocResult]], answers: dict) -> list[Question]:
    questions: list[Question] = []
    if spec.wants_all:
        return questions
    affected = [(c, r) for c, r in results if r.classification in ("definite", "potential")]
    for g in _siblings(affected):
        members = [affected[i] for i in g]
        qid = _qid("pick", *[c.doc_id for c, _ in members])
        score = {c.doc_id: disc.scope_fraction(spec.entity_terms, f"{c.title} {c.path}") for c, _ in members}
        top = max(score.values())
        if qid in answers:
            keep = {c.doc_id for c, _ in members} if answers[qid] == ALL else {c.doc_id for c, _ in members if c.title == answers[qid]}
        elif sum(1 for x in score.values() if x == top) == 1:
            keep = {d for d, x in score.items() if x == top}
        else:
            questions.append(Question(id=qid, text="Several similar documents could be affected (for example different batches of one course). Which one does this change apply to?",
                                      options=sorted(c.title for c, _ in members) + [ALL]))
            continue
        for c, res in members:
            if c.doc_id not in keep and res.classification in ("definite", "potential"):
                res.classification, res.edits, res.confidence = "related_no_change", [], min(res.confidence, 0.3)
                res.reason = "A similar document that the description does not point to: left unchanged."
    return questions


def analyse(tenant_id: str, request_id: str, attempt: int, s: Settings, model: ImpactModel, embedder) -> dict:
    inp, an = _load(tenant_id, request_id, attempt)
    _progress(tenant_id, request_id, attempt, an, "parsing")
    spec = build_spec(inp, model)
    if not spec.actionable():
        qs = [Question(id=RESTATE, text="The description does not say what the old and the new value are. Restate it as “… from <old> to <new>”.", options=[])]
        return {"outcome": "clarify", "spec": spec, "questions": qs, "results": [], "discovered": 0, "rounds": [], "learned": []}
    _progress(tenant_id, request_id, attempt, an, "discovering")
    with session_scope() as db:
        cands, report = disc.discover_with_report(db, tenant_id, s, spec, embedder=embedder, exclude=set(inp.primary_ids), include={inp.reference.doc_id} if inp.reference else set())
    results: list[tuple[Candidate, DocResult]] = []
    questions: list[Question] = []
    total = len(cands)
    for n, cand in enumerate(cands):
        _progress(tenant_id, request_id, attempt, an, "evaluating", n, total)
        ctx = gather(spec, cand, s, None)
        restrict, q = _heading_questions(spec, cand, ctx, inp.answers)
        if q is not None:
            questions.append(q)
            continue
        if restrict is not None:
            ctx = gather(spec, cand, s, restrict)
        if not ctx:
            results.append((cand, DocResult(cand.doc_id, "not_relevant", 0.0, "Nothing in this document concerns the change.")))
            continue
        ev: Evaluation = model.evaluate(spec, cand, ctx)                 # outside any transaction
        results.append((cand, validate(spec, cand, ctx, ev, s)))
    questions += _resolve_siblings(spec, results, inp.answers)
    _progress(tenant_id, request_id, attempt, an, "finishing", total, total)
    return {"outcome": "clarify" if questions else "done", "spec": spec, "questions": questions, "results": results, "discovered": total,
            "rounds": report.rounds, "learned": report.learned}


def _item_for(tenant_id: str, request_id: str, s: Settings, cand: Candidate, res: DocResult) -> KbChangeItem:
    edits = [{"start": e.start, "end": e.end, "original": e.original, "replacement": e.replacement} for e in res.edits]
    body = apply_edits(cand.content, edits) if edits else cand.content
    body, _ = v.content(body, cand.title, s)
    diff, st = diffing.build(cand.title, cand.title, cand.content, body)
    suggestion = {"reason": res.reason, "signals": cand.signals, "uncertainty": res.uncertainty[:6], "flags": res.flags, "path": cand.path, "passages": res.passages,
                  "edits": [asdict(e) for e in res.edits], "kinds": {k: sum(1 for p in res.passages if p.get("kind") == k) for k in ("definite", "potential", "ambiguous", "related", "historical", "not_relevant")}}
    return KbChangeItem(tenant_id=tenant_id, request_id=request_id, doc_id=cand.doc_id, base_version=cand.version, base_checksum=cand.checksum, proposed_title=cand.title,
                        proposed_content=body, proposed_checksum=v.checksum(cand.title, body), diff=diff, stats=st, origin="ai", classification=res.classification,
                        confidence=res.confidence, ai_suggestion=suggestion, final_edits=edits, decision=None, row_version=0, updated_by="ai", updated_at=store.now())


def _persist(tenant_id: str, request_id: str, attempt: int, s: Settings, model: ImpactModel, out: dict, an: dict) -> None:
    results: list[tuple[Candidate, DocResult]] = out["results"]
    spec: ChangeSpec = out["spec"]
    kept = [(c, r) for c, r in results if r.classification in ("definite", "potential", "related_no_change")]
    counts = {k: sum(1 for _, r in results if r.classification == k) for k in ("definite", "potential", "related_no_change", "not_relevant")}
    flags = {k: sum(r.flags.get(k, 0) for _, r in results) for k in ("unsupported", "historical", "scope")}
    clarify = out["outcome"] == "clarify"
    kinds = {k: sum(1 for _, r in results for p in r.passages if p.get("kind") == k) for k in ("definite", "potential", "ambiguous", "related", "historical", "not_relevant")}
    summary = {"discovered": out["discovered"], **counts, "flags": flags, "kinds": kinds, "rounds": out.get("rounds", []), "learned": out.get("learned", []),
               "not_relevant": [{"doc_id": c.doc_id, "title": c.title, "reason": r.reason[:200]} for c, r in results if r.classification == "not_relevant"][:20]}
    an = dict(an)
    an.update(progress={"stage": "clarify" if clarify else "done", "done": out["discovered"], "total": out["discovered"]}, summary=summary, error=None, model=model.name,
              prompt_version=getattr(model, "prompt_version", "impact-v1"), spec=spec.model_dump(), questions=[q.model_dump() for q in out["questions"]])
    status = "awaiting_clarification" if clarify else "awaiting_decisions"
    with session_scope() as db:
        r = db.get(KbChangeRequest, (tenant_id, request_id))
        n = db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id, KbChangeRequest.status == "analysing",
                                                     KbChangeRequest.analysis_attempt == attempt)
                       .values(status=status, analysis=an, analysis_completed_at=store.now(), row_version=KbChangeRequest.row_version + 1, updated_at=store.now())
                       .execution_options(synchronize_session=False)).rowcount
        if n != 1 or r is None:
            raise Superseded()                                              # nothing below is written: the transaction rolls back
        if not clarify:
            db.execute(delete(KbChangeItem).where(KbChangeItem.tenant_id == tenant_id, KbChangeItem.request_id == request_id, KbChangeItem.origin == "ai"))
            db.flush()
            for c, res in kept:
                db.add(_item_for(tenant_id, request_id, s, c, res))
            db.flush()
        actor = (r.created_by or "ai")[:64]
        if clarify:
            audit.record(db, tenant_id, actor, "change_clarification_requested", "request", request_id, request_id=request_id, questions=len(out["questions"]), model=model.name)
        else:
            audit.record(db, tenant_id, actor, "change_analysis_completed", "request", request_id, request_id=request_id, attempt=attempt, candidates=out["discovered"],
                         definite=counts["definite"], potential=counts["potential"], related=counts["related_no_change"], unsupported=flags["unsupported"],
                         historical=flags["historical"], model=model.name)
            audit.record(db, tenant_id, actor, "change_suggestions_generated", "request", request_id, request_id=request_id, count=len(kept), documents=[c.doc_id for c, _ in kept][:20])


def _fail(tenant_id: str, request_id: str, attempt: int, message: str) -> None:
    with session_scope() as db:
        r = db.get(KbChangeRequest, (tenant_id, request_id))
        if r is None:
            return
        an = dict(r.analysis or {})
        an.update(error=message[:300], progress={"stage": "failed", "done": 0, "total": 0})
        n = db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id, KbChangeRequest.status == "analysing",
                                                     KbChangeRequest.analysis_attempt == attempt)
                       .values(status="draft", analysis=an, analysis_completed_at=store.now(), row_version=KbChangeRequest.row_version + 1, updated_at=store.now())
                       .execution_options(synchronize_session=False)).rowcount
        if n == 1:
            audit.record(db, tenant_id, (r.created_by or "ai")[:64], "change_analysis_failed", "request", request_id, request_id=request_id, attempt=attempt, error=message[:200])


def run(tenant_id: str, request_id: str, attempt: int, s: Settings, model: ImpactModel, embedder) -> None:
    """The whole job. Never raises: it either finishes, asks a question, hands the request back to the author with an error, or notices it was superseded."""
    try:
        out = analyse(tenant_id, request_id, attempt, s, model, embedder)
        with session_scope() as db:
            r = db.get(KbChangeRequest, (tenant_id, request_id))
            an = dict(r.analysis or {}) if r is not None else {}
        _persist(tenant_id, request_id, attempt, s, model, out, an)
    except Superseded:
        log.info("impact analysis %s attempt %s was superseded; stopping", request_id, attempt)
    except Exception as e:                                                  # noqa: BLE001 - the author gets a retryable error; never document text
        log.exception("impact analysis %s failed", request_id)
        try:
            _fail(tenant_id, request_id, attempt, f"{type(e).__name__}: the analysis could not be completed. Try again, or edit the documents yourself.")
        except Exception:                                                   # noqa: BLE001
            log.exception("could not record the analysis failure for %s", request_id)


def launch(tenant_id: str, request_id: str, attempt: int, s: Settings, model: ImpactModel, embedder) -> None:
    if not s.kb_ai_background:
        run(tenant_id, request_id, attempt, s, model, embedder)
        return
    threading.Thread(target=run, args=(tenant_id, request_id, attempt, s, model, embedder), name=f"impact-{request_id}", daemon=True).start()
