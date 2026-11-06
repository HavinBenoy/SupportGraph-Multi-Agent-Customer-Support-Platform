"""The six mismatches from the first real-model e2e run, through the FULL support graph. Triage outputs are scripted to what the real
qwen3:1.7b returned (diagnostic run); the FAQ model is scripted where the real one misbehaved. Pins routing, abstention, citations,
escalation and the approval pause together. Real-model behaviour itself is checked by `make e2e-local`."""

import re

import pytest
from sqlalchemy import select

from app.agents.schemas import TriageResult
from app.config import Settings
from app.db.models import MakeupRequest
from app.db.session import session_scope
from app.graph.subgraphs.knowledge import build_knowledge_graph
from app.graph.support import ask_support, build_support_graph
from app.llm.gateway import get_embedding_client
from app.rag.faq import ABSTAIN_MESSAGE, build_faq_graph
from app.rag.ingest import ingest_tenant
from tests.rag.test_faq_citation_retry import Seq

T = "qspiders_sample"


class Scripted:
    def __init__(self, **kw):
        self.result = TriageResult(**kw)

    def classify(self, text):
        return self.result


@pytest.fixture
def build():
    s = Settings(_env_file=None)  # offline fakes everywhere
    with session_scope() as db:
        ingest_tenant(db, T, get_embedding_client(s))

    def make(faq_model=None, **triage):
        knowledge = None
        if faq_model is not None:
            knowledge = build_knowledge_graph(faq_graph=build_faq_graph(settings=s, model=faq_model), settings=s)
        return build_support_graph(settings=s, triager=Scripted(**triage), knowledge=knowledge)

    return make


def ask(g, text, student="anonymous"):
    return ask_support(g, text, tenant_id=T, student_id=student, today="2026-10-24")


def first_source(g_text="Can I pay the course fee in instalments?"):
    s = Settings(_env_file=None)
    out = build_faq_graph(settings=s).invoke({"question": g_text}, {"configurable": {"tenant_id": T}})
    return out["citations"][0]


# 1. faq-fee: the real triage said `other` 0.8 in the diagnostic -> knowledge -> a cited answer
def test_faq_fee_with_other_triage_is_answered_with_a_citation(build):
    out = ask(build(category="other", confidence=0.8), "Can I pay the course fee in instalments?")
    assert out["route"] == "knowledge" and not out["abstained"] and out["citations"]


# 1b. ... and when triage wrongly says concession_request the router still escalates (we do not silently override a model's escalation)
def test_a_model_escalation_is_never_overridden_by_the_faq_path(build):
    out = ask(build(category="concession_request", confidence=0.9), "Can I pay the course fee in instalments?")
    assert out["route"] == "escalate" and out["needs_human"]


# 2. faq-courses: uncited first reply, cited second -> answered; never cites -> abstains
def test_faq_courses_uncited_then_cited_is_answered(build):
    cite = first_source("Which programmes do you offer under PySpiders?")
    g = build(faq_model=Seq(script=["Under PySpiders we offer Python Full Stack.", f"Under PySpiders we offer Python Full Stack [{cite}]."]),
              category="assessment", confidence=0.8)
    out = ask(g, "Which programmes do you offer under PySpiders?")
    assert out["route"] == "knowledge" and not out["abstained"] and out["citations"] == [cite]


def test_faq_courses_model_that_never_cites_abstains(build):
    g = build(faq_model=Seq(script=["Python Full Stack.", "Python Full Stack and Data Science."]), category="assessment", confidence=0.8)
    out = ask(g, "Which programmes do you offer under PySpiders?")
    assert out["abstained"] and out["citations"] == [] and ABSTAIN_MESSAGE in out["reply"]


# 3 + 4. out-of-scope and unanswerable with `other` 0.5: FAQ path, standard abstention, nothing invented
@pytest.mark.parametrize("text", ["Will I definitely get a salary of 20 LPA after the course?", "What is the capital of Australia?"])
def test_unsure_other_triage_goes_to_the_faq_and_abstains(build, text):
    g = build(faq_model=Seq(script=["INSUFFICIENT_CONTEXT"]), category="other", confidence=0.5)
    out = ask(g, text)
    assert out["route"] == "knowledge" and out["route_reason"] == "other_low_confidence"
    assert out["abstained"] and out["citations"] == [] and out["reply"] == ABSTAIN_MESSAGE


def test_unsure_other_triage_cannot_smuggle_an_uncited_answer(build):
    g = build(faq_model=Seq(script=["It is Canberra.", "It is Canberra."]), category="other", confidence=0.5)
    out = ask(g, "What is the capital of Australia?")
    assert out["abstained"] and "Canberra" not in out["reply"]


# 5. human: triage `other` 0.5 -> floor escalates
def test_human_request_escalates(build):
    out = ask(build(category="other", confidence=0.5), "I want to talk to a person, not a bot.", student="stu_asha")
    assert out["route"] == "escalate" and out["escalation_reason"] == "human_request" and out["needs_human"]


# 6. write: the labelled make-up ticket (T038) pauses for staff approval and writes only a pending row
def test_labelled_makeup_ticket_pauses_for_staff_approval(build):
    out = ask(build(category="makeup_request", confidence=0.9), "Please arrange a make-up class for the session I missed in PYD-301.",
              student="stu_meera")
    assert out["route"] == "write" and out["awaiting"] == "staff"
    with session_scope() as db:
        (req,) = db.scalars(select(MakeupRequest)).all()
    assert (req.status, req.student_id, req.batch_id) == ("pending", "stu_meera", "PYD-301")


def test_makeup_request_for_an_anonymous_visitor_is_not_written(build):
    out = ask(build(category="makeup_request", confidence=0.9), "Please arrange a make-up class for the session I missed in PYD-301.")
    assert (out["route"], out["route_reason"]) == ("clarify", "sign_in_required")
    with session_scope() as db:
        assert db.scalars(select(MakeupRequest)).all() == []


# 6b. the invented e2e message: there is no "reschedule" action, so nothing is written and the reply promises nothing
def test_unsupported_reschedule_request_writes_nothing_and_promises_nothing(build):
    out = ask(build(category="schedule", confidence=0.9), "Please reschedule my Saturday batch to the weekday evening batch.", student="stu_asha")
    assert out["route"] == "knowledge" and not out.get("awaiting")
    assert not re.search(r"\b(i have|i've|i will|i'll) (rescheduled|moved|changed)\b", out["reply"], re.I)
    with session_scope() as db:
        assert db.scalars(select(MakeupRequest)).all() == []
