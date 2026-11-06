"""Opt-in: the same plumbing against a REAL Ollama. Skipped unless SG_TEST_OLLAMA=1 (needs `ollama serve`, qwen3:1.7b and nomic-embed-text pulled).
    SG_TEST_OLLAMA=1 ../.venv/bin/python -m pytest tests/graph/test_real_ollama.py -q
Asserts safety only (no reasoning leak, abstention on an unanswerable question), never answer quality."""

import os

import pytest

from app.config import Settings
from app.db.session import session_scope
from app.graph.support import ask_support, build_support_graph
from app.llm.gateway import get_embedding_client
from app.llm.preflight import run_preflight
from app.rag.ingest import ingest_tenant

pytestmark = pytest.mark.skipif(os.environ.get("SG_TEST_OLLAMA") != "1", reason="set SG_TEST_OLLAMA=1 to run against a real Ollama")
T = "qspiders_sample"


@pytest.fixture
def real():
    s = Settings(_env_file=None, llm_provider="ollama", llm_model="qwen3:1.7b", embedding_provider="ollama", llm_timeout_seconds=180)
    pre = run_preflight(s)
    if not pre.ok:
        pytest.skip("; ".join(pre.problems))
    with session_scope() as db:
        ingest_tenant(db, T, get_embedding_client(s))
    return build_support_graph(settings=s)


def test_real_faq_answer_has_no_reasoning(real):
    out = ask_support(real, "Can I pay the course fee in instalments?", tenant_id=T, today="2026-10-24")
    assert "<think>" not in out["reply"] and "let me" not in out["reply"].lower()
    assert out["abstained"] or out["citations"]


def test_real_unanswerable_question_does_not_invent_an_answer(real):
    out = ask_support(real, "Will I definitely get a salary of 20 LPA?", tenant_id=T, today="2026-10-24")
    assert out["abstained"] or out["citations"]  # a standard abstention, or an answer that passed citation validation
