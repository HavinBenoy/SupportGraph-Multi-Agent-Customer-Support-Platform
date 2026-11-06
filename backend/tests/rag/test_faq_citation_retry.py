"""M1b batch 2: an UNCITED answer is regenerated once; the retry faces the same validator; every other failure is never retried."""

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.rag.faq import ABSTAIN_MESSAGE, CITATION_REMINDER, answer_question, build_faq_graph
from tests.rag.conftest import CountingExtractive, settings

Q = "What is the refund policy for fees?"
T = "qspiders_sample"


class Seq(BaseChatModel):
    """Replies from a script, one per call: each item is text or (text, response_metadata). Records every call."""

    script: list = Field(default_factory=list)
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "seq"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        item = self.script[min(len(self.calls), len(self.script)) - 1]
        text, meta = item if isinstance(item, tuple) else (item, {})
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text, response_metadata=meta))])


@pytest.fixture
def cite(embedder, ingested):
    return answer_question(build_faq_graph(embedder, CountingExtractive(), settings()), Q, tenant_id=T).citations[0]


def run(embedder, model, **kw):
    return answer_question(build_faq_graph(embedder, model, settings(**kw)), Q, tenant_id=T)


def test_uncited_then_cited_is_answered_after_one_retry(embedder, cite):
    m = Seq(script=["Refunds are possible within 30 days.", f"Refunds are possible within 30 days [{cite}]."])
    r = run(embedder, m)
    assert not r.abstained and r.reason == "answered" and r.citations == [cite] and len(m.calls) == 2
    assert r.answer == f"Refunds are possible within 30 days [{cite}]."  # exactly the model's words: code adds nothing


def test_retry_prompt_is_the_original_plus_one_reminder_with_a_real_example_id(embedder, cite):
    m = Seq(script=["no citation", f"ok [{cite}]"])
    run(embedder, m)
    first, second = m.calls
    assert second[:len(first)] == first and len(second) == len(first) + 1
    example = [c for c in __import__("re").findall(r"<source id=\"([^\"]+)\"", first[1].content)][0]
    assert second[-1].content == CITATION_REMINDER.format(example=example, sentinel="INSUFFICIENT_CONTEXT")


def test_uncited_twice_abstains_and_no_third_call(embedder, cite):
    m = Seq(script=["no citation", "still no citation"])
    r = run(embedder, m)
    assert r.abstained and r.reason == "uncited_answer" and r.answer == ABSTAIN_MESSAGE and r.citations == [] and len(m.calls) == 2


@pytest.mark.parametrize("second,reason", [
    ("Refunds are free [made-up#0].", "unknown_citation"),
    ("INSUFFICIENT_CONTEXT", "model_abstained"),
    ("The sources do not mention refunds.", "model_declined"),
    ("<think>hmm [x#0] and then", "unfinished_reasoning"),
])
def test_retry_output_is_validated_like_any_other(embedder, cite, second, reason):
    r = run(embedder, Seq(script=["no citation", second]))
    assert r.abstained and r.reason == reason and r.answer == ABSTAIN_MESSAGE and r.citations == []


def test_a_truncated_retry_is_rejected_even_with_a_valid_citation(embedder, cite):
    r = run(embedder, Seq(script=["no citation", (f"Refunds within 30 days [{cite}] and then the", {"done_reason": "length"})]))
    assert r.abstained and r.reason == "truncated_answer"


def test_reasoning_in_the_retry_is_stripped_before_validation(embedder, cite):
    r = run(embedder, Seq(script=["no citation", f"<think>draft [made-up#9]</think>Refunds within 30 days [{cite}]."]))
    assert not r.abstained and r.citations == [cite] and "think" not in r.answer


@pytest.mark.parametrize("first,meta,reason", [
    ("Refunds are free [made-up#0].", {}, "unknown_citation"),
    ("INSUFFICIENT_CONTEXT", {}, "model_abstained"),
    ("The sources do not mention refunds.", {}, "model_declined"),
    ("<think>hmm and then", {}, "unfinished_reasoning"),
    ("no citation and then the", {"done_reason": "length"}, "truncated_answer"),
])
def test_only_uncited_answers_are_ever_retried(embedder, cite, first, meta, reason):
    m = Seq(script=[(first, meta), f"fine [{cite}]"])
    r = run(embedder, m)
    assert r.reason == reason and r.abstained and len(m.calls) == 1


def test_a_good_first_answer_costs_one_call(embedder, cite):
    m = Seq(script=[f"Refunds within 30 days [{cite}]."])
    r = run(embedder, m)
    assert not r.abstained and len(m.calls) == 1


def test_retry_can_be_switched_off(embedder, cite):
    m = Seq(script=["no citation", f"ok [{cite}]"])
    r = run(embedder, m, faq_citation_retries=0)
    assert r.reason == "uncited_answer" and len(m.calls) == 1


def test_nothing_retrieved_never_calls_the_model(embedder, ingested):
    m = Seq(script=["x"])
    r = answer_question(build_faq_graph(embedder, m, settings(rag_min_score=0.99)), Q, tenant_id=T)
    assert r.abstained and m.calls == []
