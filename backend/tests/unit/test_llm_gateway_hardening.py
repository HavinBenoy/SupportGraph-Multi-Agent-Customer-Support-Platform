import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.llm.errors import ExternalEndpointRefused, ModelUnavailable
from app.llm.gateway import get_chat_model, get_embedding_client
from app.llm.guard import TokenBudget, check_endpoint_allowed, describe_llm, invoke_guarded, is_local_endpoint


@pytest.mark.parametrize("url,local", [
    ("http://localhost:11434", True), ("http://127.0.0.1:11434", True), ("http://ollama:11434", True),
    ("http://192.168.1.20:11434", True), ("http://10.0.0.5:8000/v1", True),
    ("https://api.groq.com/openai/v1", False), ("https://generativelanguage.googleapis.com", False),
    ("http://8.8.8.8:11434", False), ("http://localhost.evil.com", False),
])
def test_is_local_endpoint(url, local):
    assert is_local_endpoint(url) is local


def test_external_refused_unless_allowed():
    with pytest.raises(ExternalEndpointRefused):
        check_endpoint_allowed("https://api.groq.com/openai/v1", False, what="chat model")
    check_endpoint_allowed("https://api.groq.com/openai/v1", True, what="chat model")


def test_gateway_refuses_external_chat_and_embeddings():
    s = Settings(llm_provider="openai_compat", llm_base_url="https://api.groq.com/openai/v1", _env_file=None)
    with pytest.raises(ExternalEndpointRefused):
        get_chat_model("faq", s)
    e = Settings(embedding_provider="openai_compat", embedding_base_url="https://api.example.com/v1", _env_file=None)
    with pytest.raises(ExternalEndpointRefused):
        get_embedding_client(e)


def test_ollama_options_and_role_override():
    s = Settings(llm_provider="ollama", llm_model="qwen3:4b", llm_model_triage="qwen3:1.7b", _env_file=None)
    m = get_chat_model("faq", s)
    assert m.model == "qwen3:4b" and m.num_ctx == 4096 and m.num_predict == 400 and m.keep_alive == "30m"
    assert not m.reasoning
    assert get_chat_model("triage", s).model == "qwen3:1.7b"


def test_openai_compat_options():
    s = Settings(llm_provider="openai_compat", llm_base_url="http://localhost:8080/v1", llm_timeout_seconds=12, llm_max_retries=0, _env_file=None)
    m = get_chat_model("action", s)
    assert m.request_timeout == 12 and m.max_retries == 0 and m.max_tokens == 400


def test_budget_blocks_after_limit_and_zero_is_unlimited():
    b = TokenBudget(100)
    b.check(); b.add(100)
    with pytest.raises(ModelUnavailable):
        b.check()
    u = TokenBudget(0); u.add(10**9); u.check()


class _Boom:
    def invoke(self, *a, **k):
        raise ConnectionError("refused http://secret-host student text")


class _Ok:
    def invoke(self, *a, **k):
        return AIMessage("hi", usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})


def test_invoke_guarded_normalises_errors_without_leaking_text():
    s = Settings(_env_file=None)
    with pytest.raises(ModelUnavailable) as ei:
        invoke_guarded(_Boom(), [], s)
    assert ei.value.reason == "ConnectionError" and "student" not in str(ei.value)


def test_invoke_guarded_counts_tokens_and_enforces_budget():
    s = Settings(llm_daily_token_budget=10, _env_file=None)
    invoke_guarded(_Ok(), [], s)
    invoke_guarded(_Ok(), [], s)  # 7 < 10 before the 2nd call; now 14
    with pytest.raises(ModelUnavailable):
        invoke_guarded(_Ok(), [], s)


def test_describe_llm_has_no_key_and_flags_external():
    s = Settings(llm_provider="openai_compat", llm_base_url="https://api.groq.com/openai/v1", llm_api_key="sk-SECRET",
                 allow_external_llm=True, _env_file=None)
    text = "\n".join(describe_llm(s))
    assert "EXTERNAL" in text and "WARNING" in text and "SECRET" not in text
    assert "fake" in describe_llm(Settings(_env_file=None))[0]


def test_m1_default_models_are_the_non_thinking_4b_and_the_small_triage_model():
    from app.llm.gateway import _role_model

    s = Settings(_env_file=None)
    assert s.llm_model == "qwen3:4b-instruct-2507-q4_K_M" and s.llm_model_triage == "qwen3:1.7b"
    assert _role_model("faq", s) == s.llm_model and _role_model("triage", s) == "qwen3:1.7b"
    assert "thinking" not in s.llm_model and s.llm_model != "qwen3:4b"  # plain qwen3:4b is the thinking-only build
