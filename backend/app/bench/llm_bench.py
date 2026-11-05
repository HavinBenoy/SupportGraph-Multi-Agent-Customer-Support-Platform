"""Benchmarks for chat models: speed (cold load, time to first token, tokens/s) and quality on the repo's synthetic data.

Two suites, both using the SAME prompts and code paths the product uses (gateway model, FAQ prompt, LlmTriager):
  faq     - answer knowledge questions from fixed context; checks citations, expected facts, correct abstention
  triage  - structured-output classification; checks category accuracy and how often the output is unusable
Speed numbers come from the provider's own counters when present (Ollama reports eval_count / eval_duration), else are
derived from wall-clock time and labelled as such.
"""

import time
from typing import Callable

from langchain_core.messages import AIMessageChunk

from app.agents.schemas import low_confidence_triage
from app.agents.triage import LlmTriager
from app.bench import corpus
from app.bench.stats import rate, summarise
from app.config import Settings
from app.evals.outcomes import expected_triage_category
from app.llm.fake_faq import SENTINEL
from app.rag.evaluate import _norm
from app.rag.prompts import CITATION, build_messages

ModelFactory = Callable[[str, Settings], object]  # (role, settings) -> chat model, i.e. gateway.get_chat_model


def stream_once(model, messages) -> dict:
    """Run one streamed call; return timings (seconds) and token counts."""
    t0 = time.perf_counter()
    first = None
    agg: AIMessageChunk | None = None
    n_chunks = 0
    for chunk in model.stream(messages):
        if first is None and (chunk.content or ""):
            first = time.perf_counter() - t0
        agg = chunk if agg is None else agg + chunk
        n_chunks += 1
    total = time.perf_counter() - t0
    meta = getattr(agg, "response_metadata", {}) or {}
    usage = getattr(agg, "usage_metadata", None) or {}
    out_tokens = meta.get("eval_count") or usage.get("output_tokens")
    in_tokens = meta.get("prompt_eval_count") or usage.get("input_tokens")
    eval_ns = meta.get("eval_duration")
    if out_tokens and eval_ns:
        tps, tps_src = out_tokens / (eval_ns / 1e9), "provider"
    elif out_tokens and first is not None and total > first:
        tps, tps_src = out_tokens / (total - first), "wall-clock"
    else:
        tps, tps_src = None, "unavailable"
    return {"text": str(getattr(agg, "content", "") or ""), "ttft_s": first, "total_s": total, "out_tokens": out_tokens,
            "in_tokens": in_tokens, "tokens_per_s": tps, "tps_source": tps_src,
            "load_s": (meta.get("load_duration") or 0) / 1e9 if meta.get("load_duration") is not None else None,
            "prompt_eval_s": (meta.get("prompt_eval_duration") or 0) / 1e9 if meta.get("prompt_eval_duration") is not None else None}


def judge_faq_answer(case: dict, text: str, context_ids: set[str]) -> dict:
    """Deterministic checks (no LLM judge): the benchmark must not depend on a second model."""
    cited = list(dict.fromkeys(CITATION.findall(text)))
    abstained = (not text.strip()) or SENTINEL in text
    norm = _norm(text)
    r = {"abstained": abstained, "cited": bool(cited), "unknown_citation": any(c not in context_ids for c in cited)}
    if case["must_abstain"]:
        r["correct"] = abstained
    else:
        missing = [f for f in case["expected_facts"] if _norm(f) not in norm]
        r["facts_missing"] = missing
        r["correct"] = (not abstained) and bool(cited) and not r["unknown_citation"] and not missing
    return r


def bench_faq(settings: Settings, model_name: str, factory: ModelFactory, *, tickets: list[dict], chunks, limit: int | None = None,
              institute_name: str = "QSpiders", on_progress=None) -> dict:
    s = settings.model_copy(update={"llm_model": model_name})
    model = factory("faq", s)
    cases = corpus.knowledge_cases(tickets)[: limit or None]
    rows = []
    for i, case in enumerate(cases):
        ctx = corpus.lexical_top_k(case["message"], chunks, k=settings.rag_top_k)
        msgs = build_messages(case["message"], [c.as_retrieved() for c in ctx], institute_name=institute_name)
        try:
            m = stream_once(model, msgs)
        except Exception as e:  # noqa: BLE001 - a dead model must be reported, not crash the whole benchmark
            rows.append({"id": case["id"], "error": type(e).__name__})
            continue
        ids = {c.as_retrieved().source_id for c in ctx}
        rows.append({"id": case["id"], "must_abstain": case["must_abstain"], **{k: v for k, v in m.items() if k != "text"},
                     **judge_faq_answer(case, m["text"], ids)})
        if on_progress:
            on_progress(i + 1, len(cases))
    ok = [r for r in rows if "error" not in r]
    ans = [r for r in ok if not r["must_abstain"]]
    abst = [r for r in ok if r["must_abstain"]]
    return {
        "suite": "faq", "model": model_name, "cases": len(cases), "errors": len(rows) - len(ok),
        "answerable": {"n": len(ans), "correct_rate": rate(sum(r["correct"] for r in ans), len(ans)),
                       "over_abstain_rate": rate(sum(r["abstained"] for r in ans), len(ans)),
                       "unknown_citation_rate": rate(sum(r["unknown_citation"] for r in ans), len(ans))},
        "must_abstain": {"n": len(abst), "correct_rate": rate(sum(r["correct"] for r in abst), len(abst))},
        "speed": {"ttft_s": summarise([r["ttft_s"] for r in ok if r.get("ttft_s") is not None]),
                  "total_s": summarise([r["total_s"] for r in ok]),
                  "tokens_per_s": summarise([r["tokens_per_s"] for r in ok if r.get("tokens_per_s")]),
                  "out_tokens": summarise([r["out_tokens"] for r in ok if r.get("out_tokens")], 1),
                  "prompt_tokens": summarise([r["in_tokens"] for r in ok if r.get("in_tokens")], 1)},
        "rows": rows,
    }


def bench_triage(settings: Settings, model_name: str, factory: ModelFactory, *, tickets: list[dict], limit: int | None = None,
                 on_progress=None) -> dict:
    s = settings.model_copy(update={"llm_model": model_name})
    triager = LlmTriager(factory("triage", s))
    fallback = low_confidence_triage()
    rows = []
    items = [t for t in tickets if expected_triage_category(t)][: limit or None]
    for i, t in enumerate(items):
        t0 = time.perf_counter()
        out = triager.classify(t["message"])
        dt = time.perf_counter() - t0
        unusable = out == fallback  # provider error, invalid JSON or schema violation all land here
        want = expected_triage_category(t)
        rows.append({"id": t["id"], "expected": want, "got": out.category, "unusable": unusable, "total_s": dt,
                     "own_records_ok": out.wants_own_records == (t["student_id"] != "anonymous" and t["expected_route"] == "action")})
        if on_progress:
            on_progress(i + 1, len(items))
    return {"suite": "triage", "model": model_name, "cases": len(rows),
            "category_accuracy": rate(sum(r["expected"] == r["got"] for r in rows), len(rows)),
            "unusable_output_rate": rate(sum(r["unusable"] for r in rows), len(rows)),
            "latency_s": summarise([r["total_s"] for r in rows]), "rows": rows}


def cold_start(settings: Settings, model_name: str, factory: ModelFactory, unload: Callable[[str], None] | None) -> dict:
    """Unload (Ollama only), then time a tiny request: what the first user after idle would wait."""
    if unload:
        unload(model_name)
    s = settings.model_copy(update={"llm_model": model_name})
    from langchain_core.messages import HumanMessage

    try:
        m = stream_once(factory("faq", s), [HumanMessage("Reply with the single word: ready")])
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}
    return {"unloaded_first": bool(unload), "total_s": round(m["total_s"], 3), "load_s": m["load_s"]}


def ollama_unload(base_url: str) -> Callable[[str], None]:
    import httpx

    def _unload(model: str) -> None:
        try:
            httpx.post(f"{base_url.rstrip('/')}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
            time.sleep(1.0)
        except httpx.HTTPError:
            pass

    return _unload
