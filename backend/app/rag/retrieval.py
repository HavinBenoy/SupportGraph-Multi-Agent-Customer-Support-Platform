"""Vector search over a tenant's ingested user documents.

Every query is filtered on THREE things, in the database, before any similarity is computed:
  tenant_id            -> one tenant can never see another's passages
  audience = 'public' -> staff-only text cannot be returned even if it somehow got into the table
  embedding_model      -> vectors from different models live in different spaces; comparing them returns
                          plausible-looking nonsense, so only the current model's vectors are searched.
The caller passes tenant_id from trusted request context, never from the user's message.

On Postgres the search uses pgvector's cosine distance operator (`<=>`), exact (no ANN index: see migration 0002).
On SQLite (the fast test database) vectors are stored as JSON, so the same search runs in Python. Both paths apply the
same filters and the same ordering, and tests/db/test_postgres_rag.py checks they agree.
"""

import math
from dataclasses import dataclass

from sqlalchemy import and_, func, select

from app.rag import hybrid
from sqlalchemy.orm import Session

from app.db.models import EMBEDDING_DIM, Chunk, Document

MAX_TOP_K = 50


@dataclass(frozen=True)
class RetrievedChunk:
    doc_id: str
    chunk_index: int
    doc_title: str
    heading: str  # breadcrumb, e.g. "Returns and Refunds > Return window"
    text: str
    score: float  # cosine similarity: 1.0 identical direction, 0 unrelated, negative opposed
    doc_version: int | None = None  # M6: the knowledge-store version this passage was published from (None for articles loaded from disk)

    @property
    def source_id(self) -> str:
        """The id the model must cite, and the code later validates: '<doc-id>#<chunk-index>'."""
        return f"{self.doc_id}#{self.chunk_index}"

    def to_dict(self) -> dict:
        """Plain data for graph state and traces (dicts serialise anywhere; objects may not)."""
        out = {"source_id": self.source_id, "doc_id": self.doc_id, "chunk_index": self.chunk_index,
               "title": self.doc_title, "heading": self.heading, "text": self.text, "score": round(self.score, 4)}
        if self.doc_version is not None:
            out["version"] = self.doc_version
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "RetrievedChunk":
        return cls(doc_id=d["doc_id"], chunk_index=d["chunk_index"], doc_title=d["title"], heading=d["heading"],
                   text=d["text"], score=d["score"], doc_version=d.get("version"))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _check(tenant_id: str, query_vector: list[float], top_k: int) -> None:
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError("tenant_id is required for retrieval")  # fail closed: never search 'everything'
    if len(query_vector) != EMBEDDING_DIM:
        raise ValueError(f"query vector has {len(query_vector)} dimensions, expected {EMBEDDING_DIM}")
    if not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k must be between 1 and {MAX_TOP_K}")


def search(db: Session, *, tenant_id: str, query_vector: list[float], model_name: str, top_k: int) -> list[RetrievedChunk]:
    """The `top_k` most similar user passages for this tenant and embedding model, best first.
    Scores are returned as-is (no threshold): see retrieve() for the thresholded version."""
    _check(tenant_id, query_vector, top_k)
    on_doc = and_(Document.tenant_id == Chunk.tenant_id, Document.doc_id == Chunk.doc_id)
    scope = (
        Chunk.tenant_id == tenant_id,
        Chunk.audience =="public",
        Document.audience =="public",
        Chunk.embedding_model == model_name,
    )

    if db.get_bind().dialect.name == "postgresql":
        distance = Chunk.embedding.cosine_distance(query_vector)
        stmt = (
            select(Chunk, Document.title, (1 - distance).label("score"), Document.kb_version)
            .join(Document, on_doc)
            .where(*scope)
            .order_by(distance, Chunk.doc_id, Chunk.chunk_index)  # ties broken deterministically
            .limit(top_k)
        )
        rows = [(c, title, float(score), ver) for c, title, score, ver in db.execute(stmt)]
    else:
        stmt = select(Chunk, Document.title, Document.kb_version).join(Document, on_doc).where(*scope)
        scored = [(c, title, _cosine(query_vector, list(c.embedding)), ver) for c, title, ver in db.execute(stmt)]
        scored.sort(key=lambda r: (-r[2], r[0].doc_id, r[0].chunk_index))
        rows = scored[:top_k]

    return [RetrievedChunk(doc_id=c.doc_id, chunk_index=c.chunk_index, doc_title=title, heading=c.heading,
                           text=c.text, score=score, doc_version=ver) for c, title, score, ver in rows]


def retrieve(db: Session, *, tenant_id: str, query_vector: list[float], model_name: str, top_k: int,
             min_score: float) -> list[RetrievedChunk]:
    """search() minus anything below `min_score`. An empty list means "nothing relevant", and the bot abstains."""
    return [c for c in search(db, tenant_id=tenant_id, query_vector=query_vector, model_name=model_name, top_k=top_k)
            if c.score >= min_score]


def search_hybrid(db: Session, *, tenant_id: str, query_vector: list[float], query_text: str, model_name: str, top_k: int, lexical_weight: float = 1.0) -> list[RetrievedChunk]:
    """Vector search AND keyword search over the same public passages, fused by rank. `score` stays the cosine similarity of each passage (so every threshold that already exists
    keeps its meaning); only the ORDER and the membership of the top_k change: a passage the embedding ranked low but that contains the question's rare words (a name, a code, an
    abbreviation) now gets in. The same tenant / audience / embedding-model filters apply to both lanes."""
    _check(tenant_id, query_vector, top_k)
    vec = search(db, tenant_id=tenant_id, query_vector=query_vector, model_name=model_name, top_k=min(MAX_TOP_K, max(top_k * 3, 12)))
    on_doc = and_(Document.tenant_id == Chunk.tenant_id, Document.doc_id == Chunk.doc_id)
    rows = db.execute(select(Chunk.doc_id, Chunk.chunk_index, Chunk.heading, Chunk.text, Document.title, Document.kb_version).join(Document, on_doc)
                      .where(Chunk.tenant_id == tenant_id, Chunk.audience == "public", Document.audience == "public", Chunk.embedding_model == model_name)
                      .order_by(Chunk.doc_id, Chunk.chunk_index)).all()
    meta = {(r[0], r[1]): r for r in rows}
    lex = hybrid.bm25([((r[0], r[1]), f"{r[4]} {r[2]} {r[3]}") for r in rows], query_text)[: min(MAX_TOP_K, max(top_k * 3, 12))]
    by_key = {(c.doc_id, c.chunk_index): c for c in vec}
    missing = [k for k, _ in lex if k not in by_key]
    if missing:                                                       # keyword hits the vector lane did not return: they still need their cosine
        for key, cos in _cosines(db, tenant_id, model_name, query_vector, missing).items():
            r = meta[key]
            by_key[key] = RetrievedChunk(doc_id=key[0], chunk_index=key[1], doc_title=r[4], heading=r[2], text=r[3], score=cos, doc_version=r[5])
    fused = hybrid.rrf([[(c.doc_id, c.chunk_index) for c in vec], [k for k, _ in lex]], [1.0, lexical_weight])
    order = sorted((k for k in fused if k in by_key), key=lambda k: (-fused[k], -by_key[k].score, k))
    return [by_key[k] for k in order[:top_k]]


def _cosines(db: Session, tenant_id: str, model_name: str, vector: list[float], keys: list[tuple[str, int]]) -> dict[tuple[str, int], float]:
    docs = sorted({k[0] for k in keys})
    want = set(keys)
    stmt = select(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.embedding_model == model_name, Chunk.doc_id.in_(docs))
    return {(c.doc_id, c.chunk_index): _cosine(vector, list(c.embedding)) for c in db.execute(stmt).scalars() if (c.doc_id, c.chunk_index) in want}


def count_chunks(db: Session, *, tenant_id: str, model_name: str) -> int:
    """How many searchable passages exist for this tenant and model. Zero means documents were never ingested
    (or were ingested with a different embedding model), which is a setup problem, not an unanswerable question."""
    stmt = select(func.count()).select_from(Chunk).where(
        Chunk.tenant_id == tenant_id, Chunk.audience =="public", Chunk.embedding_model == model_name)
    return int(db.execute(stmt).scalar_one())
