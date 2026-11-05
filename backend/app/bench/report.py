"""Write each suite's result as JSON and merge all of them into one Markdown summary to paste back for review.

Result files carry `schema_version` and a run manifest (command, dataset split, SHA-256 of datasets / documents / prompts,
effective settings, Ollama version and model digests). Files from an older schema are refused, never silently mixed in.
"""

import importlib.metadata as md
import json
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from app.bench import corpus
from app.bench.stats import sha256_bytes, sha256_file, system_info
from app.config import Settings
from app.evals.run import DATASETS_DIR

SCHEMA_VERSION = 2

_SETTING_KEYS = ("llm_provider", "llm_model", "embedding_provider", "embedding_model", "llm_num_ctx", "llm_max_output_tokens",
                 "llm_thinking", "llm_keep_alive", "llm_timeout_seconds", "rag_top_k", "rag_min_score", "triage_min_confidence",
                 "embedding_query_prefix", "embedding_document_prefix", "default_tenant")
_ROLE_KEYS = ("action", "faq", "triage", "rewrite", "proposer", "critic", "reviser", "judge")
_PKGS = ("langchain-core", "langchain-ollama", "langgraph", "pydantic", "httpx")


# Everything that decides what is measured or how it is scored. A change to any of these makes older numbers incomparable.
BENCH_CODE_FILES = ("app/bench/metrics.py", "app/bench/llm_bench.py", "app/bench/corpus.py", "app/bench/report.py",
                    "app/bench/stats.py", "app/bench/embed_bench.py", "scripts/bench.py")


class IncompatibleResults(Exception):
    """Result files from an older benchmark version are present."""


def ollama_info(base_url: str, models: list[str]) -> dict:
    """Best effort: server version and the digest of each model tag, so 'which exact qwen3:4b' is on record."""
    try:
        import httpx

        base = base_url.rstrip("/")
        out: dict = {"version": httpx.get(f"{base}/api/version", timeout=5).json().get("version"), "models": {}}
        tags = httpx.get(f"{base}/api/tags", timeout=5).json().get("models", [])
        for m in tags:
            name = m.get("name") or m.get("model")
            if any(name == w or name == f"{w}:latest" for w in models):
                out["models"][name] = {"digest": m.get("digest"), "size_bytes": m.get("size"),
                                       "details": m.get("details")}
        return out
    except Exception as e:  # noqa: BLE001 - the manifest must never stop a benchmark
        return {"error": type(e).__name__}


def build_manifest(settings: Settings, *, argv: list[str], run_id: str | None = None, splits: list[str], models: list[str],
                   limit: int | None, context_mode: str = "lexical_top_k", ollama: dict | None = None) -> dict:
    from app.agents.triage import SYSTEM as TRIAGE_SYSTEM
    from app.rag.documents import load_public_documents
    from app.rag.prompts import SYSTEM_TEMPLATE

    docs = {d.doc_id: d.content_hash for d in load_public_documents(settings.default_tenant)}
    bench_dir = Path(__file__).resolve().parent
    backend_dir = bench_dir.parent.parent
    pkgs = {}
    for p in _PKGS:
        try:
            pkgs[p] = md.version(p)
        except md.PackageNotFoundError:
            pass
    return {
        "run_id": run_id or uuid.uuid4().hex[:8], "schema_version": SCHEMA_VERSION,
        "command": argv, "splits": splits, "models": models, "limit_per_split": limit, "context_mode": context_mode,
        "dataset_sha256": {n: sha256_file(DATASETS_DIR / f) for n, f in corpus.SPLIT_FILES.items()},
        "tenant_docs_sha256": {"combined": sha256_bytes("".join(f"{k}:{v}" for k, v in sorted(docs.items())).encode()), "files": docs},
        "prompt_sha256": {"faq_system": sha256_bytes(SYSTEM_TEMPLATE.encode()), "triage_system": sha256_bytes(TRIAGE_SYSTEM.encode())},
        "bench_code_sha256": {f: sha256_file(backend_dir / f) for f in BENCH_CODE_FILES},
        "settings": {k: getattr(settings, k) for k in _SETTING_KEYS},
        "role_models": {r: getattr(settings, f"llm_model_{r}") for r in _ROLE_KEYS if getattr(settings, f"llm_model_{r}")},
        "endpoint_host": urlparse(settings.llm_base_url).netloc, "ollama": ollama or {}, "packages": pkgs,
    }


def write_result(out_dir: Path, result: dict, *, manifest: dict | None = None, settings_info: dict | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{result['suite']}-{result.get('model') or result.get('voice', 'x')}".replace(":", "_").replace("/", "_")
    payload = {"schema_version": SCHEMA_VERSION, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "system": system_info(), "manifest": manifest or {}, "settings": settings_info or {}, **result}
    p = out_dir / f"{name}.json"
    p.write_text(json.dumps(payload, indent=2))
    return p


def load_results(dir_: Path) -> list[dict]:
    """All result files, or IncompatibleResults naming every file that is not the current schema."""
    loaded = [(p, json.loads(p.read_text())) for p in sorted(dir_.glob("*.json"))]
    old = [p.name for p, r in loaded if r.get("schema_version") != SCHEMA_VERSION]
    if old:
        raise IncompatibleResults(
            f"{len(old)} result file(s) in {dir_} are from an older benchmark version (schema {SCHEMA_VERSION} needed) and are not "
            f"comparable: {', '.join(old)}.\nMove them aside and re-run the benchmarks:\n"
            f"  mkdir -p {dir_}/archive && mv {' '.join(str(dir_ / n) for n in old)} {dir_}/archive/")
    return [r for _, r in loaded]


_HARDWARE_KEYS = ("cpu_model", "cpu_cores_physical", "cpu_threads", "ram_gb", "machine")
_SHORT = 14  # characters of a hash / digest shown in warnings


def _tag(r: dict) -> str:
    return f"{r['suite']}:{r.get('model') or r.get('voice', '?')}"


def _show(v) -> str:
    s = "(none)" if v is None or v == "" else str(v)
    return s[:_SHORT] if len(s) > 40 else s


def _differs(results: list[dict], get) -> dict[str, list[str]]:
    """value -> result tags, only when at least two different values exist among the results that have one at all."""
    seen: dict[str, list[str]] = {}
    for r in results:
        seen.setdefault(json.dumps(get(r), sort_keys=True, default=str), []).append(_tag(r))
    return seen if len(seen) > 1 else {}


def _fmt(label: str, seen: dict[str, list[str]], unwrap=lambda x: x) -> str:
    parts = [f"{_show(unwrap(json.loads(v)))} [{', '.join(tags)}]" for v, tags in seen.items()]
    return f"{label}: " + " vs ".join(parts)


def manifest_conflict_details(results: list[dict]) -> list[dict]:
    """Differences between result files that make them unfit to compare, each as
    {"group": model|config|machine, "label": ..., "detail": ...}. Missing values count as 'different' only when another file
    has one, so files from one run never conflict."""
    out: list[dict] = []
    mres = [r for r in results if r.get("manifest")]
    suites = [r for r in mres if r["suite"] in ("faq", "triage", "embed")]

    def add(group, label, seen, unwrap=lambda x: x):
        if seen:
            out.append({"group": group, "label": label, "detail": _fmt(label, seen, unwrap)})

    # --- model / runtime versions: results are about different things ---
    add("model", "Ollama version", _differs(mres, lambda r: (r["manifest"].get("ollama") or {}).get("version")))
    digests: dict[str, dict[str, list[str]]] = {}
    for r in mres:
        for name, info in ((r["manifest"].get("ollama") or {}).get("models") or {}).items():
            digests.setdefault(name, {}).setdefault(str((info or {}).get("digest")), []).append(_tag(r))
    for name, by_digest in sorted(digests.items()):
        if len(by_digest) > 1:
            out.append({"group": "model", "label": f"model digest for {name}",
                        "detail": f"model digest for {name} (the same tag is a different build): " +
                                  " vs ".join(f"{d[:_SHORT]} [{', '.join(tags)}]" for d, tags in by_digest.items())})
    # model tags that are present in some files and absent in the digest record are not an error; only differences count.
    for field, label in (("settings", "effective setting"), ("role_models", "role-specific model override")):
        keys = sorted({k for r in mres for k in (r["manifest"].get(field) or {})})
        for k in keys:
            add("model", f"{label} {k}", _differs(mres, lambda r, k=k, f=field: (r["manifest"].get(f) or {}).get(k)))
    pkgs = sorted({k for r in mres for k in (r["manifest"].get("packages") or {})})
    for k in pkgs:
        add("model", f"package {k}", _differs(mres, lambda r, k=k: (r["manifest"].get("packages") or {}).get(k)))

    # --- benchmark configuration ---
    simple = {"dataset hashes": lambda m: m.get("dataset_sha256"),
              "tenant documents": lambda m: (m.get("tenant_docs_sha256") or {}).get("combined"),
              "FAQ prompt": lambda m: (m.get("prompt_sha256") or {}).get("faq_system"),
              "triage prompt": lambda m: (m.get("prompt_sha256") or {}).get("triage_system"),
              "context mode": lambda m: m.get("context_mode"),
              "limit per split": lambda m: m.get("limit_per_split"),
              "splits run": lambda m: m.get("splits")}
    for label, fn in simple.items():
        add("config", label, _differs(suites, lambda r, fn=fn: fn(r["manifest"])))
    files = sorted({k for r in suites for k in (r["manifest"].get("bench_code_sha256") or {})})
    changed = [f for f in files if _differs(suites, lambda r, f=f: (r["manifest"].get("bench_code_sha256") or {}).get(f))]
    if changed:
        out.append({"group": "config", "label": "benchmark code",
                    "detail": "benchmark code differs in: " + ", ".join(changed) + " (the scoring or measurement code was not the same)"})

    # --- hardware ---
    for k in _HARDWARE_KEYS:
        add("machine", f"hardware {k}", _differs(results, lambda r, k=k: (r.get("system") or {}).get(k)))
    return out


def manifest_conflicts(results: list[dict]) -> list[str]:
    """Short labels of everything that differs (see manifest_conflict_details)."""
    return [d["label"] for d in manifest_conflict_details(results)]


_GROUP_TITLE = {"model": "different model builds, runtime versions or model settings",
                "config": "a different benchmark configuration (data, prompts, scoring code or run options)",
                "machine": "different hardware"}


def render_conflict_warning(results: list[dict]) -> list[str]:
    details = manifest_conflict_details(results)
    if not details:
        return []
    out = ["> **WARNING: these results were produced under different experimental conditions. Do not compare or combine them.**"]
    for group in ("model", "config", "machine"):
        items = [d for d in details if d["group"] == group]
        if items:
            out.append(f"> - {_GROUP_TITLE[group]}:")
            out += [f">   - {d['detail']}" for d in items]
    out.append("> Re-run the benchmarks together under one configuration, or compare only files that share the same conditions.")
    out.append("")
    return out


def _f(x, nd=2):
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _pct(x):
    return "-" if x is None else f"{x * 100:.0f}%"


def _nr(n, d, rate_=None):
    return f"{n}/{d} ({_pct(rate_ if rate_ is not None else (n / d if d else None))})"


def render_markdown(results: list[dict]) -> str:
    out = ["# Benchmark summary", ""]
    if results:
        s = results[0].get("system", {})
        out += [f"Machine: {s.get('cpu_model')} | {s.get('cpu_cores_physical')}C/{s.get('cpu_threads')}T | RAM {s.get('ram_gb')} GB "
                f"(free {s.get('ram_available_gb')}) | load before run {s.get('load_avg_1m')}", ""]
        out += render_conflict_warning(results)
        runs = {r.get("manifest", {}).get("run_id") for r in results}
        m0 = next((r["manifest"] for r in results if r.get("manifest")), {})
        out += [f"Run(s): {', '.join(sorted(x for x in runs if x))} | schema {SCHEMA_VERSION} | splits {m0.get('splits')} | "
                f"limit per split {m0.get('limit_per_split')} | context: {m0.get('context_mode')}",
                f"Ollama {((m0.get('ollama') or {}).get('version'))}; model digests: " +
                ", ".join(f"{k}={d[:12]}" for k, d in sorted({k: str(v.get('digest')) for r in results
                                                             for k, v in ((r.get('manifest') or {}).get('ollama') or {}).get('models', {}).items()}.items())), ""]
    faq = [r for r in results if r["suite"] == "faq"]
    if faq:
        out += ["## FAQ answering: dev and held-out are separate; the full text of every answer is in the JSON", "",
                "| model | split | answerable (ctx-sufficient / gap) | correct, all answerable | correct, ctx-sufficient | over-abstain raw | over-abstain corrected | truncated | bad citations | must-abstain correct | total p50 / p95 (s) | tok/s p50 | cold start (s) | errors |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in faq:
            for split, sm in r["splits"].items():
                a, sp = sm["answerable"], sm["speed"]
                out.append(f"| {r['model']} | {split} | {a['n']} ({a['context_sufficient_n']} / {a['retrieval_gap_n']}) | {_pct(a['correct_rate'])} | "
                           f"{_pct(a['correct_rate_context_sufficient'])} | {_pct(a['over_abstain_rate_raw'])} | {_pct(a['over_abstain_rate_corrected'])} | "
                           f"{sp['truncated']}/{sm['cases'] - sm['errors']} | {_pct(a['unknown_citation_rate'])} | "
                           f"{_pct(sm['must_abstain']['correct_rate'])} (n={sm['must_abstain']['n']}) | "
                           f"{_f(sp['total_s'].get('p50'))} / {_f(sp['total_s'].get('p95'))} | {_f(sp['tokens_per_s'].get('p50'), 1)} | "
                           f"{_f((r.get('cold_start') or {}).get('total_s'))} | {sm['errors']} |")
        out += ["", "### FAQ failure taxonomy (one label per case)", "",
                "| model | split | ok | truncated | retrieval_gap | over_abstain | mixed_output | bad_citation | no_citation | missing_fact | answered_unanswerable | other |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in faq:
            for split, sm in r["splits"].items():
                lb = sm["labels"]
                out.append(f"| {r['model']} | {split} | " + " | ".join(str(lb.get(k, 0)) for k in
                           ("ok", "truncated", "retrieval_gap", "over_abstain", "mixed_output", "bad_citation", "no_citation",
                            "missing_fact", "answered_unanswerable", "other")) + " |")
        out.append("")
    tri = [r for r in results if r["suite"] == "triage"]
    if tri:
        out += ["## Triage: routes come from the real route node", "",
                "| model | split | cases | route accuracy | false escalations | missed escalations | own-records flag | category (secondary) | unusable | confidence p50 | below min conf | latency p50 (s) |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in tri:
            for split, sm in r["splits"].items():
                e, o, c = sm["escalation"], sm["own_records"], sm["category"]
                out.append(f"| {r['model']} | {split} | {sm['cases']} | {_pct(sm['route_accuracy'])} | {_nr(e['false_n'], e['non_escalation_n'], e['false_rate'])} | "
                           f"{_nr(e['missed_n'], e['expected_n'], e['missed_rate'])} | {_pct(o['accuracy'])} (n={o['scored_n']}) | "
                           f"{_pct(c['accuracy'])} (n={c['scored_n']}) | {_pct(sm['unusable_output_rate'])} | {_f(sm['confidence'].get('p50'))} | "
                           f"{_pct(sm['below_min_confidence_rate'])} | {_f(sm['latency_s'].get('p50'))} |")
        out.append("")
    emb = [r for r in results if r["suite"] == "embed"]
    if emb:
        out += ["## Embeddings", "", "| model | split | dim | index (s) | query p50 (s) | hit@1 | hit@4 | score gap |", "|---|---|---|---|---|---|---|---|"]
        for r in emb:
            for split, sm in r["splits"].items():
                g = sm["score_gap"]
                gap = (f"separable, suggest SG_RAG_MIN_SCORE~{g['suggested_min_score']}" if g.get("separable")
                       else "overlap: " + g.get("note", "n/a") if g.get("separable") is False else "n/a")
                out.append(f"| {r['model']} | {split} | {sm['dim']} | {_f(sm['index_total_s'], 1)} | {_f(sm['query_s'].get('p50'), 3)} | "
                           f"{_pct(sm['hit_at_1'])} | {_pct(sm.get('hit_at_4'))} | {gap} |")
        out.append("")
    for r in (x for x in results if x["suite"] == "stt"):
        out += ["## Speech-to-text", "", f"{r['model']}: RTF p50 {_f(r['rtf'].get('p50'), 3)}, p95 {_f(r['rtf'].get('p95'), 3)} over {r['files']} files; load {r['model_load_s']} s", ""]
    for r in (x for x in results if x["suite"] == "tts"):
        out += ["## Text-to-speech", "", f"{r['voice']}: RTF p50 {_f(r['rtf'].get('p50'), 3)}, per-sentence p50 {_f(r['process_s'].get('p50'), 2)} s ({r['note']})", ""]
    out += ["---", "Speeds are measured on this machine only. Quality scores are on synthetic data with deterministic checks (no LLM judge). "
            "A reply cut at the token limit counts as a failure. See docs/BENCHMARKS.md for how to read each column.", ""]
    return "\n".join(out)
