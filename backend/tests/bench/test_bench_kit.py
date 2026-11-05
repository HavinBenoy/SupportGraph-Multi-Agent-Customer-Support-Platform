import json
import struct
import wave
from pathlib import Path

import pytest

from app.bench import audio_bench, corpus, embed_bench, llm_bench, report
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
    tickets = corpus.load_tickets(corpus.DEV, TENANT)
    with MockOllama() as mock:
        s = S(mock.url)
        r = llm_bench.bench_faq(s, "qwen3:4b", get_chat_model, tickets=tickets, chunks=chunks, limit=8)
        assert r["errors"] == 0 and r["cases"] == 8
        assert r["answerable"]["over_abstain_rate"] == 0.0 and r["answerable"]["unknown_citation_rate"] == 0.0  # mock cites a real source
        assert r["speed"]["tokens_per_s"]["p50"] == pytest.approx(20.0)      # eval_count / eval_duration from the provider
        assert r["speed"]["ttft_s"]["n"] > 0 and r["speed"]["prompt_tokens"]["p50"] == 120
        chat_calls = [b for p, b in mock.calls if p == "/api/chat"]
        assert chat_calls[0]["options"]["num_ctx"] == 4096 and chat_calls[0]["options"]["num_predict"] == 400
        assert chat_calls[0]["model"] == "qwen3:4b" and chat_calls[0]["think"] is False   # thinking is off
        t = llm_bench.bench_triage(s, "qwen3:4b", get_chat_model, tickets=tickets, limit=3)
        assert t["cases"] == 3 and t["unusable_output_rate"] == 1.0   # the mock is not a classifier: bad output is COUNTED, not crashed on


def test_cold_start_unloads_first():
    unloaded = []
    with MockOllama() as mock:
        r = llm_bench.cold_start(S(mock.url), "qwen3:4b", get_chat_model, unloaded.append)
    assert unloaded == ["qwen3:4b"] and r["total_s"] >= 0 and r["load_s"] == pytest.approx(0.5)


def test_dead_server_is_reported_not_raised():
    chunks = corpus.load_chunks(TENANT)
    tickets = corpus.load_tickets(corpus.DEV, TENANT)
    r = llm_bench.bench_faq(S("http://127.0.0.1:9"), "qwen3:4b", get_chat_model, tickets=tickets, chunks=chunks, limit=2)
    assert r["errors"] == 2 and r["answerable"]["n"] == 0


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
    tickets = corpus.load_tickets(corpus.DEV, TENANT)
    with MockOllama() as mock:
        s = S(mock.url)
        faq = llm_bench.bench_faq(s, "qwen3:4b", get_chat_model, tickets=tickets, chunks=chunks, limit=4)
        faq["cold_start"] = {"total_s": 3.2}
        emb = embed_bench.bench_embed(s, "nomic-embed-text", get_embedding_client, tickets=tickets, chunks=chunks, limit=4)
    report.write_result(tmp_path, faq, settings_info={"num_ctx": 4096})
    report.write_result(tmp_path, emb)
    results = report.load_results(tmp_path)
    md = report.render_markdown(results)
    assert "FAQ answering" in md and "qwen3:4b" in md and "Embeddings" in md and "nomic-embed-text" in md
    raw = "".join(p.read_text() for p in tmp_path.glob("*.json"))
    assert "hostname" not in raw and "/home/" not in raw
