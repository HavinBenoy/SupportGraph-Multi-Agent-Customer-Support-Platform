"""Full-graph regression for the real-model findings of the first e2e run (fake FAQ model, scripted triage = what the real 1.7B returned)."""

import pytest

from app.agents.schemas import TriageResult
from app.config import Settings
from app.db.session import session_scope
from app.graph.support import ask_support, build_support_graph
from app.llm.gateway import get_embedding_client
from app.rag.faq import ABSTAIN_MESSAGE
from app.rag.ingest import ingest_tenant
from tests.rag.conftest import settings  # noqa: F401  (fake providers)

T = "qspiders_sample"


class Scripted:
    def __init__(self, **kw):
        self.result = TriageResult(**kw)

    def classify(self, text):
        return self.result


@pytest.fixture
def graph_for():
    s = Settings(_env_file=None)  # fake providers
    with session_scope() as db:
        ingest_tenant(db, T, get_embedding_client(s))
    return lambda **tri: build_support_graph(settings=s, triager=Scripted(**tri))


def ask(g, text, student="anonymous"):
    return ask_support(g, text, tenant_id=T, student_id=student, today="2026-10-24")


def test_out_of_scope_question_with_unsure_triage_goes_to_faq_and_abstains(graph_for):
    out = ask(graph_for(category="other", confidence=0.5), "What is the capital of Australia?")
    assert out["route"] == "knowledge" and out["abstained"] and ABSTAIN_MESSAGE in out["reply"] and not out["citations"]


def test_very_unsure_triage_still_asks_for_detail(graph_for):
    out = ask(graph_for(category="other", confidence=0.3), "hmm")
    assert (out["route"], out["route_reason"]) == ("clarify", "low_confidence")


def test_human_request_escalates_even_when_triage_says_other_at_half_confidence(graph_for):
    out = ask(graph_for(category="other", confidence=0.5), "I want to talk to a person, not a bot.", student="stu_asha")
    assert out["route"] == "escalate" and out["needs_human"] and out["escalation_reason"] == "human_request"


def test_human_request_floor_works_for_anonymous_visitors_too(graph_for):
    out = ask(graph_for(category="other", confidence=0.0), "I want to talk to a person, not a bot.")
    assert out["route"] == "escalate" and out["escalation_reason"] == "human_request"
