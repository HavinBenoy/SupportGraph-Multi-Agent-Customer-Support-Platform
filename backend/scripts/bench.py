"""Benchmark kit CLI. Measures candidate local models on THIS machine. No database, no student data, no network except
the local Ollama server. Results go to evals/bench/ (JSON) and evals/bench/SUMMARY.md.

    make bench-check                                   # is Ollama up, which models are pulled, what is missing
    make bench-llm MODELS="qwen3:4b qwen3:1.7b"        # FAQ + triage per model, dev AND held-out reported separately
    make bench-llm ARGS="--splits dev --limit 10"      # quick pass
    make bench-embed                                   # embedding speed + retrieval quality
    make bench-stt AUDIO=~/my-wavs MODEL=base.en       # optional (voice milestone)
    make bench-tts VOICE=~/voices/en_US-lessac-medium.onnx
    make bench-report                                  # merge all results into SUMMARY.md

Close heavy apps first and leave the laptop plugged in; the report records CPU load before the run.
"""

import argparse
import sys
import uuid
from pathlib import Path

from app.bench import audio_bench, corpus, embed_bench, llm_bench, report
from app.config import Settings, get_settings
from app.evals.run import DATASETS_DIR
from app.llm.gateway import get_chat_model, get_embedding_client
from app.llm.guard import check_endpoint_allowed

RUN_ID = uuid.uuid4().hex[:8]

OUT = DATASETS_DIR.parent / "bench"
DEFAULT_MODELS = ["qwen3:4b", "qwen3:1.7b"]
DEFAULT_EMBED = ["nomic-embed-text"]


def _ollama_models(base_url: str) -> list[str]:
    import httpx

    r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
    r.raise_for_status()
    return sorted(m["name"] for m in r.json().get("models", []))


def _has(models: list[str], want: str) -> bool:
    return want in models or (":" not in want and f"{want}:latest" in models)


def _bench_settings(a) -> Settings:
    s = get_settings().model_copy(update={"llm_provider": "ollama", "embedding_provider": "ollama"})
    if a.base_url:
        s = s.model_copy(update={"llm_base_url": a.base_url, "embedding_base_url": a.base_url})
    check_endpoint_allowed(s.llm_base_url, s.allow_external_llm, what="benchmark")  # same local-only rule as the app
    return s


# Generation settings an experiment may override for ONE run. Everything else (prompts, retrieval, datasets) is fixed on purpose.
OVERRIDABLE = ("llm_temperature", "llm_top_p", "llm_top_k", "llm_repeat_penalty", "llm_seed", "llm_max_output_tokens", "llm_num_ctx",
               "llm_thinking")


def apply_overrides(s: Settings, pairs: list[str] | None) -> Settings:
    """`--set llm_temperature=0.7` -> a validated copy of the settings. Unknown or non-generation keys are refused, so an
    experiment cannot quietly change the dataset, the prompt or the retrieval. The result is recorded in the run manifest."""
    if not pairs:
        return s
    upd: dict = {}
    for p in pairs:
        k, sep, v = p.partition("=")
        k = k.strip().lower().removeprefix("sg_")
        if not sep or k not in OVERRIDABLE:
            raise SystemExit(f"--set {p!r}: use KEY=VALUE with KEY one of: {', '.join(OVERRIDABLE)}")
        upd[k] = v
    try:
        return Settings.model_validate({**s.model_dump(), **upd})
    except Exception as e:  # noqa: BLE001 - pydantic ValidationError: say which value is wrong, no traceback
        raise SystemExit(f"invalid --set value: {str(e).splitlines()[0]} ... {e.errors()[0]['loc'] if hasattr(e, 'errors') else ''}")


def _progress(label):
    def p(i, n):
        print(f"\r  {label}: {i}/{n}", end="", flush=True)
        if i == n:
            print()
    return p


def cmd_check(a) -> int:
    s = _bench_settings(a)
    try:
        have = _ollama_models(s.llm_base_url)
    except Exception as e:  # noqa: BLE001
        print(f"Cannot reach Ollama at {s.llm_base_url} ({type(e).__name__}). Start it (`ollama serve`) and retry.")
        return 1
    print(f"Ollama reachable at {s.llm_base_url}. Pulled models: {', '.join(have) or '(none)'}")
    missing = [m for m in DEFAULT_MODELS + DEFAULT_EMBED if not _has(have, m)]
    for m in missing:
        print(f"  missing: ollama pull {m}")
    return 1 if missing else 0


def _manifest(s: Settings, a, models: list[str], splits: list[str]):
    return report.build_manifest(s, argv=sys.argv[1:], run_id=RUN_ID, splits=splits, models=models, limit=getattr(a, "limit", None),
                                 ollama=report.ollama_info(s.llm_base_url, models))


def cmd_llm(a) -> int:
    s = apply_overrides(_bench_settings(a), a.set)
    models = a.models or DEFAULT_MODELS
    split_names = a.splits
    if a.heldout:
        print("note: --heldout is no longer needed; both splits run by default and are reported separately.")
    try:
        have = _ollama_models(s.llm_base_url)
    except Exception as e:  # noqa: BLE001
        print(f"Cannot reach Ollama ({type(e).__name__}). Run `make bench-check`.")
        return 1
    chunks = corpus.load_chunks(s.default_tenant)
    splits = corpus.load_splits(s.default_tenant, split_names)
    manifest = _manifest(s, a, models, split_names)
    unload = llm_bench.ollama_unload(s.llm_base_url)
    for m in models:
        if not _has(have, m):
            print(f"skip {m}: not pulled (ollama pull {m})")
            continue
        print(f"== {m}   splits: {', '.join(f'{k}={len(v)} tickets' for k, v in splits.items())}")
        if "faq" in a.suites:
            cold = None if a.no_cold_start else llm_bench.cold_start(s, m, get_chat_model, unload)
            faq = llm_bench.bench_faq(s, m, get_chat_model, splits=splits, chunks=chunks, limit=a.limit, on_progress=_progress("faq"))
            faq["cold_start"] = cold
            faq["variant"] = a.tag
            print("  ->", report.write_result(OUT, faq, manifest=manifest))
        if "triage" in a.suites:
            tri = llm_bench.bench_triage(s, m, get_chat_model, splits=splits, limit=a.limit, on_progress=_progress("triage"))
            tri["variant"] = a.tag
            print("  ->", report.write_result(OUT, tri, manifest=manifest))
    return 0


def cmd_embed(a) -> int:
    s = _bench_settings(a)
    chunks = corpus.load_chunks(s.default_tenant)
    splits = corpus.load_splits(s.default_tenant, a.splits)
    models = a.models or DEFAULT_EMBED
    manifest = _manifest(s, a, models, a.splits)
    for m in models:
        print(f"== {m}")
        try:
            per_split = {name: embed_bench.bench_embed(s, m, get_embedding_client, tickets=tk, chunks=chunks, limit=a.limit)
                         for name, tk in splits.items()}
        except Exception as e:  # noqa: BLE001 - includes wrong vector width (the DB stores 768)
            print(f"  failed: {type(e).__name__}: {e}")
            continue
        for sm in per_split.values():
            sm.pop("suite", None)
            sm.pop("model", None)
        print("  ->", report.write_result(OUT, {"suite": "embed", "model": m, "splits": per_split}, manifest=manifest))
    return 0


def cmd_stt(a) -> int:
    try:
        r = audio_bench.bench_stt(a.model, Path(a.audio).expanduser(), threads=a.threads)
    except audio_bench.OptionalDependencyMissing as e:
        print(e)
        return 1
    print("->", report.write_result(OUT, r))
    return 0


def cmd_tts(a) -> int:
    try:
        r = audio_bench.bench_tts(Path(a.voice).expanduser(), piper_bin=a.piper_bin)
    except audio_bench.OptionalDependencyMissing as e:
        print(e)
        return 1
    print("->", report.write_result(OUT, r))
    return 0


def cmd_report(a) -> int:
    try:
        results = report.load_results(OUT)
    except report.IncompatibleResults as e:
        print(e)
        return 1
    if not results:
        print(f"No results in {OUT}. Run a benchmark first.")
        return 1
    md = report.render_markdown(results)
    (OUT / "SUMMARY.md").write_text(md)
    print(md)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out-dir", help="where results and SUMMARY.md go (default: evals/bench). A quick run uses evals/bench-quick so it never mixes with the full run")
    p.add_argument("--base-url", help="Ollama URL (default: SG_LLM_BASE_URL). Must be local unless SG_ALLOW_EXTERNAL_LLM=true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    q = sub.add_parser("llm"); q.add_argument("--models", nargs="*"); q.add_argument("--limit", type=int, help="tickets per split"); q.add_argument("--splits", nargs="+", choices=list(corpus.SPLIT_FILES), default=list(corpus.SPLIT_FILES)); q.add_argument("--heldout", action="store_true", help=argparse.SUPPRESS)
    q.add_argument("--suites", nargs="+", choices=["faq", "triage"], default=["faq", "triage"], help="run only one suite")
    q.add_argument("--set", action="append", metavar="KEY=VALUE", help="override one generation setting for this run (repeatable): " + ", ".join(OVERRIDABLE))
    q.add_argument("--tag", help="name for this variant; added to the result file name so it does not overwrite the baseline")
    q.add_argument("--no-cold-start", action="store_true", help="skip the unload + cold-start timing (quality-only runs)")
    q.set_defaults(fn=cmd_llm)
    q = sub.add_parser("embed"); q.add_argument("--models", nargs="*"); q.add_argument("--limit", type=int, help="tickets per split"); q.add_argument("--splits", nargs="+", choices=list(corpus.SPLIT_FILES), default=list(corpus.SPLIT_FILES)); q.set_defaults(fn=cmd_embed)
    q = sub.add_parser("stt"); q.add_argument("--audio", required=True); q.add_argument("--model", default="base.en"); q.add_argument("--threads", type=int, default=4); q.set_defaults(fn=cmd_stt)
    q = sub.add_parser("tts"); q.add_argument("--voice", required=True); q.add_argument("--piper-bin"); q.set_defaults(fn=cmd_tts)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    a = p.parse_args()
    if a.out_dir:
        global OUT
        OUT = Path(a.out_dir).expanduser().resolve()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
