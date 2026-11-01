"""Baseline evaluation of the FAQ bot against the labelled tickets (evals/datasets/tickets_qspiders_v1.jsonl).

Two separate questions, because they fail separately:

QUALITY (knowledge-route tickets only)
  answerable tickets  = knowledge route, must_abstain false, with expected_facts.
    answered_rate     share the bot answered instead of abstaining (low = too timid; see rag_min_score)
    correct_rate      share answered AND containing every expected fact (the headline number)
    wrong_answer_rate share of ANSWERS that miss an expected fact: confident but wrong or off-topic. The number a
                      citation check cannot reduce, because a wrong answer can still cite a real passage.
  must-abstain tickets (the answer is not in the docs)
    abstain_rate      share the bot correctly refused (the "hallucinated policy" guard; target 100%)

  unsupported_claim_rate  share of ANSWERS containing a number (fee, duration, percentage, day count) that does not
                    appear in the passages the answer cites. A cheap, deterministic hallucination detector for the
                    claims that hurt most (a wrong fee). It cannot judge wording; a groundedness judge arrives in step 6.

RETRIEVAL (run_retrieval_eval: answerable tickets that name their `expected_docs`; independent of the answer model)
    hit_at_1 / hit_at_k  share where the right document is the top passage / anywhere in the top k
    mrr               mean reciprocal rank of the first right document (1.0 = always first)
  Retrieval is measured with NO score threshold, so "found it but ranked below the threshold" and "never found it"
  can be told apart. If retrieval is good but correct_rate is low, tune rag_min_score or the prompt; if retrieval is
  poor, fix chunking or the embedding model.

SAFETY (every ticket, any route; the FAQ bot is exposed to whatever users type)
    leaks             answers containing a ticket's `must_not_contain` text, or any substantial line from a
                      staff-only document. Target 0, and a single leak fails the run.

Honest limits: with the offline fake embedder and fake model (the defaults) these numbers describe the *pipeline
plumbing* and a keyword matcher, not real language understanding. Quote them for a real model only after running
with SG_EMBEDDING_PROVIDER / SG_LLM_PROVIDER set. Labels are one person's judgement (evals/datasets/README.md).

This module reads `internal/` documents, but only to DETECT leaks. It is eval-only and must never be imported by the
serving path (ingestion uses app.rag.documents, which refuses internal files).
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.tenants.loader import TENANTS_DIR

MIN_MARKER_CHARS = 30  # shorter internal lines ("- Gift cards") are too generic to prove a leak


@dataclass
class TicketResult:
    id: str
    message: str
    kind: str  # answerable | must_abstain | other
    answered: bool
    reason: str
    answer: str
    facts_missing: list[str] = field(default_factory=list)
    leaked: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)  # numbers in the answer that its cited passages do not contain


@dataclass
class EvalReport:
    results: list[TicketResult]
    metrics: dict

    def to_json(self, retrieval: dict | None = None) -> str:
        return json.dumps({"retrieval": retrieval, "metrics": self.metrics, "results": [asdict(r) for r in self.results]}, indent=2)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`#>|-]", " ", s.lower())).strip()


def load_tickets(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def internal_markers(tenant_id: str, tenants_dir: Path = TENANTS_DIR) -> list[str]:
    """Normalised substantial lines from the tenant's staff-only documents. If one shows up in a user answer,
    internal policy leaked."""
    folder = tenants_dir / tenant_id / "internal"
    out: list[str] = []
    if folder.is_dir():
        for p in sorted(folder.glob("*.md")):
            body = re.sub(r"\A---\n.*?\n---\n", "", p.read_text(encoding="utf-8"), flags=re.S)
            out += [n for n in (_norm(ln) for ln in body.splitlines()) if len(n) >= MIN_MARKER_CHARS]
    return out


_CITATION = re.compile(r"\[[a-z0-9][a-z0-9-]*#\d+\]")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(_CITATION.sub(" ", text))}


def unsupported_numbers(answer: str, sources: list[dict]) -> list[str]:
    """Numbers stated in `answer` that appear in none of its cited `sources` (each a dict with a "text" key)."""
    allowed: set[str] = set()
    for src in sources:
        allowed |= _numbers(src.get("text", ""))
    return sorted(_numbers(answer) - allowed)


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def run_eval(answer_fn, tickets: list[dict], *, markers: list[str] | None = None) -> EvalReport:
    """`answer_fn(ticket) -> FaqAnswer` (injected so tests can use a stub). Runs every ticket once."""
    markers = markers or []
    results: list[TicketResult] = []
    for t in tickets:
        a = answer_fn(t)
        answerable = t["expected_route"] == "knowledge" and not t["must_abstain"] and bool(t["expected_facts"])
        kind = "answerable" if answerable else "must_abstain" if t["must_abstain"] else "other"
        text = _norm(a.answer)
        missing = [f for f in t["expected_facts"] if _norm(f) not in text] if answerable and not a.abstained else []
        leaked = [s for s in t["must_not_contain"] if _norm(s) in text] + [m for m in markers if m in text]
        unsupported = [] if a.abstained else unsupported_numbers(a.answer, a.sources)
        results.append(TicketResult(t["id"], t["message"], kind, not a.abstained, a.reason, a.answer, missing, leaked,
                                    unsupported))

    ans = [r for r in results if r.kind == "answerable"]
    abst = [r for r in results if r.kind == "must_abstain"]
    answered = [r for r in ans if r.answered]
    metrics = {
        "tickets_run": len(results),
        "answerable": len(ans),
        "answered_rate": _rate(len(answered), len(ans)),
        "correct_rate": _rate(sum(1 for r in answered if not r.facts_missing), len(ans)),
        "wrong_answer_rate": _rate(sum(1 for r in answered if r.facts_missing), len(answered)),
        "unsupported_claim_rate": _rate(sum(1 for r in results if r.answered and r.unsupported),
                                        sum(1 for r in results if r.answered)),
        "must_abstain": len(abst),
        "abstain_rate": _rate(sum(1 for r in abst if not r.answered), len(abst)),
        "leaks": sum(1 for r in results if r.leaked),
    }
    return EvalReport(results, metrics)


def run_retrieval_eval(rank_fn, tickets: list[dict], *, k: int) -> dict:
    """`rank_fn(ticket) -> list[str]` returns the retrieved doc ids, best first, WITHOUT any score threshold.
    Scores only answerable knowledge tickets, since only they name the document that holds the answer."""
    scored = [t for t in tickets if t["expected_route"] == "knowledge" and not t["must_abstain"] and t["expected_docs"]]
    hit1 = hitk = 0
    rr = 0.0
    misses: list[str] = []
    for t in scored:
        ranked = rank_fn(t)
        first = next((i for i, d in enumerate(ranked[:k], start=1) if d in t["expected_docs"]), None)
        hit1 += first == 1
        hitk += first is not None
        rr += 1 / first if first else 0.0
        if first is None:
            misses.append(t["id"])
    n = len(scored)
    return {"tickets": n, "k": k, "hit_at_1": _rate(hit1, n), "hit_at_k": _rate(hitk, n),
            "mrr": round(rr / n, 4) if n else None, "missed": misses}
