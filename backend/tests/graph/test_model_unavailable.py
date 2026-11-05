"""When the language model is down, the graph answers with fixed wording instead of a 500 or a made-up reply."""

import pytest

from app.graph.nodes import replies
from app.graph.support import ask_support, build_support_graph
from app.llm.errors import ModelUnavailable
from app.tools.scope import ANONYMOUS
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401


class _Down:
    def invoke(self, *a, **k):
        raise ModelUnavailable("ConnectionError")


def _ask(graph, text, student=ANONYMOUS):
    return ask_support(graph, text, tenant_id="qspiders_sample", student_id=student, today="2026-10-24")


def test_knowledge_branch_degrades(ingested):
    g = build_support_graph(settings=settings(), knowledge=_Down())
    out = _ask(g, "Can I pay the fee in instalments?")
    assert out["reply"].startswith(replies.MODEL_UNAVAILABLE) and out["abstained"] and not out["citations"]


def test_action_branch_degrades(ingested):
    g = build_support_graph(settings=settings(), action=_Down())
    out = _ask(g, "What is my attendance in PYF-101?", student="stu_asha")
    assert out["reply"] == replies.MODEL_UNAVAILABLE and out["tools_called"] == []
