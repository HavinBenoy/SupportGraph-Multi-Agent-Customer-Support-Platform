"""Write each suite's result as JSON and merge all of them into one Markdown summary to paste back for review."""

import json
from datetime import datetime
from pathlib import Path

from app.bench.stats import system_info


def write_result(out_dir: Path, result: dict, *, settings_info: dict | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{result['suite']}-{result.get('model') or result.get('voice', 'x')}".replace(":", "_").replace("/", "_")
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "system": system_info(),
               "settings": settings_info or {}, **result}
    p = out_dir / f"{name}.json"
    p.write_text(json.dumps(payload, indent=2))
    return p


def _f(x, nd=2):
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _pct(x):
    return "-" if x is None else f"{x * 100:.0f}%"


def render_markdown(results: list[dict]) -> str:
    out = ["# Benchmark summary", ""]
    if results:
        s = results[0].get("system", {})
        out += [f"Machine: {s.get('cpu_model')} | {s.get('cpu_cores_physical')}C/{s.get('cpu_threads')}T | RAM {s.get('ram_gb')} GB "
                f"(free {s.get('ram_available_gb')}) | load before run {s.get('load_avg_1m')}", ""]
    faq = [r for r in results if r["suite"] == "faq"]
    if faq:
        out += ["## FAQ answering (fixed context, same prompts as the product)", "",
                "| model | answerable correct | over-abstain | bad citations | must-abstain correct | TTFT p50 (s) | total p50 (s) | total p95 (s) | tok/s p50 | cold start (s) | errors |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in faq:
            a, sp = r["answerable"], r["speed"]
            out.append(f"| {r['model']} | {_pct(a['correct_rate'])} (n={a['n']}) | {_pct(a['over_abstain_rate'])} | {_pct(a['unknown_citation_rate'])} | "
                       f"{_pct(r['must_abstain']['correct_rate'])} (n={r['must_abstain']['n']}) | {_f(sp['ttft_s'].get('p50'))} | "
                       f"{_f(sp['total_s'].get('p50'))} | {_f(sp['total_s'].get('p95'))} | {_f(sp['tokens_per_s'].get('p50'), 1)} | "
                       f"{_f((r.get('cold_start') or {}).get('total_s'))} | {r['errors']} |")
        out.append("")
    tri = [r for r in results if r["suite"] == "triage"]
    if tri:
        out += ["## Triage (structured output)", "", "| model | category accuracy | unusable output | latency p50 (s) | p95 (s) |", "|---|---|---|---|---|"]
        for r in tri:
            out.append(f"| {r['model']} | {_pct(r['category_accuracy'])} | {_pct(r['unusable_output_rate'])} | "
                       f"{_f(r['latency_s'].get('p50'))} | {_f(r['latency_s'].get('p95'))} |")
        out.append("")
    emb = [r for r in results if r["suite"] == "embed"]
    if emb:
        out += ["## Embeddings", "", "| model | dim | index (s) | query p50 (s) | hit@1 | hit@4 | score gap |", "|---|---|---|---|---|---|---|"]
        for r in emb:
            g = r["score_gap"]
            gap = (f"separable, suggest SG_RAG_MIN_SCORE~{g['suggested_min_score']}" if g.get("separable")
                   else "overlap: " + g.get("note", "n/a") if g.get("separable") is False else "n/a")
            out.append(f"| {r['model']} | {r['dim']} | {_f(r['index_total_s'], 1)} | {_f(r['query_s'].get('p50'), 3)} | "
                       f"{_pct(r['hit_at_1'])} | {_pct(r.get('hit_at_4'))} | {gap} |")
        out.append("")
    for r in (x for x in results if x["suite"] == "stt"):
        out += ["## Speech-to-text", "", f"{r['model']}: RTF p50 {_f(r['rtf'].get('p50'), 3)}, p95 {_f(r['rtf'].get('p95'), 3)} over {r['files']} files; load {r['model_load_s']} s", ""]
    for r in (x for x in results if x["suite"] == "tts"):
        out += ["## Text-to-speech", "", f"{r['voice']}: RTF p50 {_f(r['rtf'].get('p50'), 3)}, per-sentence p50 {_f(r['process_s'].get('p50'), 2)} s ({r['note']})", ""]
    out += ["---", "Speeds are measured on this machine only. Quality scores are on synthetic data with deterministic checks (no LLM judge).", ""]
    return "\n".join(out)


def load_results(dir_: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(dir_.glob("*.json"))]
