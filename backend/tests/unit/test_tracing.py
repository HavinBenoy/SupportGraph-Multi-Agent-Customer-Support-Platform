"""Tracing config logic. No Langfuse server, no network."""

import logging

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.observability import tracing
from app.observability.tracing import instrument_config, tracing_enabled

BASE = {"configurable": {"tenant_id": "qspiders_sample", "student_id": "stu_asha"}, "recursion_limit": 10}
IDS = dict(tenant_id="qspiders_sample", student_id="stu_asha", ticket_id="T-1")


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_tracing_is_off_by_default_and_config_is_untouched():
    s = _settings()
    assert tracing_enabled(s) is False
    assert instrument_config(BASE, settings=s, **IDS) is BASE  # same object: zero side effects


def test_enabled_without_keys_warns_and_stays_off(caplog):
    s = _settings(tracing_enabled=True)
    with caplog.at_level(logging.WARNING):
        out = instrument_config(BASE, settings=s, **IDS)
    assert out is BASE
    assert "without tracing" in caplog.text


def test_enabled_with_only_one_key_stays_off():
    assert tracing_enabled(_settings(tracing_enabled=True, langfuse_public_key="pk-lf-x")) is False


def test_init_failure_degrades_to_untraced_run(monkeypatch, caplog):
    def boom(_):
        raise RuntimeError("langfuse exploded")

    monkeypatch.setattr(tracing, "_make_handler", boom)
    s = _settings(tracing_enabled=True, langfuse_public_key="pk-lf-x", langfuse_secret_key="sk-lf-secret-value")
    with caplog.at_level(logging.WARNING):
        out = instrument_config(BASE, settings=s, **IDS)
    assert out is BASE
    assert "Tracing disabled for this run" in caplog.text
    assert "sk-lf-secret-value" not in caplog.text


def test_enabled_adds_callback_and_trace_attributes(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(tracing, "_make_handler", lambda s: sentinel)
    s = _settings(tracing_enabled=True, langfuse_public_key="pk-lf-x", langfuse_secret_key="sk-lf-y")
    base = {**BASE, "callbacks": ["existing"], "metadata": {"keep": 1}}
    out = instrument_config(base, settings=s, **IDS)

    assert out["callbacks"] == ["existing", sentinel]
    assert out["metadata"]["keep"] == 1
    assert out["metadata"]["langfuse_session_id"] == "qspiders_sample:T-1"
    assert out["metadata"]["langfuse_user_id"] == "stu_asha"
    assert out["metadata"]["langfuse_tags"] == ["tenant:qspiders_sample"]
    assert out["configurable"] == BASE["configurable"]  # trusted scope is carried through unchanged
    assert "callbacks" not in BASE  # input not mutated


def test_secret_key_is_masked_in_settings_repr():
    s = _settings(langfuse_secret_key="sk-lf-topsecret")
    assert isinstance(s.langfuse_secret_key, SecretStr)
    assert "sk-lf-topsecret" not in repr(s)
    assert "sk-lf-topsecret" not in str(s)


def test_standard_langfuse_env_var_names_are_read(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-from-env")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-from-env")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://localhost:9999")
    monkeypatch.setenv("SG_TRACING_ENABLED", "true")
    s = Settings(_env_file=None)
    assert (s.langfuse_public_key, s.langfuse_base_url, s.tracing_enabled) == ("pk-lf-from-env", "http://localhost:9999", True)
    assert tracing_enabled(s) is True


def test_flush_and_shutdown_are_safe_noops_when_never_initialised(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None)
    tracing.flush_tracing()
    tracing.shutdown_tracing()


def test_test_suite_itself_runs_with_tracing_off():
    from app.config import get_settings
    assert get_settings().tracing_enabled is False
