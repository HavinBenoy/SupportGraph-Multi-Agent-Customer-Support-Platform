"""M1 blocking prerequisite: a reply cut off at the output-token limit must never reach a person, even with a valid citation."""

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.llm.truncation import is_truncated
from app.qa.critic import LlmReviser
from app.rag.faq import ABSTAIN_MESSAGE, answer_question, build_faq_graph
from tests.rag.conftest import CountingExtractive, settings

Q = "What is the refund policy for fees?"


class MetaModel(BaseChatModel):
    """Returns a fixed reply with chosen provider metadata (what ChatOllama / ChatOpenAI attach)."""

    reply: str = ""
    meta: dict = Field(default_factory=dict)
    usage: dict | None = None

    @property
    def _llm_type(self) -> str:
        return "meta-stub"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = AIMessage(content=self.reply, response_metadata=self.meta, usage_metadata=self.usage)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def ask(embedder, model, **kw):
    return answer_question(build_faq_graph(embedder, model, settings(**kw)), Q, tenant_id="qspiders_sample")


@pytest.fixture
def cite(embedder, ingested):
    return answer_question(build_faq_graph(embedder, CountingExtractive(), settings()), Q, tenant_id="qspiders_sample").citations[0]


@pytest.mark.parametrize("meta", [{"done_reason": "length"}, {"finish_reason": "length"}, {"stop_reason": "max_tokens"}, {"done_reason": "LENGTH"}])
def test_truncated_reply_with_a_valid_citation_is_rejected(embedder, cite, meta):
    r = ask(embedder, MetaModel(reply=f"Refunds are available within 30 days [{cite}] and after that the", meta=meta))
    assert r.abstained and r.reason == "truncated_answer" and r.answer == ABSTAIN_MESSAGE and r.citations == []


def test_truncation_wins_over_every_other_validation_outcome(embedder, cite):
    for reply in ("INSUFFICIENT_CONTEXT", "no citation here", "bad [made-up#0]", ""):
        r = ask(embedder, MetaModel(reply=reply, meta={"done_reason": "length"}))
        assert r.abstained and r.reason == "truncated_answer"


def test_a_complete_reply_with_stop_is_still_accepted(embedder, cite):
    r = ask(embedder, MetaModel(reply=f"Within 30 days [{cite}].", meta={"done_reason": "stop"}))
    assert not r.abstained and r.reason == "answered" and r.citations == [cite]


def test_no_finish_reason_but_whole_allowance_used_is_treated_as_truncated(embedder, cite):
    usage = {"input_tokens": 100, "output_tokens": 400, "total_tokens": 500}
    r = ask(embedder, MetaModel(reply=f"Within 30 days [{cite}] and", usage=usage), llm_max_output_tokens=400)
    assert r.abstained and r.reason == "truncated_answer"


def test_explicit_stop_is_trusted_even_if_it_used_exactly_the_allowance(embedder, cite):
    usage = {"input_tokens": 100, "output_tokens": 400, "total_tokens": 500}
    r = ask(embedder, MetaModel(reply=f"Within 30 days [{cite}].", meta={"done_reason": "stop"}, usage=usage), llm_max_output_tokens=400)
    assert not r.abstained


def test_models_without_metadata_are_unaffected(embedder, cite):
    assert not ask(embedder, MetaModel(reply=f"Within 30 days [{cite}].")).abstained


def test_is_truncated_unit():
    assert is_truncated(AIMessage("x", response_metadata={"done_reason": "length"}))
    assert not is_truncated(AIMessage("x", response_metadata={"done_reason": "stop"}))
    assert not is_truncated(AIMessage("x"), 400)  # nothing known: not flagged
    assert is_truncated(AIMessage("x", response_metadata={"eval_count": 400}), 400)


def test_qa_reviser_discards_a_truncated_revision():
    assert LlmReviser(MetaModel(reply="A half written", meta={"done_reason": "length"})).revise(reply="r", issues=[], sources=[]) == ""
    assert LlmReviser(MetaModel(reply="A full reply.", meta={"done_reason": "stop"})).revise(reply="r", issues=[], sources=[]) == "A full reply."


def test_real_ollama_client_against_mock_non_terminating_model_is_rejected(embedder, ingested):
    """Through the real langchain-ollama client and the gateway: the mock never stops, so Ollama reports done_reason=length."""
    from app.llm.gateway import get_chat_model
    from tests.bench.mock_ollama import MockOllama

    with MockOllama() as mock:
        from app.config import Settings

        s = Settings(_env_file=None, embedding_provider="fake", llm_provider="ollama", llm_base_url=mock.url, llm_max_output_tokens=30)
        r = answer_question(build_faq_graph(embedder, get_chat_model("faq", s), s), Q + " ramble", tenant_id="qspiders_sample")
    assert r.abstained and r.reason == "truncated_answer"


# ---------- refusals in the model's own words must take the abstention path ----------

from app.rag.faq import declares_no_information  # noqa: E402


@pytest.mark.parametrize("text", [
    "The sources do not mention anything about education loans or financial assistance options. [about-the-institute#5]",
    "The context does not contain this information.",
    "This is not mentioned in the sources.",
    "No information is available about hostel fees.",
    "I cannot find that in the documents.",
    "I don't have information about that.",
])
def test_own_words_refusals_are_detected(text):
    assert declares_no_information(text)


@pytest.mark.parametrize("text", [
    "The Python Full Stack course is offered in both online and offline modes [courses-and-batches#1].",
    "You can pay in two instalments: 60% at registration and 40% before week 8 [fees-and-payments#2].",
    "Submitting the enquiry form does not reserve a seat [admissions-and-demo-classes#5].",
    "No, it is not ok to type your card number here to pay [privacy-and-student-data#4].",
    "Weekend classes are available, but the sources list Saturday and Sunday batches [courses-and-batches#5].",
])
def test_ordinary_answers_including_negative_ones_are_not_flagged(text):
    assert not declares_no_information(text)


def test_validate_turns_a_cited_own_words_refusal_into_the_standard_abstention(embedder, cite):
    text = f"The sources do not mention anything about education loans or financial assistance options. [{cite}]"
    r = ask(embedder, MetaModel(reply=text, meta={"done_reason": "stop"}))
    assert r.abstained and r.reason == "model_declined" and r.answer == ABSTAIN_MESSAGE and r.citations == []
