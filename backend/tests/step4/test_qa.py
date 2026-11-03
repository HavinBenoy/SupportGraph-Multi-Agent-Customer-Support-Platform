"""QA validation: the deterministic checks, the revise/fallback loop, the optional LLM critic."""

import pytest

from app.config import Settings
from app.graph.support import ask_support, build_support_graph
from app.qa.checks import QaIssue, question_entities, run_checks
from app.qa.critic import LlmCritic, LlmReviser
from app.qa.revise import DeterministicReviser
from tests.rag.conftest import StubChatModel, settings
from tests.step4.conftest import Escalation, embedder, ingested, rows  # noqa: F401

SRC = [{"source_id": "fees#1", "heading": "Fees > Python Full Stack", "text": "The Python Full Stack fee is INR 60,000 in two instalments."}]


def checks(reply, question="What is the fee for Python Full Stack?", **kw):
    args = dict(reply=reply, question=question, student_id="stu_asha", markers=["secret rule 7"], route="knowledge", abstained=False,
                citations=["fees#1"], sources=SRC)
    return {i.code for i in run_checks(**{**args, **kw})}


def test_clean_answer_passes():
    assert checks("The Python Full Stack fee is INR 60,000 [fees#1].") == set()


@pytest.mark.parametrize("reply,code", [
    ("", "empty_reply"),
    ("Per the Secret Rule 7 you get a waiver.", "internal_leak"),
    ("Rahul (stu_rahul) has 60% attendance.", "other_student_id"),
    ("We guarantee a job after the course.", "forbidden_promise"),
    ("I have approved your concession.", "forbidden_promise"),
    ("Your refund will be processed in 3 days.", "forbidden_promise"),
    ("The fee is INR 99,000 [fees#1].", "unsupported_number"),
    ("Call Rahul on 9876543210 or mail rahul@example.com.", "pii_in_reply"),
])
def test_each_check_fires(reply, code):
    assert code in checks(reply)


def test_negated_promise_is_honest_and_allowed():
    assert "forbidden_promise" not in checks("Placement is not guaranteed; we do not guarantee a job. Fee INR 60,000 [fees#1].")


def test_contact_details_the_student_typed_themselves_are_not_flagged():
    assert "pii_in_reply" not in checks("We have your number 9876543210 noted.", question="my number is 9876543210")


def test_own_student_id_is_allowed():
    assert "other_student_id" not in checks("Your id stu_asha is registered. INR 60,000 [fees#1]", student_id="stu_asha")


def test_uncited_and_entity_mismatch():
    assert "uncited_answer" in checks("The fee is INR 60,000.", citations=[])
    assert "entity_mismatch" in checks("The fee is INR 60,000 [fees#1].", question="What is the fee for the Java Full Stack course?")
    assert "entity_mismatch" in checks("Trainer is X [fees#1].", question="Who is the trainer for PYF-101?")


def test_abstentions_skip_the_knowledge_checks():
    assert checks("I don't have that information.", question="Fee for Java?", abstained=True, citations=[]) == set()


def test_question_entities():
    assert question_entities("What courses are available?") == []
    assert question_entities("Do you have a centre in Hyderabad?") == ["Hyderabad"]
    assert question_entities("Tell me about QSpiders in Pune.") == ["Pune"]


def test_deterministic_reviser_removes_only_the_bad_sentences():
    issues = [QaIssue("unsupported_number", "99000")]
    out = DeterministicReviser().revise(reply="The fee is INR 60,000. Also INR 99,000 extra. Pay in two instalments.", issues=issues, sources=SRC)
    assert out == "The fee is INR 60,000. Pay in two instalments."
    assert DeterministicReviser().revise(reply="x", issues=[QaIssue("internal_leak")], sources=[]) == ""
    assert DeterministicReviser().revise(reply="x", issues=[QaIssue("entity_mismatch", "Java")], sources=[]) == ""


class _Knowledge:
    """A scripted knowledge branch, so the QA loop can be driven with any reply."""

    def __init__(self, reply, sources=SRC, citations=("fees#1",), abstained=False):
        self.out = {"result": {"answer": reply, "abstained": abstained, "citations": list(citations), "chunks": sources}, "queries": ["q"]}

    def invoke(self, *_a, **_k):
        return self.out


def run(reply, question="What is the fee for Python Full Stack?", student="anonymous", **kw):
    g = build_support_graph(knowledge=_Knowledge(reply, **kw), settings=settings())
    return ask_support(g, question, tenant_id="qspiders_sample", student_id=student, ticket_id="qa-1", today="2026-10-24")


def test_a_clean_reply_goes_through_untouched(ingested):
    out = run("The Python Full Stack fee is INR 60,000 [fees#1].")
    assert out["qa_issues"] == [] and out["qa_revisions"] == 0 and not out["qa_blocked"] and "60,000" in out["reply"]


def test_a_fixable_problem_is_revised_once_then_passes(ingested):
    out = run("The Python Full Stack fee is INR 60,000 [fees#1]. We will also give INR 99,000 cashback.")
    assert out["qa_revisions"] == 1 and out["qa_issues"] == [] and "99,000" not in out["reply"] and "60,000" in out["reply"]


def test_an_unfixable_problem_is_replaced_by_the_fallback(ingested):
    out = run("The fee for Java is INR 60,000 [fees#1].", question="What is the fee for the Java Full Stack course?")
    assert out["qa_blocked"] and out["abstained"] and "reliable answer" in out["reply"] and "60,000" not in out["reply"]
    assert not rows(Escalation)  # not a security failure: no escalation


def test_internal_text_in_a_reply_is_blocked_and_escalated(ingested):
    from app.graph.nodes.qa import _markers

    marker = _markers("qspiders_sample")[0]
    out = run(f"Staff note: {marker} [fees#1]", student="stu_asha")
    assert out["qa_blocked"] and marker not in out["reply"].lower() and out["needs_human"] and out["escalation_reason"] == "qa_security"
    (esc,) = rows(Escalation)
    assert esc.reason == "qa_security" and marker not in str(esc.brief).lower()


def test_revision_cap_is_respected(ingested):
    g = build_support_graph(knowledge=_Knowledge("Fee INR 60,000 [fees#1]. Extra INR 99,000."), settings=settings(qa_max_revisions=0))
    out = ask_support(g, "What is the fee for Python Full Stack?", tenant_id="qspiders_sample", ticket_id="qa-2")
    assert out["qa_blocked"] and out["qa_revisions"] == 0


def test_a_reviser_that_returns_nothing_ends_in_the_fallback(ingested):
    class Empty:
        def revise(self, **_):
            return ""

    g = build_support_graph(knowledge=_Knowledge("Fee INR 60,000 [fees#1]. Extra INR 99,000."), reviser=Empty(), settings=settings())
    out = ask_support(g, "What is the fee for Python Full Stack?", tenant_id="qspiders_sample", ticket_id="qa-3")
    assert out["qa_blocked"] and out["qa_revisions"] == 1


class _Structured:
    def __init__(self, out=None, err=None):
        self.out, self.err = out, err

    def with_structured_output(self, _):
        return self

    def invoke(self, _):
        if self.err:
            raise self.err
        return self.out


def test_llm_critic_blocks_ungrounded_replies_and_survives_failures():
    review = lambda m: LlmCritic(m).review(question="q", reply="r", sources=SRC)  # noqa: E731
    assert review(_Structured({"grounded": True})) == []
    assert [i.code for i in review(_Structured({"grounded": False, "problems": ["wrong course"]}))] == ["critic_ungrounded"]
    assert review(_Structured(err=RuntimeError("503"))) == []          # outage: deterministic verdict stands
    assert review(_Structured({"nonsense": 1})) == []


def test_critic_verdict_blocks_through_the_graph_and_llm_reviser_is_used(ingested):
    class Critic:
        def review(self, **_):
            return [QaIssue("critic_ungrounded", "x")]

    g = build_support_graph(knowledge=_Knowledge("Fee INR 60,000 [fees#1]."), critic=Critic(), reviser=LlmReviser(StubChatModel(reply="")), settings=settings())
    out = ask_support(g, "What is the fee for Python Full Stack?", tenant_id="qspiders_sample", ticket_id="qa-4")
    assert out["qa_blocked"]


def test_settings_bounds():
    with pytest.raises(ValueError):
        Settings(_env_file=None, qa_max_revisions=9)
