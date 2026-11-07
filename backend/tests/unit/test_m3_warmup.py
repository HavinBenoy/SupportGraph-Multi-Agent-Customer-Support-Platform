"""M3 model lifecycle: the warm-up preloads with the SAME runner options as real calls, runs sequentially, once, never blocks and never crashes."""

import threading
import time
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm import warmup
from app.llm.gateway import get_chat_model
from app.llm.preflight import runner_options, warm_up
from app.observability import stages
from tests.bench.mock_ollama import MockOllama


def S(url, **kw):
    return Settings(_env_file=None, llm_provider="ollama", llm_base_url=url, llm_model="qwen3:1.7b", embedding_provider="ollama", embedding_base_url=url, **kw)


def app_stub():
    return SimpleNamespace(state=SimpleNamespace())


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


@pytest.fixture(autouse=True)
def reset_warm_flag():
    yield
    stages.mark_warm(False)


def test_the_preload_uses_exactly_the_options_the_real_calls_send():
    """Ollama starts a new runner (a full reload, seconds on a CPU) when context size or thread count differ from the loaded one."""
    with MockOllama() as m:
        s = S(m.url, llm_num_thread=4, llm_num_ctx=3072)
        warm_up(s)
        gen = next(b for p, b in m.calls if p == "/api/generate")
        get_chat_model("faq", s).invoke("hello")
        chat = next(b for p, b in m.calls if p == "/api/chat")
        for k in ("num_ctx", "num_thread"):
            assert gen["options"][k] == chat["options"][k] == runner_options(s)[k]
        assert gen["options"]["num_predict"] == 1 and gen["keep_alive"] == s.llm_keep_alive


def test_without_a_thread_setting_neither_side_pins_threads():
    with MockOllama() as m:
        s = S(m.url)
        warm_up(s)
        get_chat_model("faq", s).invoke("hello")
        assert "num_thread" not in next(b for p, b in m.calls if p == "/api/generate")["options"]
        assert "num_thread" not in next(b for p, b in m.calls if p == "/api/chat")["options"]


def test_the_steps_run_one_after_another_and_all_report_ok():
    with MockOllama() as m:
        detail = warmup.warm_llm(S(m.url))
        assert [p for p, _ in m.calls] == ["/api/generate", "/api/embed", "/api/chat", "/api/embed"]     # models, then triage, then retrieval
        assert all(detail[k]["ok"] for k in ("ollama", "triage", "retrieval")) and detail["ollama"]["ms"] >= 0


def test_warm_up_failure_is_reported_and_never_raises():
    detail = warmup.warm_llm(S("http://127.0.0.1:9"))
    assert detail["ollama"]["ok"] is False and detail["triage"]["ok"] is False and "error" in detail["ollama"]
    assert detail["retrieval"]["ok"] is False                                                           # each step failed on its own; the sequence finished


def test_priming_can_be_switched_off():
    with MockOllama() as m:
        detail = warmup.warm_llm(S(m.url, warmup_prime_triage=False))
        assert set(detail) == {"ollama"} and [p for p, _ in m.calls] == ["/api/generate", "/api/embed"]


def test_the_background_warm_up_runs_once_and_marks_the_process_warm():
    with MockOllama() as m:
        app, s = app_stub(), S(m.url)
        warmup.start_llm_warmup(app, s)
        warmup.start_llm_warmup(app, s)                                                                 # a second request starts nothing
        wait_for(lambda: app.state.warm["llm"] == "ready")
        assert [p for p, _ in m.calls].count("/api/generate") == 1 and stages.is_warm()
        warmup.start_llm_warmup(app, s)
        assert [p for p, _ in m.calls].count("/api/generate") == 1
        assert warmup.warm_report(app)["steps"]["ollama"]["ok"] is True


def test_a_failed_background_warm_up_is_reported_and_does_not_mark_the_process_warm():
    app = app_stub()
    warmup.start_llm_warmup(app, S("http://127.0.0.1:9"))
    wait_for(lambda: app.state.warm["llm"] == "failed")
    assert not stages.is_warm() and warmup.warm_report(app)["llm"] == "failed"


def test_nothing_starts_for_fake_providers_or_when_switched_off():
    for s in (Settings(_env_file=None), S("http://127.0.0.1:9", llm_warmup=False)):
        app = app_stub()
        before = threading.active_count()
        warmup.start_llm_warmup(app, s)
        assert getattr(app.state, "warm", None) is None and threading.active_count() == before


def test_the_start_does_not_block_the_caller():
    app = app_stub()
    t0 = time.perf_counter()
    warmup.start_llm_warmup(app, S("http://10.255.255.1", llm_timeout_seconds=5))                       # an unroutable address would hang a synchronous call
    assert time.perf_counter() - t0 < 0.5


def real_voice(**kw):
    return Settings(_env_file=None, voice_provider="real", **kw)


def test_voice_warm_up_failure_is_reported_not_raised():
    app = app_stub()

    def boom():
        raise FileNotFoundError("piper voice missing")
    warmup.start_voice_warmup(app, real_voice(), boom)
    wait_for(lambda: app.state.voice_warm == "failed")
    assert warmup.warm_report(app)["steps"]["voice"] == {"ok": False, "ms": pytest.approx(0, abs=500), "error": "FileNotFoundError"}


def test_voice_warm_up_succeeds_once_and_waits_for_the_llm_warm_up(monkeypatch):
    app, order = app_stub(), []
    st = warmup._state(app)
    st["llm"] = "running"                                                                                # the LLM warm-up is still busy
    warmup.start_voice_warmup(app, real_voice(), lambda: order.append("voice"))
    warmup.start_voice_warmup(app, real_voice(), lambda: order.append("second"))                         # ignored
    time.sleep(0.5)
    assert order == [] and app.state.voice_warm == "running"                                             # never concurrent with the model load
    st["llm"] = "ready"
    wait_for(lambda: app.state.voice_warm == "ready")
    assert order == ["voice"]


def test_voice_warm_up_is_a_no_op_for_the_fake_provider():
    app = app_stub()
    warmup.start_voice_warmup(app, Settings(_env_file=None, voice_provider="fake"), lambda: pytest.fail("must not load"))
    assert getattr(app.state, "voice_warm", None) is None
