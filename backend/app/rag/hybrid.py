"""The keyword lane of retrieval, and the rank fusion that joins it to vector search.

Why a second lane. An embedding finds passages that MEAN something similar; it is weakest exactly where support questions are most specific: a person ("Jyothi"), a code
("PYF-101"), an amount ("55,000"), an abbreviation ("ML"). Those are matched far more reliably by the words themselves. A keyword lane (BM25) ranks passages by how many of the
question's rare words they contain; fusing the two rankings (reciprocal rank fusion) keeps what either lane is confident about and does not need their scores to be comparable.

BM25 is computed here, in Python, over the tenant's current public passages (hundreds, not millions): exact, dependency-free, identical on PostgreSQL and SQLite. Nothing is
written to the database, so nothing can drift out of date: the index IS the live `chunks` table. Tokenising reuses the stemmer the offline fakes use, so "returns/returned" match."""

import math
from collections import Counter

from app.llm.lexical import tokens

K1, B = 1.5, 0.75
RRF_K = 60


def bm25(docs: list[tuple[object, str]], query: str) -> list[tuple[object, float]]:
    """(key, score) for every document sharing at least one non-stopword token with the query, best first. Ties keep input order (deterministic)."""
    q = list(dict.fromkeys(tokens(query)))
    if not q or not docs:
        return []
    toks = [(key, Counter(tokens(text))) for key, text in docs]
    n = len(toks)
    avg = (sum(sum(c.values()) for _, c in toks) / n) or 1.0
    df = {t: sum(1 for _, c in toks if t in c) for t in q}
    out: list[tuple[object, float]] = []
    for key, c in toks:
        length = sum(c.values())
        score = 0.0
        for t in q:
            f = c.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * length / avg))
        if score > 0:
            out.append((key, score))
    out.sort(key=lambda kv: -kv[1])
    return out


def rrf(rankings: list[list[object]], weights: list[float] | None = None) -> dict[object, float]:
    """Reciprocal rank fusion: each item scores sum(weight / (RRF_K + rank)) over the lists it appears in (rank starts at 1)."""
    weights = weights or [1.0] * len(rankings)
    out: dict[object, float] = {}
    for w, ranking in zip(weights, rankings):
        for i, key in enumerate(ranking, start=1):
            out[key] = out.get(key, 0.0) + w / (RRF_K + i)
    return out
