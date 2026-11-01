"""The FAQ graph under the real Langfuse SDK, with a local stand-in server. Proves each stage (retrieve, generate,
validate / abstain) is exported as its own span, tagged with tenant/session/user. It does NOT prove the real Langfuse
server renders them: that is `make verify-tracing-faq`."""

import time
import uuid

from app.config import Settings
from app.llm.fake_faq import ExtractiveFaqModel
from app.observability.tracing import flush_tracing, instrument_config, shutdown_tracing
from app.rag.faq import build_faq_graph
from tests.graph.test_tracing_integration import _Collector
from tests.rag.conftest import settings


def _run(embedder, url, question, ticket):
    s = Settings(_env_file=None, tracing_enabled=True, langfuse_public_key=f"pk-lf-{uuid.uuid4().hex}",
                 langfuse_secret_key="sk-lf-test", langfuse_base_url=url)
    cfg = instrument_config({"configurable": {"tenant_id": "qspiders_sample"}, "recursion_limit": 10},
                            tenant_id="qspiders_sample", student_id="anonymous", ticket_id=ticket, settings=s)
    out = build_faq_graph(embedder, ExtractiveFaqModel(), settings()).invoke({"question": question}, cfg)
    flush_tracing()
    shutdown_tracing()
    return out


def _exported(collector) -> bytes:
    deadline = time.time() + 10
    while time.time() < deadline and not collector.trace_bytes():
        time.sleep(0.2)
    return collector.trace_bytes()


def test_an_answered_question_exports_retrieve_generate_and_validate_spans(embedder, ingested):
    c = _Collector()
    try:
        out = _run(embedder, c.url, "What payment methods do you accept?", "faq-1")
        data = _exported(c)
    finally:
        c.close()
    assert not out["abstained"]
    for expected in (b"retrieve", b"generate", b"validate", b"qspiders_sample:faq-1", b"tenant:qspiders_sample"):
        assert expected in data, f"{expected!r} missing from exported spans"


def test_an_abstained_question_exports_the_abstain_span_and_no_generate_span(embedder, ingested):
    c = _Collector()
    try:
        out = _run(embedder, c.url, "What is the weather in Mumbai today?", "faq-2")
        data = _exported(c)
    finally:
        c.close()
    assert out["abstained"] and out["reason"] == "no_relevant_context"
    assert b"retrieve" in data and b"abstain" in data and b"generate" not in data  # the model was never called


def test_tracing_never_changes_the_answer(embedder, ingested):
    plain = build_faq_graph(embedder, ExtractiveFaqModel(), settings()).invoke(
        {"question": "What payment methods do you accept?"}, {"configurable": {"tenant_id": "qspiders_sample"}})
    c = _Collector()
    try:
        traced = _run(embedder, c.url, "What payment methods do you accept?", "faq-3")
    finally:
        c.close()
    assert traced["answer"] == plain["answer"] and traced["citations"] == plain["citations"]
