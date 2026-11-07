"""M3 latency benchmark: the SAME questions through the whole support graph (triage -> route -> knowledge -> qa -> finalize), with stage timings.

    make bench-voice                                   all scenarios, 5 runs each            (real Ollama + Postgres: `make db-up db-setup ingest`)
    make bench-voice ARGS="--cold"                     also measures a TRUE cold first call (unloads the Ollama models first)
    make bench-voice ARGS="--no-cache --label before"  honest per-call numbers with the M3 caches off
    make bench-voice ARGS="--n 20 --label after"       enough runs for P90/P95
    python scripts/bench_voice_latency.py --compare ../evals/bench-voice/before.json ../evals/bench-voice/after.json

Scenarios: cold_first (needs --cold), warm_faq_repeat, warm_faq_varied, unsupported, human_escalation, greeting, long_utterance, context_top_k_{1..4}.
Every run records wall time, node times, stage times (embed / search / context / llm / validate ...), Ollama counters (model load, prompt tokens
and speed, decode tokens and speed), the cold/warm verdict, cache hits and system CPU/memory. Output: a table on screen and a JSON file in
evals/bench-voice/. With fake providers the header says so: those numbers prove the plumbing, not the model.

It measures the graph path (everything between "the transcript is ready" and "the answer text exists"). Speech-to-text and Piper are measured
by `make bench-stt` / `make bench-tts`; the end-to-end voice numbers come from the `metrics` message of a real call (docs/M3.md).
It never changes data except one ticket row per run in the chat events table (the same rows a real chat writes) and uses a throw-away ticket per run.
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from app.bench.latency import compare, render, summarise_runs
from app.bench.stats import system_info
from app.config import get_settings
from app.graph.support import ask_support, build_support_graph
from app.tools.scope import ANONYMOUS

FAQ_REPEAT = "What is the fee for the Python Full Stack course?"
FAQ_VARIED = ["What courses do you offer?", "Do you provide placement support?", "Can I pay the fee in instalments?",
              "What are the eligibility requirements for the data science course?", "Do you offer demo classes?",
              "What is the attendance requirement?", "What are the batch timings?", "How long is the Java full stack course?"]
UNSUPPORTED = ["What is the capital of France?", "Who won the cricket world cup in 2011?", "How do I bake a chocolate cake?"]
HUMAN = ["I want to talk to a person", "Can I speak to a human agent please?"]
GREETING = ["Hello", "Hi there", "Thanks, that is all"]
LONG = ["Hi, I am a final year student and I am thinking about joining your Python full stack course, but I also want to know what the fee is, "
        "whether I can pay in instalments, and if there are any demo classes before I decide"]


def _cpu_times() -> tuple[int, int] | None:
    try:
        f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        return sum(f) - f[3] - f[4], sum(f)          # busy (not idle/iowait), total
    except (OSError, ValueError, IndexError):
        return None


def _mem_available_mb() -> float | None:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                return round(int(line.split()[1]) / 1024)
    except (OSError, ValueError):
        return None
    return None


def one_run(graph, tenant: str, text: str, scenario: str, i: int) -> dict:
    c0, m0 = _cpu_times(), _mem_available_mb()
    t0 = time.perf_counter()
    out = ask_support(graph, text, tenant_id=tenant, student_id=ANONYMOUS, ticket_id=f"bench-{uuid.uuid4().hex[:8]}")
    wall = round((time.perf_counter() - t0) * 1000)
    c1, m1 = _cpu_times(), _mem_available_mb()
    busy = round(100 * (c1[0] - c0[0]) / max(1, c1[1] - c0[1]), 1) if c0 and c1 else None
    return {"scenario": scenario, "i": i, "wall_ms": wall, "route": out.get("route"), "route_reason": out.get("route_reason"),
            "abstained": bool(out.get("abstained")), "citations": out.get("citations", []), "node_ms": out.get("node_ms") or {},
            "perf": out.get("perf") or {}, "cpu_busy_pct": busy, "mem_available_mb": min(x for x in (m0, m1) if x is not None) if m0 or m1 else None}


def unload_models(s) -> None:
    """True cold start: ask Ollama to drop the chat and embedding models from memory (keep_alive 0). Harmless: the next call reloads them."""
    import httpx

    from app.llm.preflight import chat_models_in_use
    for m in chat_models_in_use(s):
        httpx.post(f"{s.llm_base_url.rstrip('/')}/api/generate", json={"model": m, "keep_alive": 0}, timeout=60).raise_for_status()
    httpx.post(f"{s.embedding_base_url.rstrip('/')}/api/embed", json={"model": s.embedding_model, "input": "x", "keep_alive": 0}, timeout=60).raise_for_status()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=5, help="runs per scenario (>=10 for P90, >=20 for P95)")
    p.add_argument("--scenarios", default="", help="comma list; default all")
    p.add_argument("--tenant", default=get_settings().default_tenant)
    p.add_argument("--cold", action="store_true", help="unload the Ollama models and measure a true cold first call (scenario cold_first)")
    p.add_argument("--cold-runs", type=int, default=3)
    p.add_argument("--no-cache", action="store_true", help="switch the triage and answer caches off for this run")
    p.add_argument("--no-warmup", action="store_true", help="skip the warm-up pass before the scenarios")
    p.add_argument("--label", default="run")
    p.add_argument("--out", default="")
    p.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    a = p.parse_args()
    if a.compare:
        b, af = (json.loads(Path(x).read_text()) for x in a.compare)
        print(compare(b, af))
        return 0

    s = get_settings()
    if a.no_cache:
        s = s.model_copy(update={"answer_cache_size": 0, "triage_cache_size": 0})
    fake = s.llm_provider == "fake" or s.embedding_provider == "fake"
    if fake:
        print("NOTE: fake model providers are configured: this run checks the plumbing only (numbers are NOT model performance).", file=sys.stderr)
    snapshot = {k: getattr(s, k) for k in ("llm_provider", "llm_model", "llm_model_triage", "llm_model_faq", "llm_num_ctx", "llm_num_thread", "llm_max_output_tokens",
                                           "embedding_model", "rag_top_k", "rag_context_band", "rag_max_context_chars", "triage_prompt",
                                           "triage_max_output_tokens", "triage_cache_size", "answer_cache_size", "llm_keep_alive")}

    if not a.no_warmup and not a.cold:
        from app.llm.warmup import warm_llm
        print("warm-up:", warm_llm(s))

    from app.observability.stages import mark_warm
    mark_warm(not a.no_warmup and not a.cold)
    graph = build_support_graph(settings=s)

    def run(name: str, texts: list[str], n: int, g=None, before=None) -> list[dict]:
        runs = []
        for i in range(n):
            if before:
                before()
            runs.append(one_run(g or graph, a.tenant, texts[i % len(texts)], name, i))
            print(f"  {name} #{i}: {runs[-1]['wall_ms']} ms  route={runs[-1]['route']}  cold={runs[-1]['perf'].get('cold')}", file=sys.stderr)
        return runs

    plan: dict[str, callable] = {}
    if a.cold:
        plan["cold_first"] = lambda: run("cold_first", [FAQ_REPEAT], a.cold_runs, before=lambda: unload_models(s))
    plan["warm_faq_repeat"] = lambda: run("warm_faq_repeat", [FAQ_REPEAT], a.n)
    plan["warm_faq_varied"] = lambda: run("warm_faq_varied", FAQ_VARIED, a.n)
    plan["unsupported"] = lambda: run("unsupported", UNSUPPORTED, a.n)
    plan["human_escalation"] = lambda: run("human_escalation", HUMAN, a.n)
    plan["greeting"] = lambda: run("greeting", GREETING, a.n)
    plan["long_utterance"] = lambda: run("long_utterance", LONG, a.n)
    for k in (1, 2, 3, 4):
        def ctx(k=k):
            sk = s.model_copy(update={"rag_top_k": k})
            return run(f"context_top_k_{k}", [FAQ_REPEAT, *FAQ_VARIED[:3]], a.n, g=build_support_graph(settings=sk))
        plan[f"context_top_k_{k}"] = ctx
    wanted = [x.strip() for x in a.scenarios.split(",") if x.strip()] or list(plan)
    unknown = [w for w in wanted if w not in plan]
    if unknown:
        print(f"unknown scenario(s): {unknown}. Available: {list(plan)}", file=sys.stderr)
        return 2

    report = {"meta": {"label": a.label, "when": datetime.now().isoformat(timespec="seconds"), "fake": fake, "settings": snapshot, "system": system_info(),
                       "n": a.n, "cold": a.cold, "caches": "off" if a.no_cache else "as configured"}, "scenarios": {}}
    for name in wanted:
        print(f"-- {name}", file=sys.stderr)
        runs = plan[name]()
        report["scenarios"][name] = {"runs": runs, "summary": summarise_runs(runs)}
    print(render(report))
    out = Path(a.out or f"../evals/bench-voice/{a.label}-{datetime.now():%Y%m%d-%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
