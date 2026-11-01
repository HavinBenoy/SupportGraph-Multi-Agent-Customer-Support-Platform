"""Deterministic, offline embedder using feature hashing ("the hashing trick").

Each word (and each adjacent word pair) is hashed into one of 768 slots, counted, and the vector is length-
normalised. Texts that share words get similar vectors, so cosine similarity behaves like a keyword-overlap score.

What this is for: tests, CI and the no-LLM demo. Identical input always gives identical output, in any process
(it uses md5, not Python's randomised hash()).
What this is NOT: a model of meaning. It cannot tell that "refund" and "money back" are related. Judge real
retrieval quality only with a real embedding model (SG_EMBEDDING_PROVIDER=ollama or openai_compat).
"""

import hashlib
import math
from collections import Counter

from langchain_core.embeddings import Embeddings

from app.llm.lexical import tokens

MODEL_NAME = "hashing-v1"
_BIGRAM_WEIGHT = 0.5


def _slot(feature: str, dim: int) -> tuple[int, float]:
    h = int.from_bytes(hashlib.md5(feature.encode("utf-8")).digest()[:8], "big")
    return h % dim, (1.0 if (h >> 40) & 1 else -1.0)  # signed hashing: collisions cancel instead of piling up


class HashingEmbeddings(Embeddings):
    def __init__(self, dim: int = 768):
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        toks = tokens(text)
        counts: Counter[str] = Counter(toks)
        weights = {t: 1.0 + math.log(c) for t, c in counts.items()}  # repeated words help, with diminishing returns
        for a, b in zip(toks, toks[1:]):
            weights[f"{a}_{b}"] = weights.get(f"{a}_{b}", 0.0) + _BIGRAM_WEIGHT
        vec = [0.0] * self.dim
        for feature, w in weights.items():
            i, sign = _slot(feature, self.dim)
            vec[i] += sign * w
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
