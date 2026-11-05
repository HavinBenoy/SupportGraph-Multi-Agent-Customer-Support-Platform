"""Benchmark inputs from the repo only: tenant documents -> chunks, and the labelled synthetic tickets."""

import json
from dataclasses import dataclass
from pathlib import Path

from app.evals.run import DATASETS_DIR
from app.llm.lexical import tokens
from app.rag.chunking import chunk_markdown
from app.rag.documents import load_public_documents
from app.rag.evaluate import _norm
from app.rag.retrieval import RetrievedChunk

DEV = "tickets_qspiders_v1.jsonl"
HELDOUT = "tickets_qspiders_v2_heldout.jsonl"


@dataclass(frozen=True)
class BenchChunk:
    doc_id: str
    chunk_index: int
    doc_title: str
    heading: str
    text: str

    @property
    def embedding_input(self) -> str:
        return f"{self.heading}\n{self.text}"

    def as_retrieved(self, score: float = 0.0) -> RetrievedChunk:
        return RetrievedChunk(doc_id=self.doc_id, chunk_index=self.chunk_index, doc_title=self.doc_title,
                              heading=self.heading, text=self.text, score=score)


def load_chunks(tenant_id: str) -> list[BenchChunk]:
    out: list[BenchChunk] = []
    for d in load_public_documents(tenant_id):
        for c in chunk_markdown(d.body, doc_title=d.title):
            out.append(BenchChunk(d.doc_id, c.index, d.title, c.heading, c.text))
    return out


def load_tickets(name: str = DEV, tenant_id: str | None = None, path: Path | None = None) -> list[dict]:
    p = path or DATASETS_DIR / name
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    return [r for r in rows if tenant_id is None or r["tenant_id"] == tenant_id]


def knowledge_cases(tickets: list[dict]) -> list[dict]:
    """Questions the FAQ model should answer (with expected facts) or must refuse (must_abstain)."""
    return [t for t in tickets if t["expected_route"] == "knowledge" and (t["expected_facts"] or t["must_abstain"])]


def lexical_top_k(question: str, chunks: list[BenchChunk], k: int = 4) -> list[BenchChunk]:
    """Cheap offline retrieval so the LLM benchmark isolates the LLM: the same context goes to every model.
    (Embedding quality is measured separately by `bench embed`.)"""
    q = set(tokens(question))
    scored = sorted(((len(q & set(tokens(c.embedding_input))) / (1 + 0.02 * len(c.text.split())), i)
                     for i, c in enumerate(chunks)), reverse=True)
    return [chunks[i] for s, i in scored[:k] if s > 0]


SPLIT_FILES = {"dev": DEV, "heldout": HELDOUT}


def load_splits(tenant_id: str, names: list[str] | None = None) -> dict[str, list[dict]]:
    """The labelled tickets, kept apart: {"dev": [...], "heldout": [...]}. They are never merged in any report."""
    return {n: load_tickets(SPLIT_FILES[n], tenant_id) for n in (names or list(SPLIT_FILES))}


def context_check(case: dict, ctx: list[BenchChunk]) -> dict:
    """Is what the case needs actually in the context given to the model? Uses the same normaliser as the fact check.
    Only meaningful for answerable cases: a must-abstain case has nothing it needs."""
    if case["must_abstain"]:
        return {"sufficient": None, "facts_missing_from_context": []}
    text = _norm(" ".join(c.embedding_input for c in ctx))
    if case["expected_facts"]:
        missing = [f for f in case["expected_facts"] if _norm(f) not in text]
    else:  # no phrase to look for: fall back to "was a required document retrieved at all"
        have = {c.doc_id for c in ctx}
        missing = [d for d in case["expected_docs"] if d not in have]
    return {"sufficient": not missing, "facts_missing_from_context": missing}
