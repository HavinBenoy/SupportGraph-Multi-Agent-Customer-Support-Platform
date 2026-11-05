import json
import struct
import wave
from pathlib import Path

import pytest

from app.bench import audio_bench, corpus, embed_bench, llm_bench, metrics, report
from app.bench.stats import percentile, rate, summarise
from app.config import Settings
from app.llm.gateway import get_chat_model, get_embedding_client
from tests.bench.mock_ollama import MockOllama

TENANT = "qspiders_sample"


def S(url: str) -> Settings:
    return Settings(llm_provider="ollama", llm_base_url=url, embedding_provider="ollama", embedding_base_url=url, _env_file=None)


def test_stats():
    assert percentile([1, 2, 3, 4], 50) == 2.5 and percentile([], 50) is None
    assert summarise([1.0, 3.0])["mean"] == 2.0 and summarise([]) == {"n": 0} and rate(1, 0) is None


def test_corpus_loads_from_repo_only():
    chunks = corpus.load_chunks(TENANT)
    assert chunks and all(c.doc_id and c.text for c in chunks)
    cases = corpus.knowledge_cases(corpus.load_tickets(corpus.DEV, TENANT))
    assert any(c["must_abstain"] for c in cases) and any(c["expected_facts"] for c in cases)
    top = corpus.lexical_top_k("What is the fee for the Python Full Stack course?", chunks, 3)
    assert top and len(top) <= 3


def test_judge_faq_answer_rules():
    ans = {"must_abstain": False, "expected_facts": ["10 days"]}
    ok = llm_bench.judge_faq_answer(ans, "Within 10 days [doc-a#0].", {"doc-a#0"})
    assert ok["correct"]
    assert not llm_bench.judge_faq_answer(ans, "Within 10 days.", {"doc-a#0"})["correct"]           # uncited
    assert not llm_bench.judge_faq_answer(ans, "Within 10 days [doc-b#9].", {"doc-a#0"})["correct"]  # hallucinated source
    assert not llm_bench.judge_faq_answer(ans, "Within 5 days [doc-a#0].", {"doc-a#0"})["correct"]   # missing fact
    ab = {"must_abstain": True, "expected_facts": []}
    assert llm_bench.judge_faq_answer(ab, "INSUFFICIENT_CONTEXT", set())["correct"]
    assert not llm_bench.judge_faq_answer(ab, "Yes, 20 LPA [doc-a#0].", {"doc-a#0"})["correct"]


def test_llm_and_triage_bench_through_real_ollama_client():
    chunks = corpus.load_chunks(TENANT)
    splits = corpus.load_splits(TENANT)
    with MockOllama() as mock:
        s = S(mock.url)
        r = llm_bench.bench_faq(s, "qwen3:4b", get_chat_model, splits=splits, chunks=chunks, limit=8)
        assert set(r["splits"]) == {"dev", "heldout"}                     # never merged
        for name, sm in r["splits"].items():
            assert sm["errors"] == 0 and sm["cases"] == 8
            assert sm["answerable"]["over_abstain_rate_raw"] == 0.0 and sm["answerable"]["unknown_citation_rate"] == 0.0  # mock cites a real source
            assert sm["speed"]["tokens_per_s"]["p50"] == pytest.approx(20.0)   # eval_count / eval_duration from the provider
            assert sm["speed"]["prompt_tokens"]["p50"] == 120 and sm["speed"]["truncated"] == 0
        row = r["rows"][0]
        assert row["text"].startswith("Here is the answer [") and row["done_reason"] == "stop" and row["truncated"] is False
        assert row["question"] and row["context_ids"] and "context_sufficient" in row and row["label"] in metrics.LABELS
        assert r["generation"]["num_ctx"] == 4096 and r["generation"]["num_predict"] == 400 and r["generation"]["model"] == "qwen3:4b"
        chat_calls = [b for p, b in mock.calls if p == "/api/chat"]
        assert chat_calls[0]["options"]["num_ctx"] == 4096 and chat_calls[0]["options"]["num_predict"] == 400
        assert chat_calls[0]["model"] == "qwen3:4b" and chat_calls[0]["think"] is False   # thinking is off
        t = llm_bench.bench_triage(s, "qwen3:4b", get_chat_model, splits=splits, limit=3)
        for sm in t["splits"].values():
            assert sm["cases"] == 3 and sm["unusable_output_rate"] == 1.0   # the mock is not a classifier: bad output is COUNTED, not crashed on
            assert sm["escalation"]["false_n"] == 0                          # unusable output -> low-confidence fallback -> clarify, never escalate


def test_cold_start_unloads_first():
    unloaded = []
    with MockOllama() as mock:
        r = llm_bench.cold_start(S(mock.url), "qwen3:4b", get_chat_model, unloaded.append)
    assert unloaded == ["qwen3:4b"] and r["total_s"] >= 0 and r["load_s"] == pytest.approx(0.5)
    assert r["text"] and r["done_reason"] == "stop" and r["truncated"] is False


def test_dead_server_is_reported_not_raised():
    chunks = corpus.load_chunks(TENANT)
    tickets = corpus.load_tickets(corpus.DEV, TENANT)
    r = llm_bench.bench_faq(S("http://127.0.0.1:9"), "qwen3:4b", get_chat_model, splits={"dev": tickets}, chunks=chunks, limit=2)
    sm = r["splits"]["dev"]
    assert sm["errors"] == 2 and sm["answerable"]["n"] == 0


def test_embed_bench_through_real_ollama_client_and_gap_logic():
    chunks = corpus.load_chunks(TENANT)
    tickets = corpus.load_tickets(corpus.DEV, TENANT)
    with MockOllama() as mock:
        r = embed_bench.bench_embed(S(mock.url), "nomic-embed-text", get_embedding_client, tickets=tickets, chunks=chunks, limit=10)
    assert r["dim"] == 768 and r["chunks"] == len(chunks) and r["answerable_n"] > 0 and r["hit_at_4"] is not None
    assert embed_bench.suggest_min_score([0.6, 0.7, 0.8], [0.2, 0.3])["separable"] is True
    assert embed_bench.suggest_min_score([0.3, 0.4], [0.35, 0.5])["separable"] is False
    assert embed_bench.suggest_min_score([], [0.1])["separable"] is None


def _wav(path: Path, seconds: float = 0.5):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * int(16000 * seconds))


def test_stt_and_tts_plumbing_with_injected_engines(tmp_path):
    _wav(tmp_path / "a.wav", 1.0); _wav(tmp_path / "b.wav", 2.0)
    r = audio_bench.bench_stt("base.en", tmp_path, transcribe=lambda p: "what is the fee")
    assert r["files"] == 2 and all(x["transcript_words"] == 4 for x in r["rows"]) and "what is the fee" not in json.dumps(r)
    with pytest.raises(audio_bench.OptionalDependencyMissing):
        audio_bench.bench_stt("base.en", tmp_path / "empty")
    t = audio_bench.bench_tts(Path("voice.onnx"), runner=lambda text, out: _wav(out, 0.4))
    assert len(t["rows"]) == len(audio_bench.TTS_SENTENCES) and t["rtf"]["n"] == len(t["rows"])
    with pytest.raises(audio_bench.OptionalDependencyMissing):
        audio_bench.bench_tts(tmp_path / "missing.onnx", piper_bin=None)  # no piper on PATH / no voice file


def test_report_roundtrip_and_markdown(tmp_path):
    chunks = corpus.load_chunks(TENANT)
    splits = corpus.load_splits(TENANT)
    with MockOllama() as mock:
        s = S(mock.url)
        faq = llm_bench.bench_faq(s, "qwen3:4b", get_chat_model, splits=splits, chunks=chunks, limit=4)
        faq["cold_start"] = {"total_s": 3.2}
        tri = llm_bench.bench_triage(s, "qwen3:4b", get_chat_model, splits=splits, limit=4)
        per = {n: embed_bench.bench_embed(s, "nomic-embed-text", get_embedding_client, tickets=tk, chunks=chunks, limit=4) for n, tk in splits.items()}
        manifest = report.build_manifest(s, argv=["llm"], splits=list(splits), models=["qwen3:4b"], limit=4,
                                         ollama=report.ollama_info(mock.url, ["qwen3:4b"]))
    report.write_result(tmp_path, faq, manifest=manifest)
    report.write_result(tmp_path, tri, manifest=manifest)
    report.write_result(tmp_path, {"suite": "embed", "model": "nomic-embed-text", "splits": per}, manifest=manifest)
    results = report.load_results(tmp_path)
    md = report.render_markdown(results)
    assert "FAQ answering" in md and "failure taxonomy" in md and "Triage" in md and "Embeddings" in md
    assert "| qwen3:4b | dev |" in md and "| qwen3:4b | heldout |" in md and "WARNING" not in md
    assert manifest["ollama"]["version"] == "0.0.mock" and manifest["ollama"]["models"]["qwen3:4b"]["digest"] == "abc123def4567890"
    raw = "".join(p.read_text() for p in tmp_path.glob("*.json"))
    assert "hostname" not in raw and "/home/" not in raw and "api_key" not in raw


def test_manifest_records_split_hashes_prompts_and_settings():
    s = S("http://127.0.0.1:1")
    m = report.build_manifest(s, argv=["llm", "--limit", "5"], splits=["dev"], models=["qwen3:4b"], limit=5)
    assert m["schema_version"] == report.SCHEMA_VERSION and m["command"] == ["llm", "--limit", "5"]
    assert set(m["dataset_sha256"]) == {"dev", "heldout"} and all(len(v) == 64 for v in m["dataset_sha256"].values())
    assert len(m["prompt_sha256"]["faq_system"]) == 64 and len(m["prompt_sha256"]["triage_system"]) == 64
    assert m["settings"]["llm_num_ctx"] == 4096 and m["tenant_docs_sha256"]["files"]
    assert "api_key" not in json.dumps(m).lower()


def test_old_schema_results_are_refused_not_mixed(tmp_path):
    (tmp_path / "faq-old.json").write_text(json.dumps({"suite": "faq", "model": "x", "answerable": {}}))   # first-run layout, no schema_version
    with pytest.raises(report.IncompatibleResults) as e:
        report.load_results(tmp_path)
    assert "faq-old.json" in str(e.value) and "mkdir -p" in str(e.value)


def test_manifest_conflicts_flag_unfair_comparisons():
    a = {"suite": "faq", "manifest": {"prompt_sha256": {"faq_system": "1"}, "dataset_sha256": {"dev": "x"}}}
    b = {"suite": "faq", "manifest": {"prompt_sha256": {"faq_system": "2"}, "dataset_sha256": {"dev": "x"}}}
    assert report.manifest_conflicts([a, b]) == ["FAQ prompt"]
    assert report.manifest_conflicts([a, a]) == []


def _res(suite="faq", model="qwen3:4b", *, version="0.12.0", digest="d1", settings=None, roles=None, pkgs=None, hw=None, code=None, **extra):
    manifest = {"run_id": "r", "ollama": {"version": version, "models": {model: {"digest": digest}}},
                "settings": {"llm_num_ctx": 4096, "llm_model": "qwen3:4b", **(settings or {})}, "role_models": roles or {},
                "packages": {"langchain-ollama": "1.0.0", **(pkgs or {})},
                "bench_code_sha256": code or {"app/bench/metrics.py": "m1", "scripts/bench.py": "s1"},
                "dataset_sha256": {"dev": "x", "heldout": "y"}, "context_mode": "lexical_top_k", "limit_per_split": None,
                "splits": ["dev", "heldout"], **extra}
    return {"suite": suite, "model": model, "manifest": manifest,
            "system": {"cpu_model": "i5", "cpu_cores_physical": 4, "cpu_threads": 8, "ram_gb": 15.4, "machine": "x86_64", **(hw or {})}}


def test_identical_conditions_produce_no_warning():
    rs = [_res("faq"), _res("triage"), _res("faq", "qwen3:1.7b", digest="d2")]
    assert report.manifest_conflicts(rs) == [] and report.render_conflict_warning(rs) == []


def test_ollama_version_and_model_digest_differences_are_detected():
    d = report.manifest_conflict_details([_res(version="0.12.0"), _res("triage", version="0.12.5")])
    assert [x["label"] for x in d] == ["Ollama version"] and d[0]["group"] == "model"
    assert "0.12.0 [faq:qwen3:4b]" in d[0]["detail"] and "0.12.5 [triage:qwen3:4b]" in d[0]["detail"]
    d = report.manifest_conflict_details([_res(digest="aaaaaaaaaaaaaaaa"), _res("triage", digest="bbbbbbbbbbbbbbbb")])
    assert [x["label"] for x in d] == ["model digest for qwen3:4b"] and "same tag is a different build" in d[0]["detail"]
    # different models with different digests are expected and are NOT a conflict
    assert report.manifest_conflicts([_res(digest="a"), _res("faq", "qwen3:1.7b", digest="b")]) == []


def test_effective_settings_role_overrides_and_packages_are_compared():
    labels = report.manifest_conflicts([_res(), _res("triage", settings={"llm_num_ctx": 2048}, roles={"triage": "qwen3:1.7b"},
                                                     pkgs={"langchain-ollama": "1.1.0"})])
    assert "effective setting llm_num_ctx" in labels and "role-specific model override triage" in labels and "package langchain-ollama" in labels


def test_every_benchmark_code_file_is_hashed_and_a_change_in_any_is_detected():
    s = S("http://127.0.0.1:1")
    m = report.build_manifest(s, argv=[], splits=["dev"], models=["m"], limit=None)
    assert set(m["bench_code_sha256"]) == set(report.BENCH_CODE_FILES) == {
        "app/bench/metrics.py", "app/bench/llm_bench.py", "app/bench/corpus.py", "app/bench/report.py", "app/bench/stats.py",
        "app/bench/embed_bench.py", "scripts/bench.py"}
    assert all(len(v) == 64 for v in m["bench_code_sha256"].values())
    d = report.manifest_conflict_details([_res(), _res("triage", code={"app/bench/metrics.py": "m1", "scripts/bench.py": "s2"})])
    assert [x["label"] for x in d] == ["benchmark code"] and "scripts/bench.py" in d[0]["detail"] and "metrics.py" not in d[0]["detail"].split(":", 1)[1].replace("app/bench/metrics.py", "") 


def test_hardware_difference_across_machines_is_detected():
    d = report.manifest_conflict_details([_res(), _res("triage", hw={"cpu_model": "Ryzen 7", "ram_gb": 32.0})])
    assert {x["label"] for x in d} == {"hardware cpu_model", "hardware ram_gb"} and all(x["group"] == "machine" for x in d)


def test_report_warns_clearly_across_model_versions_and_configurations():
    rs = [_res(version="0.12.0", digest="aaaaaaaaaaaaaaaa"),
          _res("triage", version="0.12.5", digest="bbbbbbbbbbbbbbbb", code={"app/bench/metrics.py": "m2", "scripts/bench.py": "s1"},
               hw={"cpu_model": "Ryzen 7"})]
    for r in rs:
        r.update({"splits": {}, "generation": {}})
    md = report.render_markdown(rs)
    assert "WARNING: these results were produced under different experimental conditions. Do not compare or combine them." in md
    assert "different model builds, runtime versions or model settings" in md and "model digest for qwen3:4b" in md and "Ollama version" in md
    assert "different benchmark configuration" in md and "different hardware" in md and "benchmark code differs in: app/bench/metrics.py" in md


def test_stt_tts_files_without_manifest_do_not_trigger_warnings():
    rs = [_res(), {"suite": "stt", "model": "w", "manifest": {}, "system": {"cpu_model": "i5", "cpu_cores_physical": 4, "cpu_threads": 8,
                                                                         "ram_gb": 15.4, "machine": "x86_64"}}]
    assert report.manifest_conflicts(rs) == []
