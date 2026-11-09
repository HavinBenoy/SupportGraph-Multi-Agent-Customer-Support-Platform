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
from app.knowledge.impact.passages import with_headings
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


def scope_fraction(terms: list[str], haystack: str) -> float:
    if not terms:
        return 1.0
    have = set(words(haystack))
    return sum(1 for t in terms if t in have) / len(terms)


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


def discover(db: Session, tenant_id: str, s: Settings, spec: ChangeSpec, *, embedder=None, exclude: set[str] = frozenset(), include: set[str] = frozenset()) -> list[Candidate]:
    """Ranked candidates (best first), at most `kb_ai_max_candidates`. `include` forces documents in (workflow A keeps the reference document's own manual item out of
    this list via `exclude`)."""
    corpus = published_corpus(db, tenant_id, exclude=exclude)
    vocab = {w for c in corpus for w in words(f"{c.title} {c.path} {c.content}")}
    pruned = [t for t in spec.entity_terms if t in vocab]                    # a word of the description that no document contains ("goes", "raise") names nothing
    if pruned:
        spec.entity_terms = pruned
    spec.key_terms = key_terms(spec.entity_terms, corpus)
    sem = _semantic(db, tenant_id, spec, embedder)
    old_values = [f.old_value for f in spec.actionable()]
    query_terms = [w for w in words(spec.summary) if w in set(spec.entity_terms)] or spec.entity_terms
    out: list[Candidate] = []
    for c in corpus:
        occ = sum(len(find_value(c.content, ov)) for ov in old_values)
        scope = scope_fraction(spec.entity_terms, structure_text(c))
        doc_words = set(words(c.title + " " + c.content))
        lex = (sum(1 for t in query_terms if t in doc_words) / len(query_terms)) if query_terms else 0.0
        sm = max(0.0, sem.get(c.doc_id, 0.0))
        c.signals = {"value": occ, "scope": round(scope, 2), "lexical": round(lex, 2), "semantic": round(sm, 2), "path": c.path, "category": c.category}
        c.score = round(W_VALUE * (1.0 if occ else 0.0) + W_SCOPE * scope + W_LEX * lex + W_SEM * sm, 4)
        if occ or c.doc_id in include or (scope >= 0.99 and spec.entity_terms and (lex >= 0.5 or sm >= s.kb_ai_semantic_floor)):
            out.append(c)
    out.sort(key=lambda c: (-c.score, c.doc_id))
    return out[: s.kb_ai_max_candidates]
