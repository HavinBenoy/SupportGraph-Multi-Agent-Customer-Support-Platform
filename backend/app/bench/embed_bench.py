"""Embedding benchmark: speed plus retrieval quality (hit@1 / hit@k on the synthetic questions) and the score gap that
decides SG_RAG_MIN_SCORE. Pure Python cosine over the in-memory chunks: no database needed."""

import math
import time
from typing import Callable

from app.bench import corpus
from app.bench.stats import rate, summarise
from app.config import Settings


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def suggest_min_score(answerable_top: list[float], abstain_top: list[float]) -> dict:
    """If answerable questions' top scores sit above the unanswerable ones, a threshold between them works. Otherwise say so."""
    if not answerable_top or not abstain_top:
        return {"separable": None, "note": "need both answerable and must-abstain questions"}
    lo_ans = sorted(answerable_top)[max(0, int(len(answerable_top) * 0.1) - 0)]   # ~p10 of answerable
    hi_abs = sorted(abstain_top)[min(len(abstain_top) - 1, int(len(abstain_top) * 0.9))]  # ~p90 of must-abstain
    if lo_ans > hi_abs:
        return {"separable": True, "suggested_min_score": round((lo_ans + hi_abs) / 2, 3),
                "answerable_p10": round(lo_ans, 3), "abstain_p90": round(hi_abs, 3)}
    return {"separable": False, "answerable_p10": round(lo_ans, 3), "abstain_p90": round(hi_abs, 3),
            "note": "score distributions overlap: a similarity threshold alone cannot separate them; keep the model-side abstention"}


def bench_embed(settings: Settings, model_name: str, factory: Callable[[Settings], object], *, tickets: list[dict], chunks,
                top_k: int = 4, limit: int | None = None) -> dict:
    s = settings.model_copy(update={"embedding_model": model_name})
    client = factory(s)
    t0 = time.perf_counter()
    doc_vecs: list[list[float]] = []
    per_batch: list[float] = []
    for i in range(0, len(chunks), 8):
        b0 = time.perf_counter()
        doc_vecs += client.embed_documents([c.embedding_input for c in chunks[i:i + 8]])
        per_batch.append((time.perf_counter() - b0) / len(chunks[i:i + 8]))
    index_s = time.perf_counter() - t0

    answerable, abstain = [], []
    rows, qlat = [], []
    for t in [t for t in tickets if t["expected_route"] == "knowledge"][: limit or None]:
        if not (t["expected_docs"] or t["must_abstain"]):
            continue
        q0 = time.perf_counter()
        qv = client.embed_query(t["message"])
        qlat.append(time.perf_counter() - q0)
        ranked = sorted(((_cos(qv, v), i) for i, v in enumerate(doc_vecs)), reverse=True)[:top_k]
        top = ranked[0][0]
        if t["must_abstain"]:
            abstain.append(top)
            continue
        got = [chunks[i].doc_id for _, i in ranked]
        answerable.append(top)
        rows.append({"id": t["id"], "top_score": round(top, 4), "hit_at_1": got[0] in t["expected_docs"],
                     "hit_at_k": any(d in t["expected_docs"] for d in got)})
    return {"suite": "embed", "model": model_name, "chunks": len(chunks), "dim": len(doc_vecs[0]) if doc_vecs else None,
            "index_total_s": round(index_s, 2), "per_chunk_s": summarise(per_batch), "query_s": summarise(qlat),
            "hit_at_1": rate(sum(r["hit_at_1"] for r in rows), len(rows)),
            f"hit_at_{top_k}": rate(sum(r["hit_at_k"] for r in rows), len(rows)), "answerable_n": len(rows),
            "score_gap": suggest_min_score(answerable, abstain), "rows": rows}
