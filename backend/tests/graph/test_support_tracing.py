"""The support graph under the real Langfuse SDK with a local stand-in server: each node is its own span, tagged with
tenant/session. Does NOT prove the real Langfuse server renders them: that is `make verify-tracing-support`."""

import time
import uuid

from app.config import Settings
from app.graph.support import ask_support, build_support_graph
from app.observability.tracing import flush_tracing, shutdown_tracing
from tests.graph.test_tracing_integration import _Collector
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401


def test_each_support_node_is_exported_as_a_span(ingested, monkeypatch):
    c = _Collector()
    try:
        s = Settings(_env_file=None, tracing_enabled=True, langfuse_public_key=f"pk-lf-{uuid.uuid4().hex}",
                     langfuse_secret_key="sk-lf-test", langfuse_base_url=c.url)
        monkeypatch.setattr("app.observability.tracing.get_settings", lambda: s)
        out = ask_support(build_support_graph(settings=settings()), "What payment methods do you accept?",
                          tenant_id="qspiders_sample", ticket_id="sup-1")
        flush_tracing()
        shutdown_tracing()
        deadline = time.time() + 10
        while time.time() < deadline and not c.trace_bytes():
            time.sleep(0.2)
        data = c.trace_bytes()
    finally:
        c.close()
    assert out["route"] == "knowledge"
    for expected in (b"triage", b"route", b"knowledge", b"finalize", b"qspiders_sample:sup-1", b"tenant:qspiders_sample"):
        assert expected in data, f"{expected!r} missing from exported spans"
