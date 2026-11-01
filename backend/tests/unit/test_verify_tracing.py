"""The Langfuse v4 verifier. No Langfuse server needed.

Layer 1 (fake client): the polling / classification logic.
Layer 2 (real SDK client + local stand-in HTTP server): the actual request the SDK sends, and that the legacy
`/api/public/traces` endpoint (404 on Langfuse v4) is never used.
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlparse

import pytest
from langfuse import Langfuse
from langfuse.api.core.api_error import ApiError

from scripts import verify_tracing as vt

SESSION = "qspiders_sample:verify-abc"


def obs(name, trace_id="t1", session_id=SESSION):
    return NS(id=f"o-{name}", name=name, trace_id=trace_id, session_id=session_id)


class FakeObservations:
    """Scripted get_many: each call pops the next scripted reply (a list of obs, or an exception).
    The last reply repeats. Records the filters it was called with."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def get_many(self, **kw):
        self.calls.append(kw)
        reply = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(reply, Exception):
            raise reply
        data, cursor = reply if isinstance(reply, tuple) else (reply, None)
        return NS(data=data, meta=NS(cursor=cursor))


def fake_client(script):
    return NS(api=NS(observations=FakeObservations(script)))


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(client, timeout=30.0):
    c = Clock()
    return vt.verify_session(client, SESSION, timeout=timeout, interval=3, sleep=c.sleep, clock=c.now, log=lambda m: None)


# ---------------- layer 1: logic ----------------

def test_success_when_all_expected_spans_are_present():
    r = run(fake_client([[obs("supportgraph.ticket"), obs("agent"), obs("tools"), obs("get_my_attendance")]]))
    assert r["ok"] and r["trace_id"] == "t1" and r["missing"] == set()


def test_child_spans_without_a_session_id_are_found_through_the_trace_id():
    # By session we only see the root; the children are only discoverable by trace id.
    by_session = [obs("supportgraph.ticket")]
    by_trace = [obs("supportgraph.ticket"), obs("agent", session_id=None), obs("tools", session_id=None),
                obs("get_my_attendance", session_id=None)]
    client = fake_client([by_session, by_trace])
    r = run(client)
    assert r["ok"]
    filters = [tuple(sorted(k for k in c if c[k] is not None and k in ("session_id", "trace_id")))
               for c in client.api.observations.calls]
    assert ("session_id",) in filters and ("trace_id",) in filters


def test_keeps_polling_until_late_spans_arrive():
    partial = [obs("supportgraph.ticket"), obs("agent")]
    full = partial + [obs("tools"), obs("get_my_attendance")]
    client = fake_client([[], partial, partial, partial, partial, full])  # nothing, then partial, then complete
    assert run(client)["ok"]


def test_reports_exactly_which_spans_are_missing_after_timeout():
    client = fake_client([[obs("supportgraph.ticket"), obs("agent"), obs("tools")]])  # no get_my_attendance
    r = run(client, timeout=9)
    assert not r["ok"] and r["trace_id"] == "t1" and r["missing"] == {"get_my_attendance"}


def test_no_observations_at_all_times_out_cleanly():
    r = run(fake_client([[]]), timeout=9)
    assert not r["ok"] and r["trace_id"] is None and r["missing"] == vt.EXPECTED_SPANS


def test_transient_errors_are_retried():
    full = [obs("agent"), obs("tools"), obs("get_my_attendance")]
    client = fake_client([ConnectionError("server starting"), ApiError(status_code=503), full])
    assert run(client)["ok"]


@pytest.mark.parametrize("status", [401, 403])
def test_bad_keys_fail_fast_with_a_clear_message(status):
    client = fake_client([ApiError(status_code=status)])
    with pytest.raises(vt.VerifyError, match="rejected the API keys"):
        run(client)
    assert len(client.api.observations.calls) == 1  # did not keep retrying


def test_404_fails_fast_and_names_the_base_url():
    with pytest.raises(vt.VerifyError, match="LANGFUSE_BASE_URL"):
        run(fake_client([ApiError(status_code=404)]))


def test_pagination_follows_the_cursor():
    page1 = ([obs("supportgraph.ticket")], "next-page")
    page2 = ([obs("agent"), obs("tools"), obs("get_my_attendance")], None)
    client = fake_client([page1, page2, ([obs("agent"), obs("tools"), obs("get_my_attendance")], None)])
    rows = vt.fetch_observations(client, session_id=SESSION)
    assert [o.name for o in rows] == ["supportgraph.ticket", "agent", "tools", "get_my_attendance"]
    assert client.api.observations.calls[1]["cursor"] == "next-page"


def test_pagination_has_a_hard_stop():
    endless = ([obs("agent")], "again")
    client = fake_client([endless])
    assert len(vt.fetch_observations(client, session_id=SESSION)) == vt.MAX_PAGES


def test_diagnose_lists_what_langfuse_did_receive():
    lines: list[str] = []
    roots = [NS(name="supportgraph.ticket", session_id="other:session")]
    vt.diagnose(fake_client([roots]), datetime.now(timezone.utc), log=lines.append)
    assert any("supportgraph.ticket" in ln and "other:session" in ln for ln in lines)
    lines.clear()
    vt.diagnose(fake_client([[]]), datetime.now(timezone.utc), log=lines.append)
    assert any("no root observations" in ln for ln in lines)


# ---------------- layer 2: the real SDK against a v4-like stand-in server ----------------

class V4StandIn:
    """Mimics Langfuse v4: serves /api/public/v2/observations, answers the legacy traces endpoint with 404."""

    def __init__(self, observations, observations_status=200):
        self.requests: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                url = urlparse(self.path)
                if url.path == "/api/public/v2/observations" and observations_status != 200:
                    body, code = {"message": "error"}, observations_status
                elif url.path == "/api/public/v2/observations":
                    q = parse_qs(url.query)
                    rows = [o for o in observations
                            if q.get("sessionId", [o["sessionId"]])[0] == o["sessionId"]
                            and q.get("traceId", [o["traceId"]])[0] == o["traceId"]]
                    body, code = {"data": rows, "meta": {}}, 200
                else:  # includes the legacy /api/public/traces
                    body, code = {"message": "Not Found"}, 404
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_POST(self):  # OTLP span export from the tracing client
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append(f"POST {self.path}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def raw(name, session=SESSION, parent=None):
    return {"id": f"o-{name}", "traceId": "t1", "projectId": "supportgraph-dev", "type": "SPAN", "name": name,
            "startTime": "2026-10-26T04:00:00.000Z", "sessionId": session, "parentObservationId": parent}


@pytest.fixture
def reader_for():
    created = []

    def make(observations, observations_status=200):
        srv = V4StandIn(observations, observations_status)
        # Unique key per client: the SDK caches its API client by public key, so reusing a key would silently
        # keep talking to an earlier (shut-down) stand-in server.
        client = Langfuse(public_key=f"pk-lf-{uuid.uuid4().hex}", secret_key="sk-lf-test", base_url=srv.url,
                          tracing_enabled=False)
        created.append(srv)
        return client, srv

    yield make
    for s in created:
        s.server.shutdown()


def test_real_sdk_client_verifies_against_v4_style_server_without_touching_legacy_endpoint(reader_for):
    client, srv = reader_for([raw("supportgraph.ticket"), raw("agent"), raw("tools"), raw("get_my_attendance")])
    r = vt.verify_session(client, SESSION, timeout=5, interval=0.1)
    assert r["ok"] and r["trace_id"] == "t1"
    assert srv.requests, "expected the SDK to call the server"
    assert all(p.startswith("/api/public/v2/observations") for p in srv.requests), srv.requests
    assert not any("/api/public/traces" in p for p in srv.requests)
    assert any("sessionId=" in p for p in srv.requests) and any("traceId=t1" in p for p in srv.requests)


def test_real_sdk_reports_a_different_session_as_not_found(reader_for):
    client, _ = reader_for([raw("agent", session="qspiders_sample:someone-else")])
    r = vt.verify_session(client, SESSION, timeout=0.3, interval=0.1)
    assert not r["ok"] and r["trace_id"] is None


@pytest.mark.parametrize("status, message", [(404, "LANGFUSE_BASE_URL"), (401, "rejected the API keys")])
def test_real_sdk_http_errors_become_actionable_errors_without_retry_loops(reader_for, status, message):
    client, srv = reader_for([], observations_status=status)
    with pytest.raises(vt.VerifyError, match=message):
        vt.verify_session(client, SESSION, timeout=5, interval=0.1)
    assert len(srv.requests) == 1  # failed fast instead of polling for the whole timeout


def test_main_sequence_traced_run_then_shutdown_then_read_with_the_same_key_and_url():
    """Mirrors scripts/verify_tracing.main(): the tracing client exports and shuts down, then a *reader* client
    with the SAME public key and URL must still be able to query. Uses the real graph and the real exporter."""
    from langchain_core.messages import HumanMessage

    from app.config import Settings
    from app.graph.builder import build_graph
    from app.observability.tracing import instrument_config, shutdown_tracing

    pk = f"pk-lf-{uuid.uuid4().hex}"
    srv = V4StandIn([raw("supportgraph.ticket"), raw("agent"), raw("tools"), raw("get_my_attendance")])
    try:
        settings = Settings(_env_file=None, tracing_enabled=True, langfuse_public_key=pk,
                            langfuse_secret_key="sk-lf-test", langfuse_base_url=srv.url)
        cfg = instrument_config({"configurable": {"tenant_id": "qspiders_sample", "student_id": "stu_asha",
                                                 "today": "2026-10-24"}, "recursion_limit": 10},
                                tenant_id="qspiders_sample", student_id="stu_asha", ticket_id="verify-abc", settings=settings)
        result = build_graph().invoke({"messages": [("human", "What is my attendance in PYF-101?")]}, cfg)
        assert "80.0%" in result["messages"][-1].content
        shutdown_tracing()

        reader = Langfuse(public_key=pk, secret_key="sk-lf-test", base_url=srv.url, tracing_enabled=False)
        r = vt.verify_session(reader, SESSION, timeout=5, interval=0.1)
        assert r["ok"], r
        assert any(p.startswith("POST") and "otel" in p for p in srv.requests), "tracing client should have exported spans"
        assert not any("/api/public/traces" in p for p in srv.requests)
    finally:
        srv.server.shutdown()


def test_faq_workflow_expects_the_three_answering_stages_and_its_question_really_gets_answered():
    """The verifier question must take the answered path (retrieve -> generate -> validate), not abstain, or the
    expected `generate`/`validate` spans could never appear and `make verify-tracing-faq` would always fail."""
    from app.db.session import session_scope
    from app.llm.gateway import get_embedding_client
    from app.rag.ingest import ingest_tenant
    from tests.rag.conftest import settings

    assert vt.EXPECTED_BY_WORKFLOW["faq"] == {"retrieve", "generate", "validate"}
    with session_scope() as db:
        ingest_tenant(db, "qspiders_sample", get_embedding_client(settings()))
    summary = vt._run_workflow("faq", "qspiders_sample", "verify-faq-1")
    assert "UPI" in summary and "abstained" not in summary
