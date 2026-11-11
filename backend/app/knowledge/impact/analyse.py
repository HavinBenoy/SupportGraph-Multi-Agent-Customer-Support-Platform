"""The impact-analysis job (Part 2). It never publishes and never edits a document: it only WRITES SUGGESTIONS (kb_change_items with origin='ai') that staff must
decide on and an administrator must approve.

Pipeline: parse the change -> (ask for clarification when the description cannot pick between equally good targets) -> discover candidate documents -> retrieve the
passages that hold the old value -> let the model judge each passage -> validate every proposal with deterministic guards -> persist suggestions.

Concurrency and failure: the job runs in a background thread (or inline in tests) under a fenced attempt number. Every write checks `status='analysing' AND
analysis_attempt=<mine>` in the UPDATE itself, so a cancelled, retried or taken-over run can never overwrite newer work: it notices and stops quietly. The model is
called OUTSIDE any database transaction. Any error returns the request to `draft` with a short error (never document text) so staff can retry."""

import hashlib
import re
import logging
import threading
from dataclasses import replace, asdict, dataclass, field

from sqlalchemy import delete, select, update

from app.config import Settings
from app.db.models import KbChangeItem, KbChangeRequest
from app.db.session import session_scope
from app.knowledge import audit, diffing, store
from app.knowledge import validation as v
from app.knowledge.impact import tasks as TK
from app.knowledge.impact import trace as T
from app.knowledge.impact import discover as disc
from app.knowledge.impact.discover import Candidate
from app.knowledge.impact.aliases import expand
from app.knowledge.impact.evaluate import (DocResult, Evaluation, ImpactModel, apply_edits, context_of, gather, validate)
from app.knowledge.impact.spec import words, split_intents, ChangeSpec, Question, are_siblings, entity_terms_from, spec_from_edit

log = logging.getLogger(__name__)
ALL = "All of them"
RESTATE = "restate"
_ACTION = re.compile(r"\b(?:add(?:s|ed|ing)?|remov\w+|delet\w+|drop(?:s|ped|ping)?|discontinu\w+|cancel\w*|no longer|not (?:required|needed|allowed|applicable)|now|also|includ\w+|introduc\w+|new|launch\w*|"
                     r"stop\w*|replac\w+|rewrit\w+|reword\w*|rephras\w+|instead|extend\w*|increas\w+|reduc\w+|lower\w*|rais\w+|switch\w*|convert\w*|mov(?:e|es|ed|ing)|shift\w*|waiv\w+|"
                     r"relax\w*|require[sd]?|must|only|open|clos(?:e|es|ed|ing)|available|unavailable|offer\w*|hybrid|offline|online|merge\w*|split|renam\w+|correct\w*)\b", re.I)
_TARGET = re.compile(r"\b(?:to|into|as|from|instead of|by)\b", re.I)


def vague(description: str) -> bool:
    """A description that says that something changed, but neither what the change is (no action such as add / remove / replace / now / no longer) nor to what: acting on it would be a guess."""
    d = " ".join((description or "").split())
    return len(d) < 12 or not (_ACTION.search(d) or _TARGET.search(d))


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


def generalize(spec: ChangeSpec, description: str, restated: str = "") -> ChangeSpec | None:
    """The spec of a change that is not a value replacement, or None when the description is too vague to act on (see `vague`)."""
    terms = list(dict.fromkeys(spec.entity_terms + entity_terms_from(description) + entity_terms_from(restated)))[:12]
    if not terms or vague(f"{description} {restated}"):
        return None
    return spec.model_copy(update={"general": True, "entity_terms": terms, "summary": (description + (" | " + restated if restated else ""))[:600]})


_PRONOUN = re.compile(r"\b(?:he|she|they|him|her|his|their|it|its)\b", re.I)


def build_intents(inp: Input, model: ImpactModel) -> tuple[list[ChangeSpec], dict]:
    """One spec per requested change (tasks.decompose: the model's schema-validated task list when it is grounded and covers the request, else the rule split). A manual
    edit (workflow A) or a single change keeps the one-spec path. A task that refers back with a pronoun borrows the subject words of the task before it."""
    if inp.primary:
        return [build_spec(inp, model)], {"source": "single", "tasks": [inp.description], "uncovered": [], "duplicates": [], "note": ""}
    chat = getattr(model, "_chat", None)
    dec = TK.decompose(inp.description, chat)
    info = {"source": dec.source, "tasks": dec.tasks, "uncovered": dec.uncovered, "duplicates": dec.duplicates, "note": dec.note}
    T.event("decomposition", dec.source, tasks=dec.tasks, uncovered=dec.uncovered or None, duplicates=dec.duplicates or None, why=dec.note or None)
    if len(dec.tasks) < 2:
        return [build_spec(inp, model)], info
    out: list[ChangeSpec] = []
    for part in dec.tasks:
        sp = build_spec(replace(inp, description=part), model)
        if out and _PRONOUN.search(part):
            sp = sp.model_copy(update={"entity_terms": list(dict.fromkeys(sp.entity_terms + out[-1].entity_terms))[:12]})
        out.append(sp.model_copy(update={"summary": part[:600]}))
    return out, info


_TIME_WORDS = {"today", "tomorrow", "yesterday", "onwards", "now", "currently", "immediately", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
               "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "week", "month", "year"}


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
        # The subject words of the description itself are always part of the search, whatever a language-model parse made of it: a model parse of "Change the work
        # experience of santhosh sir to Mechanical industry" named only "mechanical industry" (the NEW value) as the subject, so the document about Santhosh scored 0%.
        new_words = {w for f in spec.facts for w in words(f.new_value or "") + words(f.old_value or "")}      # values are not the subject
        own = [t for t in entity_terms_from(inp.description) if t not in new_words and t not in _TIME_WORDS]
        spec = spec.model_copy(update={"entity_terms": list(dict.fromkeys([t for t in spec.entity_terms if t not in new_words] + own))[:12]})
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
    T.event("request", "received", description_chars=len(inp.description), answers=sorted(inp.answers)[:10], model=model.name,
            settings={"discovery": s.kb_ai_discovery, "judge": s.kb_ai_judge, "thinking": s.kb_ai_judge_thinking, "context_tokens": s.kb_ai_context_tokens,
                      "max_output": s.kb_ai_judge_max_tokens, "num_ctx": s.kb_ai_judge_num_ctx, "semantic_check": s.kb_ai_semantic_check, "retries": s.kb_ai_edit_retries},
            text={"description": inp.description})
    intents, decomposition = build_intents(inp, model)
    ready: list[ChangeSpec] = []
    skipped_vague: list[int] = []
    for k, spec in enumerate(intents, 1):
        T.event("understanding", "value_change" if spec.actionable() else "other_change", intent=k, of=len(intents),
                facts=[{"attribute": f.attribute, "old": f.old_value, "new": f.new_value} for f in spec.actionable()], terms=spec.entity_terms,
                aliases=[a for a, _ in spec.alias_pairs][:10], text={"intent": spec.summary})
        if not spec.actionable():
            # Not a value replacement ("add ...", "remove ...", "now also ...", a rewrite): the language model reads the relevant sections and drafts the revision. Only a
            # description that names nothing to search for, or says nothing about what the change is, cannot be acted on.
            general = generalize(spec, spec.summary or inp.description, str(inp.answers.get(RESTATE, "")))
            if general is None:
                T.event("understanding", "vague", intent=k, why="the intent names no action (add / remove / change to ...) or no subject: it is not acted on")
                skipped_vague.append(k)
                continue
            spec = general
            T.event("understanding", "general_change", intent=k, terms=spec.entity_terms, why="not a value replacement: the model reads the sections and drafts the revision")
        ready.append(spec)
    if not ready:
        qs = [Question(id=RESTATE, text="The description does not say what should change or where. Describe it with the subject (course, person, policy ...) and what to add, remove or change.", options=[])]
        return {"outcome": "clarify", "spec": intents[0], "questions": qs, "results": [], "discovered": 0, "rounds": [], "learned": [], "intents": [], "decomposition": decomposition}
    _progress(tenant_id, request_id, attempt, an, "discovering")
    # Discovery per intent (an intent that finds nothing never hides the others); a document nominated by several intents is evaluated once per intent and the results merged.
    by_doc: dict[str, list[tuple[int, ChangeSpec, Candidate]]] = {}
    rounds, learned, summaries = [], [], []
    for k, spec in enumerate(ready, 1):
        with session_scope() as db:
            cands, report = disc.discover_with_report(db, tenant_id, s, spec, embedder=embedder, exclude=set(inp.primary_ids),
                                                      include={inp.reference.doc_id} if inp.reference else set())
        T.event("intent", "discovered", intent=k, of=len(ready), documents=[c.doc_id for c in cands], why=spec.summary[:200] if len(ready) > 1 else None)
        summaries.append({"intent": k, "text": spec.summary[:300], "facts": [{"attribute": f.attribute, "old": f.old_value, "new": f.new_value} for f in spec.actionable()],
                          "general": spec.general, "documents": [c.doc_id for c in cands]})
        rounds += report.rounds
        learned += report.learned
        for c in cands:
            by_doc.setdefault(c.doc_id, []).append((k, spec, c))
    results: list[tuple[Candidate, DocResult]] = []
    per_intent: dict[int, list[tuple[Candidate, DocResult]]] = {}
    q_per_intent: dict[int, int] = {}
    questions: list[Question] = []
    total = sum(len(v) for v in by_doc.values())
    n = 0
    finals: dict[str, str] = {}
    originals: dict[str, str] = {}
    for doc_id, entries in by_doc.items():
        parts: list[DocResult] = []
        ks: list[int] = []
        provenance: list[dict] = []
        original = entries[0][2].content
        working = original                                                   # the UNPUBLISHED working copy: each task reads what the earlier tasks produced
        for k, spec, cand in entries:
            _progress(tenant_id, request_id, attempt, an, "evaluating", n, total)
            n += 1
            if working != original:
                cand = _on_working_copy(cand, working, spec, s)
                T.event("task", "working_copy", doc_id, intent=k, why="this task reads the text produced by the earlier task(s) on this document (never published)")
            res, q = _evaluate_one(tenant_id, request_id, attempt, an, s, model, spec, cand, inp, n, total, intent=k)
            if q is not None:
                questions.append(q)
                q_per_intent[k] = q_per_intent.get(k, 0) + 1
                continue
            parts.append(res)
            ks.append(k)
            per_intent.setdefault(k, []).append((cand, res))
            if res.edits:
                try:
                    working = apply_edits(working, [{"start": e.start, "end": e.end, "original": e.original, "replacement": e.replacement} for e in res.edits])
                except Exception as e:                                       # noqa: BLE001 - a patch that no longer fits is reported, the earlier tasks' text is kept
                    res.uncertainty.append(f"this task's change could not be combined with the earlier tasks' changes ({type(e).__name__}); it was left out")
                    res.edits = []
            provenance.append({"task": k, "classification": res.classification, "reason": res.reason[:300], "edits": [asdict(e) for e in res.edits][:8]})
        if parts:
            merged = parts[0] if len(parts) == 1 else merge_results(parts, ks)
            if len(parts) > 1 and working != original:
                merged = _combined(merged, parts, original, working, doc_id)
            merged.tasks = provenance
            results.append((entries[0][2], merged))
            finals[doc_id], originals[doc_id] = working, original
    for k, spec in enumerate(ready, 1):
        questions += _resolve_siblings(spec, per_intent.get(k, []), inp.answers)
    task_rows = []
    for sm in summaries:
        k = sm["intent"]
        docs = [(c.doc_id, r.classification, bool(r.edits)) for c, r in per_intent.get(k, [])]
        sm["outcomes"] = {d: c for d, c, _ in docs}
        st = TK.status_of(docs, False, q_per_intent.get(k, 0))
        if st != "completed" and any(r.uncertainty and any("could not be used" in u or "could not give" in u for u in r.uncertainty) for _, r in per_intent.get(k, [])) and not any(e for _, _, e in docs):
            st = "failed"
        sm["status"], sm["status_label"] = st, TK.STATUS[st]
        task_rows.append({"intent": k, "text": sm["text"], "new_values": [f["new"] for f in sm["facts"] if f.get("new")], "edited": [d for d, _, e in docs if e],
                          "attribute": (sm["facts"][0]["attribute"] if sm["facts"] else ""), "subject_terms": ready[k - 1].entity_terms})
        T.event("task", st, intent=k, why=TK.STATUS[st], documents=[d for d, _, _ in docs])
    for k in skipped_vague:
        summaries.append({"intent": k, "text": intents[k - 1].summary[:300], "facts": [], "general": False, "documents": [], "outcomes": {}, "status": "clarify",
                          "status_label": TK.STATUS["clarify"]})
    problems = TK.final_check(task_rows, finals, originals) if len(ready) > 1 else []
    for pr in problems:
        T.event("final_check", "problem", why=pr)
    if problems:
        for _, r in results:
            r.uncertainty += [pr for pr in problems if r.doc_id in pr][:3]
    _progress(tenant_id, request_id, attempt, an, "finishing", total, total)
    return {"outcome": "clarify" if questions else "done", "spec": ready[0], "questions": questions, "results": results, "discovered": len(by_doc),
            "rounds": rounds, "learned": learned, "intents": summaries if (len(ready) > 1 or skipped_vague) else [], "specs": [x.model_dump() for x in ready],
            "decomposition": decomposition, "final_check": problems}


def _on_working_copy(cand: Candidate, text: str, spec: ChangeSpec, s: Settings) -> Candidate:
    """The same document with the working-copy text: evidence offsets must point into the text this task edits (they were found in the published text)."""
    wc = Candidate(doc_id=cand.doc_id, title=cand.title, path=cand.path, category=cand.category, tags=cand.tags, version=cand.version, checksum=cand.checksum, content=text)
    ranked, _ = disc.rank([wc], s, spec, groups={}, sem={wc.doc_id: cand.signals.get("semantic", 0.0) or 0.0}, include={wc.doc_id})
    out = ranked[0] if ranked else wc
    out.signals = {**cand.signals, **out.signals}
    return out


def _combined(merged: DocResult, parts: list[DocResult], original: str, final: str, doc_id: str) -> DocResult:
    """Several tasks changed one document in sequence: the proposal is ONE set of patches from the published text to the final working copy (so no task's change is lost
    when they touch the same line), marked definite only when every contributing change was."""
    from app.knowledge.impact import edit as E
    from app.knowledge.impact.context import Row, Section
    from app.knowledge.impact.evaluate import Edit
    from app.knowledge.impact.passages import with_headings

    rows = with_headings(original)
    sec = Section([Row(a, b, raw) for a, b, raw, _ in rows], "", "document", set(), headings=[h for _, _, _, h in rows])
    orig = [r.text for r in sec.rows]
    new = final.split("\n")
    if original.endswith("\n") and new and new[-1] == "":
        new = new[:-1]
    hunks = E.build_hunks(sec, orig, new, E.line_ops(orig, new))
    every = [e for p in parts for e in p.edits]
    verdict = "definite" if every and all(e.verdict == "definite" for e in every) else "potential"
    conf = min((e.confidence for e in every), default=merged.confidence)
    flags = list(dict.fromkeys(f for e in every for f in getattr(e, "flags", []) or []))
    why = " | ".join(f"{e.reason[:160]}" for e in every)[:400]
    merged.edits = [Edit(h.start, h.end, h.original, h.replacement, "", verdict, conf, why, h.kind, flags) for h in hunks]
    if merged.edits:
        merged.classification = "definite" if verdict == "definite" and not merged.uncertainty else "potential"
    return merged


def _evaluate_one(tenant_id, request_id, attempt, an, s, model, spec, cand, inp, n, total, *, intent: int):
    """One document against one intent: passages -> (model) section reading -> evaluation -> validation. Returns (result, question)."""
    ctx = gather(spec, cand, s, None)
    restrict, q = _heading_questions(spec, cand, ctx, inp.answers)
    if q is not None:
        T.event("passages", "question", cand.doc_id, intent=intent, why="several sections could be meant; the staff member is asked which",
                headings=[o for o in getattr(q, "options", [])][:8])
        return None, q
    if restrict is not None:
        ctx = gather(spec, cand, s, restrict)
    if not ctx:
        T.event("passages", "no_passage", cand.doc_id, intent=intent, why="no line of the document (or the heading above it) names what the change is about")
        return DocResult(cand.doc_id, "not_relevant", 0.0, "Nothing in this document concerns the change."), None
    T.event("passages", "selected", cand.doc_id, intent=intent, count=len(ctx), headings=list(dict.fromkeys(pc.passage.heading for pc in ctx))[:8],
            how=[getattr(pc.hit, "how", None) or ("general" if spec.general else "value") for pc in ctx][:12], text={"lines": "\n".join(pc.passage.text for pc in ctx)})
    judge = getattr(model, "judge", None)
    if judge is None:
        T.event("context", "model_not_invoked", cand.doc_id, intent=intent, why="the configured impact model has no section editor (rules-only mode)")
    else:
        _progress(tenant_id, request_id, attempt, an, "reading", n, total)
        ctx = judge(spec, cand, ctx, s)                                      # the section editor (a model call, outside any transaction); a no-op without a model
    ev: Evaluation = model.evaluate(spec, cand, ctx)                         # outside any transaction
    res = validate(spec, cand, ctx, ev, s)
    T.event("classification", res.classification, cand.doc_id, intent=intent, confidence=res.confidence, edits=[e.kind for e in res.edits], reason=res.reason[:300],
            passages=[{"verdict": p.get("verdict"), "judge": p.get("judge"), "why": (p.get("reason") or "")[:200]} for p in res.passages][:12],
            uncertainty=[u[:200] for u in res.uncertainty][:6])
    return res, None


_ORDER = {"definite": 3, "potential": 2, "related_no_change": 1, "not_relevant": 0}


def merge_results(parts: list[DocResult], intents: list[int]) -> DocResult:
    """One document, several intents: the edits of all of them (never overlapping: the earlier intent wins and the overlap is reported), every passage, and a classification
    that is `definite` only when every edit is."""
    from app.knowledge.impact.evaluate import _non_overlapping

    best = max(parts, key=lambda r: _ORDER[r.classification])
    out = DocResult(best.doc_id, best.classification, max(r.confidence for r in parts),
                    " | ".join(f"Intent {k}: {r.reason}" for k, r in zip(intents, parts) if r.reason)[:1200])
    for k, r in zip(intents, parts):
        out.edits += r.edits
        out.uncertainty += [f"(intent {k}) {u}" for u in r.uncertainty]
        out.passages += [{**p, "intent": k} for p in r.passages]
        for f, c in r.flags.items():
            out.flags[f] = out.flags.get(f, 0) + c
    out.edits = _non_overlapping(out)
    for i, p in enumerate(out.passages):
        p["n"] = i
    if out.edits:
        out.classification = "definite" if all(e.verdict == "definite" for e in out.edits) and not out.uncertainty else "potential"
    return out


def _item_for(tenant_id: str, request_id: str, s: Settings, cand: Candidate, res: DocResult) -> KbChangeItem:
    edits = [{"start": e.start, "end": e.end, "original": e.original, "replacement": e.replacement} for e in res.edits]
    body = apply_edits(cand.content, edits) if edits else cand.content
    body, _ = v.content(body, cand.title, s)
    diff, st = diffing.build(cand.title, cand.title, cand.content, body)
    suggestion = {"reason": res.reason, "signals": cand.signals, "uncertainty": res.uncertainty[:6], "flags": res.flags, "path": cand.path, "passages": res.passages,
                  "edits": [asdict(e) for e in res.edits], "tasks": getattr(res, "tasks", None) or [], "kinds": {k: sum(1 for p in res.passages if p.get("kind") == k) for k in ("definite", "potential", "ambiguous", "related", "historical", "not_relevant")}}
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
              prompt_version=getattr(model, "prompt_version", "impact-v1"), spec=spec.model_dump(), questions=[q.model_dump() for q in out["questions"]],
              trace=out.get("trace"), intents=out.get("intents") or [], specs=out.get("specs") or [spec.model_dump()], decomposition=out.get("decomposition"),
              final_check=out.get("final_check") or [])
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


def _fail(tenant_id: str, request_id: str, attempt: int, message: str, trace: dict | None = None) -> None:
    with session_scope() as db:
        r = db.get(KbChangeRequest, (tenant_id, request_id))
        if r is None:
            return
        an = dict(r.analysis or {})
        an.update(error=message[:300], progress={"stage": "failed", "done": 0, "total": 0})
        if trace is not None:
            an["trace"] = trace
        n = db.execute(update(KbChangeRequest).where(KbChangeRequest.tenant_id == tenant_id, KbChangeRequest.request_id == request_id, KbChangeRequest.status == "analysing",
                                                     KbChangeRequest.analysis_attempt == attempt)
                       .values(status="draft", analysis=an, analysis_completed_at=store.now(), row_version=KbChangeRequest.row_version + 1, updated_at=store.now())
                       .execution_options(synchronize_session=False)).rowcount
        if n == 1:
            audit.record(db, tenant_id, (r.created_by or "ai")[:64], "change_analysis_failed", "request", request_id, request_id=request_id, attempt=attempt, error=message[:200])


def run(tenant_id: str, request_id: str, attempt: int, s: Settings, model: ImpactModel, embedder) -> None:
    """The whole job. Never raises: it either finishes, asks a question, hands the request back to the author with an error, or notices it was superseded."""
    tr = T.Trace(request_id, attempt, content=s.kb_ai_trace_content)
    try:
        with T.tracing(tr):
            out = analyse(tenant_id, request_id, attempt, s, model, embedder)
            T.event("analysis", out["outcome"], documents=out["discovered"], questions=len(out["questions"]))
        out["trace"] = tr.export()
        with session_scope() as db:
            r = db.get(KbChangeRequest, (tenant_id, request_id))
            an = dict(r.analysis or {}) if r is not None else {}
        _persist(tenant_id, request_id, attempt, s, model, out, an)
    except Superseded:
        log.info("impact analysis %s attempt %s was superseded; stopping", request_id, attempt)
    except Exception as e:                                                  # noqa: BLE001 - the author gets a retryable error; never document text
        log.exception("impact analysis %s failed", request_id)
        tr.add("analysis", "error", error=type(e).__name__)
        try:
            _fail(tenant_id, request_id, attempt, f"{type(e).__name__}: the analysis could not be completed. Try again, or edit the documents yourself.", tr.export())
        except Exception:                                                   # noqa: BLE001
            log.exception("could not record the analysis failure for %s", request_id)


def launch(tenant_id: str, request_id: str, attempt: int, s: Settings, model: ImpactModel, embedder) -> None:
    if not s.kb_ai_background:
        run(tenant_id, request_id, attempt, s, model, embedder)
        return
    threading.Thread(target=run, args=(tenant_id, request_id, attempt, s, model, embedder), name=f"impact-{request_id}", daemon=True).start()
