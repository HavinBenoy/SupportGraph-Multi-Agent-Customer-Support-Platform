"""Candidate discovery: which published documents might the described change touch?

Not plain RAG. Five signals are computed per document and recorded, so the reviewer can see WHY a document was selected:
  value     the old value occurs in the text (any spelling: ₹15,000 / Rs. 15000/- / 15k, 10 AM–1 PM / 10:00 am to 1:00 pm)
  scope     the entity words of the change (course, batch type, person ...) appear in the title, folder path, category, tags or headings
  lexical   word overlap between the change description and the document
  semantic  the best cosine similarity of the description to the document's LIVE passages (the configured embedding model; skipped when none are indexed)
  structure folder path and category (they feed `scope`; shown separately for the reviewer)
A document with the old value is always a candidate; documents without it are kept only when scope or semantic similarity is strong, and are then judged
"related, no change" unless a model finds a defensible edit. The text is never sent anywhere from here."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import KbDocument, KbVersion
from app.knowledge import workspace
from app.knowledge.impact.aliases import build_aliases, expand
from app.knowledge.impact.scan import ScanReport, scan_corpus
from app.knowledge.impact.scope import person_terms, scope_fraction  # noqa: F401 (scope_fraction re-exported)
from app.knowledge.impact.passages import slot_passages, with_headings
from app.knowledge.impact.spec import ChangeSpec, words
from app.knowledge.impact.values import find_value
from app.rag import retrieval

W_VALUE, W_SCOPE, W_LEX, W_SEM = 0.45, 0.25, 0.15, 0.15
MAX_SCAN_DOCS = 2000


@dataclass
class Candidate:
    doc_id: str
    title: str
    path: str
    category: str
    tags: list[str]
    version: int
    checksum: str
    content: str
    signals: dict = field(default_factory=dict)
    score: float = 0.0
    hits: list = field(default_factory=list)       # scan.Hit: the sections of this document that carry evidence (discovery "v2")
    dropped: int = 0                               # matching passages beyond the per-document limit (set by evaluate.gather)
    judge_notes: list = field(default_factory=list)  # things the section judge saw but did not use (shown to the reviewer)
    hunks: list = field(default_factory=list)      # edit.Hunk: the validated section revisions proposed by the model (patches on this document version)


def published_corpus(db: Session, tenant_id: str, *, exclude: set[str] = frozenset()) -> list[Candidate]:
    fmap = workspace.folders_by_id(db, tenant_id)
    place = workspace.placements(db, tenant_id)
    rows = db.execute(select(KbDocument, KbVersion).join(KbVersion, (KbVersion.tenant_id == KbDocument.tenant_id) & (KbVersion.doc_id == KbDocument.doc_id)
                                                         & (KbVersion.version_no == KbDocument.published_version))
                      .where(KbDocument.tenant_id == tenant_id, KbDocument.status == "published").order_by(KbDocument.doc_id).limit(MAX_SCAN_DOCS)).all()
    return [Candidate(d.doc_id, v.title, workspace.path_of(fmap, place.get(d.doc_id)), d.category, list(d.tags or []), v.version_no, v.checksum, v.content)
            for d, v in rows if d.doc_id not in exclude]


def structure_text(c: Candidate) -> str:
    heads = " ".join(h for _, _, _, h in with_headings(c.content) if h)
    return f"{c.title} {c.path} {c.category} {' '.join(c.tags)} {heads}"


def _semantic(db: Session, tenant_id: str, spec: ChangeSpec, embedder) -> dict[str, float]:
    if embedder is None:
        return {}
    try:
        q = embedder.embed_query(spec.summary or " ".join(spec.entity_terms))
        hits = retrieval.search(db, tenant_id=tenant_id, query_vector=q, model_name=embedder.name, top_k=retrieval.MAX_TOP_K)
    except Exception:                                      # noqa: BLE001 - a missing/unreachable embedder weakens discovery, it must not break it
        return {}
    best: dict[str, float] = {}
    for h in hits:
        best[h.doc_id] = max(best.get(h.doc_id, 0.0), h.score)
    return best


def key_terms(entity_terms: list[str], corpus: list[Candidate]) -> list[str]:
    """Of the words naming the subject, the ones that DISTINGUISH it: those that appear in the fewest document titles/folders (at least one). "Python Full Stack" is told apart
    from "Java Full Stack" by 'python'; 'full' and 'stack' are shared. A passage must carry every key term to count as being about the same subject."""
    df = {t: sum(1 for c in corpus if t in set(words(structure_text(c)))) for t in entity_terms}
    seen = [n for n in df.values() if n > 0]
    if not seen:
        return []
    low = min(seen)
    return [t for t, n in df.items() if n == low]


def occurrences(c: Candidate, spec: ChangeSpec, facts) -> int:
    """How many places in the document hold what the change is about: the old value, or (a change that names none) the attribute's current value on a line that is about the
    change's subject."""
    n = 0
    for f in facts:
        if f.old_value:
            n += len(find_value(c.content, f.old_value))
        else:
            ctx = structure_text(c)
            n += sum(1 for p, _ in slot_passages(c.content, f) if scope_fraction(spec.entity_terms, f"{ctx} {p.text}") >= 0.99)
    return n


def _tenant_groups(tenant_id: str) -> dict[str, list[str]]:
    try:
        from app.tenants.loader import load_tenant_config
        return dict(load_tenant_config(tenant_id).entity_aliases)
    except Exception:                                      # noqa: BLE001 - a tenant without a config file has no institute vocabulary; discovery still works
        return {}


def discover(db: Session, tenant_id: str, s: Settings, spec: ChangeSpec, *, embedder=None, exclude: set[str] = frozenset(), include: set[str] = frozenset()) -> list[Candidate]:
    return discover_with_report(db, tenant_id, s, spec, embedder=embedder, exclude=exclude, include=include)[0]


def discover_with_report(db: Session, tenant_id: str, s: Settings, spec: ChangeSpec, *, embedder=None, exclude: set[str] = frozenset(),
                         include: set[str] = frozenset()) -> tuple[list[Candidate], ScanReport]:
    """Ranked candidates (best first), at most `kb_ai_max_candidates`, and what the scan found (rounds, learned values). `include` forces documents in (workflow A keeps the
    reference document's own manual item out of this list via `exclude`)."""
    corpus = published_corpus(db, tenant_id, exclude=exclude)
    return rank(corpus, s, spec, groups=_tenant_groups(tenant_id), sem=_semantic_for(db, tenant_id, spec, embedder, corpus, s), include=include)


def _semantic_for(db, tenant_id, spec, embedder, corpus, s) -> dict[str, float]:
    return _semantic(db, tenant_id, spec, embedder)


def rank(corpus: list[Candidate], s: Settings, spec: ChangeSpec, *, groups: dict[str, list[str]] | None = None, sem: dict[str, float] | None = None,
         include: set[str] = frozenset()) -> tuple[list[Candidate], ScanReport]:
    """The database-free core of discovery: aliases, pruning of empty words, the evidence scan and the ranking, over an in-memory corpus."""
    v2 = s.kb_ai_discovery == "v2"
    groups, sem = groups or {}, sem or {}
    texts = [c.content for c in corpus] if v2 else []
    vocab = {w for c in corpus for w in words(f"{c.title} {c.path} {c.content}")}
    known = build_aliases(spec, groups, texts, with_acronym=False) if v2 else []           # institute / document vocabulary only: the subject's own acronym says nothing about a word
    alias_words = {w for form, target in known for w in (*words(form), *target)}
    pruned = [t for t in spec.entity_terms if t in vocab or t in alias_words or t in person_terms(spec)]           # a word of the description that no document contains ("goes", "raise") names nothing
    if pruned:
        spec.entity_terms = pruned
    if v2:
        spec.alias_pairs = build_aliases(spec, groups, texts)
    spec.key_terms = key_terms(spec.entity_terms, corpus)
    facts = spec.actionable()
    report = scan_corpus(corpus, spec) if (v2 and facts) else ScanReport()
    query_terms = [w for w in words(spec.summary) if w in set(spec.entity_terms)] or spec.entity_terms
    out: list[Candidate] = []
    for c in corpus:
        scope = scope_fraction(spec.entity_terms, expand(structure_text(c), spec.alias_pairs))
        c.hits = report.hits.get(c.doc_id, []) if v2 else []
        occ = (sum(1 for h in c.hits if h.how != "role") if v2 else occurrences(c, spec, facts))
        doc_words = set(words(c.title + " " + c.content))
        lex = (sum(1 for t in query_terms if t in doc_words) / len(query_terms)) if query_terms else 0.0
        sm = max(0.0, sem.get(c.doc_id, 0.0))
        strong = sum(1 for h in c.hits if h.strong)
        c.signals = {"value": occ, "scope": round(scope, 2), "lexical": round(lex, 2), "semantic": round(sm, 2), "path": c.path, "category": c.category}
        if v2:
            c.signals.update(evidence=len(c.hits), strong=strong, aliases=sorted({a for h in c.hits for a in h.aliases}))
        c.score = round(W_VALUE * (1.0 if occ else 0.0) + W_SCOPE * scope + W_LEX * lex + W_SEM * sm + (0.1 if strong else 0.0) + (0.05 if c.hits else 0.0), 4)
        if occ or c.hits or c.doc_id in include or (spec.general and not facts and (lex >= 0.5 or sm >= s.kb_ai_semantic_floor)) or (scope >= 0.99 and spec.entity_terms and (lex >= 0.5 or sm >= s.kb_ai_semantic_floor)):
            out.append(c)
    out.sort(key=lambda c: (-c.score, c.doc_id))
    return out[: s.kb_ai_max_candidates], report
