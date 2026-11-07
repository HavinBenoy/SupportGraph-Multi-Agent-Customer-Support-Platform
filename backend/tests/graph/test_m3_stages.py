"""M3: stage-level timings, Ollama counters, the cold/warm verdict and the reconciliation of node timings with the whole run.
Part 1 uses the recorder directly; part 2 runs the REAL langchain-ollama client against the mock Ollama server (counters and runner options)."""

import json

import pytest
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.messages import AIMessage

from app.config import Settings
from app.db.session import session_scope
from app.graph.support import ask_support, build_support_graph
from app.llm.gateway import get_embedding_client
from app.observability import stages
from app.rag.ingest import ingest_tenant
from app.tools.scope import ANONYMOUS
from tests.bench.mock_ollama import MockOllama

T = "qspiders_sample"
QUESTION = "Can I pay the fee in instalments?"


# ---- the recorder ---------------------------------------------------------------------------------------------------------------------
def test_stage_is_a_no_op_without_a_recorder():
    with stages.stage("embed"):
        pass
    stages.note("x", 1)
    assert stages.current() is None


def test_stages_are_attributed_to_the_running_node_and_other_is_the_remainder():
    rec, tok = stages.start()
    try:
        rec.node = "knowledge"
        rec.add("embed", 40)
        rec.add("llm", 300)
        rec.add("llm", 20)                      # the same stage twice accumulates
        rec.node = "triage"
        rec.add("triage_llm", 100)
    finally:
        stages.finish(tok)
    s = rec.summary({"triage": 120, "knowledge": 400, "qa": 1}, 530)
    assert s["by_node"]["knowledge"] == {"embed": 40.0, "llm": 320.0, "other": 40.0}       # 400 - 360
    assert s["by_node"]["triage"] == {"triage_llm": 100.0, "other": 20.0}
    assert s["nodes_sum_ms"] == 521 and s["graph_ms"] == 530 and s["unaccounted_ms"] == 9   # graph time minus what the nodes explain
    assert s["stages"] == {"embed": 40.0, "llm": 320.0, "triage_llm": 100.0}
    assert "qa" not in s["by_node"]                                                           # a node without stages is not invented


def test_a_node_never_reports_negative_other_time():
    rec = stages.StageRecorder()
    rec.node = "knowledge"
    rec.add("llm", 50)
    assert rec.summary({"knowledge": 49}, 49)["by_node"]["knowledge"]["other"] == 0.0         # rounding noise is clamped, not shown as negative


def test_cold_verdict_comes_from_the_model_load_time_not_from_a_guess():
    def verdict(load_ms, embed_ms=10, first=False, warmed=False):
        stages.mark_warm(warmed)
        try:
            rec = stages.StageRecorder()
            rec.first_run = first
            rec.node = "knowledge"
            rec.add("embed", embed_ms)
            if load_ms is not None:
                rec.llm_call("faq", {"load_ms": load_ms})
            return rec.summary({"knowledge": 100}, 100, cold_load_ms=800, cold_embed_ms=1500)
        finally:
            stages.mark_warm(False)
    assert verdict(4200)["cold_reasons"] == ["llm_load"] and verdict(4200)["cold"] is True
    assert verdict(30)["cold"] is False                                                        # a warm call: a few ms of "load"
    assert verdict(30, embed_ms=2500)["cold_reasons"] == ["embed_slow"]
    assert verdict(None, first=True)["cold_reasons"] == ["first_run_without_warmup"]          # first run of the process and nobody warmed it
    assert verdict(None, first=True, warmed=True)["cold"] is False                            # ... but after a warm-up it is just a run


def test_ollama_counters_are_converted_to_milliseconds_and_rates():
    m = stages.parse_ollama_metrics({"load_duration": 4_000_000_000, "prompt_eval_count": 900, "prompt_eval_duration": 3_000_000_000,
                                     "eval_count": 60, "eval_duration": 4_000_000_000, "total_duration": 11_000_000_000})
    assert m == {"load_ms": 4000.0, "prompt_tokens": 900, "prompt_eval_ms": 3000.0, "prompt_tok_s": 300.0, "decode_tokens": 60, "decode_ms": 4000.0,
                 "decode_tok_s": 15.0, "total_ms": 11000.0}
    assert stages.parse_ollama_metrics({"model": "x"}) is None                                 # other providers: no counters, no crash


def test_the_callback_copies_counters_into_the_active_recorder_and_never_raises():
    h = stages.OllamaMetricsHandler("faq")
    msg = AIMessage("hi", response_metadata={"load_duration": 5_000_000, "eval_count": 3, "eval_duration": 300_000_000})
    resp = LLMResult(generations=[[ChatGeneration(message=msg)]])
    h.on_llm_end(resp)                                         # no recorder: silently nothing
    rec, tok = stages.start()
    try:
        rec.node = "knowledge"
        h.on_llm_end(resp)
        h.on_llm_end(object())                                 # garbage must not break an answer
    finally:
        stages.finish(tok)
    assert [c["role"] for c in rec.llm_calls] == ["faq"] and rec.llm_calls[0]["node"] == "knowledge" and rec.llm_calls[0]["decode_tokens"] == 3


# ---- through the real graph (fake providers) --------------------------------------------------------------------------------------------
@pytest.fixture
def fake_graph():
    s = Settings(_env_file=None)
    with session_scope() as db:
        ingest_tenant(db, T, get_embedding_client(s))
    return build_support_graph(settings=s)


def test_a_faq_run_reports_every_stage_and_the_numbers_reconcile(fake_graph):
    out = ask_support(fake_graph, QUESTION, tenant_id=T, student_id=ANONYMOUS, ticket_id="m3-a")
    p = out["perf"]
    for name in ("rewrite", "embed", "search", "context", "llm", "validate"):
        assert name in p["by_node"]["knowledge"], name                                          # none of the knowledge stages is invisible
    assert p["by_node"]["knowledge"]["other"] >= 0 and p["graph_ms"] >= p["nodes_sum_ms"] - 1
    assert p["unaccounted_ms"] == p["graph_ms"] - p["nodes_sum_ms"]
    stage_sum = sum(v for k, v in p["by_node"]["knowledge"].items() if k != "other")
    assert stage_sum <= out["node_ms"]["knowledge"] + 2                                         # stages are INSIDE the node: never counted twice
    assert out["node_ms"].keys() >= {"triage", "route", "knowledge", "qa", "finalize"}
    assert p["first_run"] in (True, False) and isinstance(p["cold"], bool)


def test_the_perf_report_contains_numbers_and_names_only_never_the_question_or_the_answer(fake_graph):
    out = ask_support(fake_graph, QUESTION, tenant_id=T, student_id=ANONYMOUS, ticket_id="m3-b")
    blob = json.dumps(out["perf"]).lower()
    assert "instalment" not in blob and out["reply"].lower()[:40] not in blob


def test_a_greeting_has_no_model_stages_and_a_human_request_skips_the_triage_model(fake_graph):
    for text in ("Hello", "I want to talk to a person"):
        p = ask_support(fake_graph, text, tenant_id=T, student_id=ANONYMOUS, ticket_id=f"m3-{len(text)}")["perf"]
        assert "llm" not in p["stages"] and "triage_llm" not in p["stages"] and p["llm"] == []


def test_the_qa_node_is_a_deterministic_check_so_it_is_cheap_and_validation_lives_in_the_knowledge_stage(fake_graph):
    out = ask_support(fake_graph, QUESTION, tenant_id=T, student_id=ANONYMOUS, ticket_id="m3-c")
    assert out["node_ms"]["qa"] <= 50 and "validate" in out["perf"]["stages"]                  # why "qa" reads ~0 ms: see docs/M3.md


# ---- the real langchain-ollama client against the mock server ------------------------------------------------------------------------------
def stack(url, **kw):
    s = Settings(_env_file=None, llm_provider="ollama", llm_base_url=url, llm_model="qwen3:1.7b", embedding_provider="ollama",
                 embedding_base_url=url, **kw)
    with session_scope() as db:
        ingest_tenant(db, T, get_embedding_client(s))
    return build_support_graph(settings=s), s


def test_ollama_counters_and_runner_options_reach_the_report_and_the_requests():
    with MockOllama() as m:
        graph, s = stack(m.url, llm_num_thread=4, triage_max_output_tokens=150, obs_cold_load_ms=400)
        out = ask_support(graph, "force_knowledge: how are the fee instalments arranged?", tenant_id=T, student_id=ANONYMOUS, ticket_id="m3-d")
        p = out["perf"]
        roles = [c["role"] for c in p["llm"]]
        assert roles == ["triage", "faq"] and {c["node"] for c in p["llm"]} == {"triage", "knowledge"}
        assert all(c["load_ms"] == 500.0 and c["prompt_tokens"] == 120 and c["decode_tokens"] == 20 for c in p["llm"])
        assert p["cold"] is True and p["cold_reasons"] == ["llm_load"] and p["llm_load_ms"] == 1000.0       # the mock reports a 500 ms load per call
        chats = [b for path, b in m.calls if path == "/api/chat"]
        assert len(chats) == 2 and all(b["options"]["num_thread"] == 4 and b["options"]["num_ctx"] == s.llm_num_ctx for b in chats)
        assert chats[0]["options"]["num_predict"] == 150                                                     # triage is capped
        assert chats[1]["options"]["num_predict"] == s.llm_max_output_tokens                                 # the answer is not
        assert {"triage_llm", "embed", "llm"} <= set(p["stages"])


def test_counters_can_be_switched_off_without_changing_the_answer():
    with MockOllama() as m:
        graph, s = stack(m.url, obs_ollama_metrics=False)
        out = ask_support(graph, "force_knowledge: how are the fee instalments arranged?", tenant_id=T, student_id=ANONYMOUS, ticket_id="m3-e")
        assert out["perf"]["llm"] == [] and out["citations"]
