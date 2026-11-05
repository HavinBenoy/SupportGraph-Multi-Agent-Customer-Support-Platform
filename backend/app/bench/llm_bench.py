"""Benchmarks for chat models: speed (cold load, time to first token, tokens/s) and quality on the repo's synthetic data.

Two suites, both using the SAME prompts and code paths the product uses (gateway model, FAQ prompt, LlmTriager):
  faq     - answer knowledge questions from fixed context; checks citations, expected facts, correct abstention
  triage  - structured-output classification; route accuracy through the real route node, false/missed escalations

Every FAQ row keeps the full response text, `done_reason` and generation metadata (the questions and documents are
synthetic, so this is safe), so any failure can be inspected afterwards. A reply stopped by the token limit
(done_reason == "length") is a FAILURE. Results are always computed per dataset split (dev, heldout), never merged.
Speed numbers come from the provider's own counters when present (Ollama reports eval_count / eval_duration), else are
derived from wall-clock time and labelled as such.
"""

import time
from typing import Callable

from langchain_core.messages import AIMessageChunk

from app.agents.triage import LlmTriager
from app.bench import corpus, metrics
from app.bench.metrics import judge_faq_answer  # noqa: F401  (re-exported: older callers import it from here)
from app.bench.stats import summarise
from app.config import Settings
from app.evals.outcomes import expected_triage_category
from app.rag.prompts import build_messages

ModelFactory = Callable[[str, Settings], object]  # (role, settings) -> chat model, i.e. gateway.get_chat_model

_GEN_ATTRS = ("model", "temperature", "num_ctx", "num_predict", "top_p", "top_k", "repeat_penalty", "keep_alive", "reasoning",
              "seed", "max_tokens", "request_timeout", "base_url")


def describe_chat_model(model) -> dict:
    """The generation options the client object was built with (what is sent to the provider). Never includes keys."""
    out = {"class": type(model).__name__}
    for a in _GEN_ATTRS:
        v = getattr(model, a, None)
        if v is not None:
            out[a] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return out


def _secs(ns) -> float | None:
    return None if ns is None else ns / 1e9


def stream_once(model, messages) -> dict:
    """Run one streamed call; return the text, why it stopped, timings (seconds), token counts and provider metadata."""
    t0 = time.perf_counter()
    first = None
    agg: AIMessageChunk | None = None
    for chunk in model.stream(messages):
        if first is None and (chunk.content or ""):
            first = time.perf_counter() - t0
        agg = chunk if agg is None else agg + chunk
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
    done_reason = meta.get("done_reason") or meta.get("finish_reason")  # Ollama / OpenAI-compatible
    return {"text": str(getattr(agg, "content", "") or ""), "done_reason": done_reason, "truncated": done_reason == "length",
            "ttft_s": first, "total_s": total, "out_tokens": out_tokens, "in_tokens": in_tokens, "tokens_per_s": tps,
            "tps_source": tps_src, "model_reported": meta.get("model"),
            "load_s": _secs(meta.get("load_duration")), "prompt_eval_s": _secs(meta.get("prompt_eval_duration")),
            "eval_s": _secs(eval_ns), "provider_total_s": _secs(meta.get("total_duration"))}


def _speed(rows: list[dict]) -> dict:
    clean = [r for r in rows if not r["truncated"]]
    return {"ttft_s": summarise([r["ttft_s"] for r in rows if r.get("ttft_s") is not None]),
            "total_s": summarise([r["total_s"] for r in rows]),
            "total_s_excluding_truncated": summarise([r["total_s"] for r in clean]),
            "tokens_per_s": summarise([r["tokens_per_s"] for r in rows if r.get("tokens_per_s")]),
            "out_tokens": summarise([r["out_tokens"] for r in rows if r.get("out_tokens")], 1),
            "prompt_tokens": summarise([r["in_tokens"] for r in rows if r.get("in_tokens")], 1),
            "truncated": sum(r["truncated"] for r in rows)}


def bench_faq(settings: Settings, model_name: str, factory: ModelFactory, *, splits: dict[str, list[dict]], chunks,
              limit: int | None = None, institute_name: str = "QSpiders", on_progress=None) -> dict:
    """`splits` maps a split name to its tickets. `limit` applies to each split. Context is the same lexical top-k for every model."""
    s = settings.model_copy(update={"llm_model": model_name})
    model = factory("faq", s)
    rows: list[dict] = []
    summaries: dict[str, dict] = {}
    for split, tickets in splits.items():
        cases = corpus.knowledge_cases(tickets)[: limit or None]
        split_rows: list[dict] = []
        for i, case in enumerate(cases):
            ctx = corpus.lexical_top_k(case["message"], chunks, k=settings.rag_top_k)
            msgs = build_messages(case["message"], [c.as_retrieved() for c in ctx], institute_name=institute_name)
            base = {"id": case["id"], "split": split}
            try:
                m = stream_once(model, msgs)
            except Exception as e:  # noqa: BLE001 - a dead model must be reported, not crash the whole benchmark
                split_rows.append({**base, "error": type(e).__name__})
                continue
            ids = {c.as_retrieved().source_id for c in ctx}
            judged = metrics.judge_faq_answer(case, m["text"], ids, m["done_reason"])
            check = corpus.context_check(case, ctx)
            label = metrics.label_faq_row(case, judged, check["sufficient"])
            split_rows.append({**base, "question": case["message"], "must_abstain": case["must_abstain"],
                               "expected_facts": case["expected_facts"], "expected_docs": case["expected_docs"],
                               "context_ids": sorted(ids), "context_sufficient": check["sufficient"],
                               "facts_missing_from_context": check["facts_missing_from_context"],
                               **m, **judged, "label": label, "failure_class": metrics.FAILURE_CLASS[label]})
            if on_progress:
                on_progress(i + 1, len(cases))
        rows += split_rows
        summaries[split] = metrics.summarise_faq(split_rows, _speed)
    return {"suite": "faq", "model": model_name, "generation": describe_chat_model(model), "context_mode": "lexical_top_k",
            "splits": summaries, "rows": rows}


def bench_triage(settings: Settings, model_name: str, factory: ModelFactory, *, splits: dict[str, list[dict]],
                 limit: int | None = None, on_progress=None, triager=None, route_fn=None) -> dict:
    """Triage every ticket in each split. Routes come from the support graph's real route node (decide_route + escalation
    floor) using your SG_TRIAGE_MIN_CONFIDENCE. `triager` / `route_fn` can be injected for tests."""
    s = settings.model_copy(update={"llm_model": model_name})
    gen = {}
    if triager is None:
        model = factory("triage", s)
        gen = describe_chat_model(model)
        triager = LlmTriager(model)
    route_fn = route_fn or metrics.get_route_fn(settings)
    rows: list[dict] = []
    summaries: dict[str, dict] = {}
    for split, tickets in splits.items():
        items = tickets[: limit or None]
        split_rows = []
        for i, t in enumerate(items):
            t0 = time.perf_counter()
            out = triager.classify(t["message"])
            dt = time.perf_counter() - t0
            route = metrics.real_route(route_fn, t, out, t["tenant_id"])
            split_rows.append(metrics.triage_row(t, split, out, route, expected_triage_category(t), dt))
            if on_progress:
                on_progress(i + 1, len(items))
        rows += split_rows
        summaries[split] = metrics.summarise_triage(split_rows, settings.triage_min_confidence, summarise, summarise)
    return {"suite": "triage", "model": model_name, "generation": gen, "splits": summaries, "rows": rows}


def cold_start(settings: Settings, model_name: str, factory: ModelFactory, unload: Callable[[str], None] | None) -> dict:
    """Unload (Ollama only), then time a tiny request: what the first user after idle would wait. Keeps the text and
    done_reason: a one-word request that produces hundreds of tokens is a finding in itself."""
    if unload:
        unload(model_name)
    s = settings.model_copy(update={"llm_model": model_name})
    from langchain_core.messages import HumanMessage

    try:
        m = stream_once(factory("faq", s), [HumanMessage("Reply with the single word: ready")])
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}
    return {"unloaded_first": bool(unload), "total_s": round(m["total_s"], 3), "load_s": m["load_s"], "text": m["text"],
            "done_reason": m["done_reason"], "truncated": m["truncated"], "out_tokens": m["out_tokens"]}


def ollama_unload(base_url: str) -> Callable[[str], None]:
    import httpx

    def _unload(model: str) -> None:
        try:
            httpx.post(f"{base_url.rstrip('/')}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
            time.sleep(1.0)
        except httpx.HTTPError:
            pass

    return _unload
