"""Validated-answer cache for repeated FAQ questions (M3).

WHAT IT STORES: the finished result of the knowledge agent for one question: the answer text, the passages it cited, and the search queries.
WHEN IT IS USED (all must hold; anything else takes the normal path):
  * the caller is anonymous (default `answer_cache_anonymous_only`) - no signed-in student's context ever reaches the cache;
  * the route was `knowledge`, so the answer came only from the tenant's PUBLIC documents;
  * the result was a fully validated answer: not abstained, reason "answered", at least one citation, every citation among the stored passages.
    Abstentions, truncated/uncited/declined answers, model-unavailable fallbacks and anything produced after an error are NEVER stored.
HOW IT IS KEYED: tenant + normalised question text + a fingerprint of the tenant's ingested documents (ids, content hashes, passage count for
  the embedding model) + a fingerprint of every setting that changes what an answer would be (models, retrieval and prompt-size settings).
HOW IT STAYS HONEST: the document fingerprint is recomputed from the database on EVERY lookup, so re-ingesting a changed or removed document
  changes the key and the old answer can no longer be found; entries also expire (`answer_cache_ttl_seconds`) and the cache is bounded (LRU).
  On a hit the stored answer is validated AGAIN with the same citation rule the FAQ graph uses; a failing entry is dropped and the normal path runs.
Everything above runs before the answer is spoken, exactly like an uncached answer: the QA node and finalize still see it.
"""

import hashlib
import re
import threading
import time
from collections import OrderedDict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Chunk, Document
from app.llm.fake_faq import SENTINEL
from app.rag.prompts import CITATION

_SPACES = re.compile(r"\s+")
_TRAIL = re.compile(r"[\s.?!,;:]+$")


def normalise_question(text: str) -> str:
    """Case, spacing and trailing punctuation do not change a question; everything else (including numbers and course names) does."""
    return _TRAIL.sub("", _SPACES.sub(" ", (text or "").strip().lower()))


def kb_fingerprint(db: Session, tenant_id: str, embedding_name: str) -> str:
    docs = db.execute(select(Document.doc_id, Document.content_hash).where(Document.tenant_id == tenant_id).order_by(Document.doc_id)).all()
    n_chunks = db.execute(select(func.count()).select_from(Chunk).where(
        Chunk.tenant_id == tenant_id, Chunk.audience == "public", Chunk.embedding_model == embedding_name)).scalar_one()
    h = hashlib.sha1()
    for doc_id, content_hash in docs:
        h.update(f"{doc_id}:{content_hash};".encode())
    return f"{len(docs)}.{int(n_chunks)}.{h.hexdigest()[:16]}"


def settings_fingerprint(s: Settings, embedding_name: str) -> str:
    parts = (s.llm_provider, s.llm_model, s.llm_model_faq, s.llm_temperature, s.llm_seed, s.llm_num_ctx, s.llm_max_output_tokens,
             embedding_name, s.rag_top_k, s.rag_min_score, s.rag_context_band, s.rag_max_context_chars, s.faq_citation_retries,
             s.knowledge_max_attempts, s.rewriter_mode)
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def make_key(tenant_id: str, question: str, kb_fp: str, settings_fp: str) -> str:
    return f"{tenant_id}|{kb_fp}|{settings_fp}|{normalise_question(question)}"


def storable(out: dict) -> bool:
    """Only a fully validated, cited answer from the knowledge agent may be stored."""
    r = out.get("result") or {}
    return bool(r.get("answer") and not r.get("abstained") and r.get("reason") == "answered" and _valid(r))


def _valid(r: dict) -> bool:
    """The FAQ graph's own acceptance rule, applied again: cites at least one source, every cited source was retrieved, no refusal sentinel."""
    answer = r.get("answer") or ""
    retrieved = {c.get("source_id") for c in r.get("chunks", [])}
    cited = list(dict.fromkeys(CITATION.findall(answer)))
    return bool(cited) and SENTINEL not in answer and all(c in retrieved for c in cited) and list(r.get("citations", [])) == cited


class AnswerCache:
    """Bounded, expiring, thread-safe. Values are deep-enough copies so a caller can never mutate a stored entry."""

    def __init__(self, size: int, ttl: float) -> None:
        self.size, self.ttl = size, ttl
        self._d: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = self.rejected = 0

    @property
    def enabled(self) -> bool:
        return self.size > 0

    def get(self, key: str) -> dict | None:
        with self._lock:
            hit = self._d.get(key)
            if hit is not None and time.monotonic() - hit[0] > self.ttl:
                del self._d[key]
                hit = None
            if hit is None:
                self.misses += 1
                return None
            if not _valid(hit[1].get("result") or {}):  # defence in depth: never serve an entry that fails the citation rule
                del self._d[key]
                self.rejected += 1
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return _copy(hit[1])

    def put(self, key: str, out: dict) -> bool:
        if not self.enabled or not storable(out):
            return False
        with self._lock:
            self._d[key] = (time.monotonic(), _copy(out))
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)
        return True

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def __len__(self) -> int:
        return len(self._d)


def _copy(out: dict) -> dict:
    r = out["result"]
    return {"queries": list(out.get("queries", [])),
            "result": {**r, "citations": list(r.get("citations", [])), "chunks": [dict(c) for c in r.get("chunks", [])]}}
