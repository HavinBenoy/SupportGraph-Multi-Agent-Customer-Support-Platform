"""Real Langfuse SDK + real graph, with a throwaway local HTTP server standing in for Langfuse.

This proves the integration actually emits spans for the graph nodes and tool calls and tags them
with tenant/session/user -- with no Langfuse server, no LLM key, no external network.
It does NOT prove the real Langfuse server renders them; that is `make verify-tracing`.
"""

import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from langchain_core.messages import HumanMessage

from app.config import Settings
from app.graph.builder import build_graph
from app.observability.tracing import flush_tracing, instrument_config, shutdown_tracing


class _Collector:
    def __init__(self):
        self.requests: list[tuple[str, str, bytes]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _body(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                return gzip.decompress(raw) if self.headers.get("Content-Encoding") == "gzip" else raw

            def do_POST(self):
                outer.requests.append(("POST", self.path, self._body()))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def do_GET(self):
                outer.requests.append(("GET", self.path, b""))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"id": "p1", "name": "test"}]}).encode())

            def log_message(self, *a):  # silence
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def trace_bytes(self) -> bytes:
        return b"".join(b for m, p, b in self.requests if m == "POST" and "otel" in p)

    def close(self):
        self.server.shutdown()


@pytest.fixture
def collector():
    c = _Collector()
    yield c
    c.close()


def _traced_run(base_url: str, public_key: str, ticket: str):
    s = Settings(_env_file=None, tracing_enabled=True, langfuse_public_key=public_key,
                 langfuse_secret_key="sk-lf-test", langfuse_base_url=base_url)
    cfg = {"configurable": {"tenant_id": "qspiders_sample", "student_id": "stu_asha", "today": "2026-10-24"},
           "recursion_limit": 10}
    cfg = instrument_config(cfg, tenant_id="qspiders_sample", student_id="stu_asha", ticket_id=ticket, settings=s)
    result = build_graph().invoke({"messages": [HumanMessage("What is my attendance in PYF-101?")]}, cfg)
    return cfg, result


def test_traced_run_exports_graph_and_tool_spans(collector):
    cfg, result = _traced_run(collector.url, "pk-lf-integration-1", "T-42")
    flush_tracing()
    shutdown_tracing()

    assert cfg["callbacks"], "handler should be attached when tracing is on"
    assert "80.0%" in result["messages"][-1].content  # answer unaffected by tracing

    deadline = time.time() + 10
    while time.time() < deadline and not collector.trace_bytes():
        time.sleep(0.2)
    data = collector.trace_bytes()
    assert data, f"no OTLP trace export received; saw: {[(m, p) for m, p, _ in collector.requests]}"
    for expected in (b"agent", b"tools", b"get_my_attendance", b"qspiders_sample:T-42", b"stu_asha", b"tenant:qspiders_sample"):
        assert expected in data, f"{expected!r} missing from exported spans"


def test_unreachable_langfuse_never_breaks_or_stalls_the_run():
    started = time.time()
    # Nothing listens on this port.
    cfg, result = _traced_run("http://127.0.0.1:9", "pk-lf-integration-2", "T-43")
    assert "80.0%" in result["messages"][-1].content
    shutdown_tracing()  # must return (best-effort), not raise
    assert time.time() - started < 30


def test_untraced_run_is_identical_in_behavior(asha_cfg):
    result = build_graph().invoke({"messages": [HumanMessage("What is my attendance in PYF-101?")]}, asha_cfg)
    assert [m.type for m in result["messages"]] == ["human", "ai", "tool", "ai"]
