"""Unit tests for the M1b local-AI plumbing: prefix policy, preflight, warm-up, reasoning stripping."""

import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.llm.gateway import effective_embedding_prefixes, get_embedding_client
from app.llm.preflight import chat_models_in_use, enforce, run_preflight, warm_up
from app.llm.reasoning import strip_message
from tests.bench.mock_ollama import MockOllama


def S(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_prefix_policy():
    assert effective_embedding_prefixes(S()) == ("", "")  # fake provider: never
    n = S(embedding_provider="ollama", embedding_model="nomic-embed-text")
    assert effective_embedding_prefixes(n) == ("search_query: ", "search_document: ")
    assert effective_embedding_prefixes(n.model_copy(update={"embedding_prefix_mode": "none"})) == ("", "")
    other = S(embedding_provider="ollama", embedding_model="bge-m3")
    assert effective_embedding_prefixes(other) == ("", "")


def test_explicit_prefixes_win():
    s = S(embedding_provider="ollama", embedding_model="nomic-embed-text", embedding_query_prefix="Q: ", embedding_document_prefix="D: ")
    assert effective_embedding_prefixes(s) == ("Q: ", "D: ")


def test_fake_embedder_name_is_unchanged_by_this_milestone():
    assert "@px-" not in get_embedding_client(S()).name


def test_preflight_ok_and_missing_model():
    with MockOllama() as m:
        ok = run_preflight(S(llm_provider="ollama", llm_base_url=m.url, llm_model="qwen3:4b", embedding_provider="ollama",
                             embedding_base_url=m.url))
        assert ok.ok, ok.problems
        bad = run_preflight(S(llm_provider="ollama", llm_base_url=m.url, llm_model="qwen3:1.7b"))
        assert not bad.ok and "ollama pull qwen3:1.7b" in bad.problems[0]


def test_preflight_unreachable_and_external_refused():
    r = run_preflight(S(llm_provider="ollama", llm_base_url="http://127.0.0.1:9"), timeout=0.5)
    assert not r.ok and "ollama serve" in r.problems[0]
    ext = run_preflight(S(llm_provider="ollama", llm_base_url="https://api.example.com"))
    assert not ext.ok and "SG_ALLOW_EXTERNAL_LLM" in ext.problems[0]


def test_preflight_has_nothing_to_do_with_fakes():
    assert run_preflight(S()).ok and enforce(S()) is None


def test_enforce_modes():
    cfg = dict(llm_provider="ollama", llm_base_url="http://127.0.0.1:9")
    assert enforce(S(llm_preflight="off", **cfg)) is None
    assert not enforce(S(llm_preflight="warn", **cfg)).ok  # logs, does not raise
    with pytest.raises(RuntimeError, match="strict"):
        enforce(S(llm_preflight="strict", **cfg))


def test_warm_up_loads_chat_and_embedding_models_with_keep_alive():
    with MockOllama() as m:
        s = S(llm_provider="ollama", llm_base_url=m.url, llm_model="qwen3:4b", embedding_provider="ollama", embedding_base_url=m.url)
        res = warm_up(s)
        assert [r[0] for r in res] == ["qwen3:4b", "nomic-embed-text"] and all(isinstance(r[1], float) for r in res)
        paths = [p for p, _ in m.calls]
        assert "/api/generate" in paths and "/api/embed" in paths
        gen = next(b for p, b in m.calls if p == "/api/generate")
        assert gen["keep_alive"] == s.llm_keep_alive


def test_warm_up_reports_errors_instead_of_raising():
    res = warm_up(S(llm_provider="ollama", llm_base_url="http://127.0.0.1:9", llm_model="x"))
    assert res[0][0] == "x" and isinstance(res[0][1], str)


def test_chat_models_in_use_includes_role_overrides():
    assert chat_models_in_use(S(llm_model="a", llm_model_triage="b")) == ["a", "b"]


def test_strip_message():
    m = strip_message(AIMessage("<think>hidden</think>Visible."))
    assert m.content == "Visible."
    u = strip_message(AIMessage("<think>never closed"))
    assert u.content == "" and u.additional_kwargs.get("reasoning_unfinished") is True
