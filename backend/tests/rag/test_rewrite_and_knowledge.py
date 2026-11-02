"""Query rewriting and the corrective knowledge graph."""


from app.graph.subgraphs.knowledge import RETRYABLE_REASONS, build_knowledge_graph
from app.llm.fake_faq import ExtractiveFaqModel
from app.rag.faq import build_faq_graph
from app.rag.rewrite import MAX_QUERY_CHARS, GlossaryRewriter, LlmRewriter
from tests.rag.conftest import CountingExtractive, StubChatModel, settings

SYN = {"courses": ["programmes"], "marks": ["score"]}
CFG = {"configurable": {"tenant_id": "qspiders_sample", "student_id": "anonymous"}}


def test_glossary_expands_with_tenant_vocabulary_without_dropping_the_question():
    q = GlossaryRewriter().rewrite("What courses are available?", attempt=0, synonyms=SYN)
    assert q.startswith("What courses are available?") and q.endswith("programmes")


def test_glossary_retry_drops_filler_words():
    q = GlossaryRewriter().rewrite("What are the marks for me?", attempt=1, synonyms=SYN)
    assert "what" not in q.split() and "marks" in q and "score" in q


def test_glossary_with_no_synonyms_is_the_identity_on_attempt_zero():
    assert GlossaryRewriter().rewrite("hello  there", attempt=0, synonyms={}) == "hello there"


def test_glossary_output_is_bounded():
    assert len(GlossaryRewriter().rewrite("courses " * 500, attempt=0, synonyms=SYN)) <= MAX_QUERY_CHARS


def test_llm_rewriter_uses_first_line_and_falls_back_on_failure_or_empty():
    assert LlmRewriter(StubChatModel(reply="programmes list\nextra")).rewrite("courses?", attempt=0, synonyms=SYN) == "programmes list"
    assert LlmRewriter(StubChatModel(reply="")).rewrite("courses?", attempt=0, synonyms=SYN) == "courses?"

    class Broken(StubChatModel):
        def _generate(self, *a, **k):
            raise RuntimeError("boom")

    assert LlmRewriter(Broken()).rewrite("a  b", attempt=0, synonyms=SYN) == "a b"


def test_expansion_rescues_the_courses_question(embedder, ingested):
    k = build_knowledge_graph(build_faq_graph(embedder, ExtractiveFaqModel(), settings()), GlossaryRewriter(), settings())
    out = k.invoke({"question": "What courses are available?"}, CFG)
    assert not out["result"]["abstained"] and out["result"]["citations"]


class _Fixed:
    """A rewriter that returns scripted queries, to test the retry loop independently of any vocabulary."""

    def __init__(self, *queries):
        self.queries, self.calls = list(queries), []

    def rewrite(self, question, *, attempt, synonyms):
        self.calls.append(attempt)
        return self.queries[min(attempt, len(self.queries) - 1)]


def test_retry_is_capped_by_max_attempts(embedder, ingested):
    model = CountingExtractive()
    k = build_knowledge_graph(build_faq_graph(embedder, model, settings()), _Fixed("zzqx one", "zzqx two", "zzqx three"),
                              settings(knowledge_max_attempts=2))
    out = k.invoke({"question": "zzqx"}, CFG)
    assert out["result"]["abstained"] and out["attempt"] == 2 and len(out["queries"]) == 2
    assert model.calls == []  # nothing relevant retrieved: the model is never called


def test_identical_rewrite_stops_instead_of_repeating_the_search(embedder, ingested):
    r = _Fixed("zzqx")
    k = build_knowledge_graph(build_faq_graph(embedder, ExtractiveFaqModel(), settings()), r, settings(knowledge_max_attempts=3))
    out = k.invoke({"question": "zzqx"}, CFG)
    assert out["attempt"] == 1 and out["queries"] == ["zzqx", "zzqx"]


def test_non_retryable_abstention_is_not_retried(embedder, ingested):
    assert "question_too_long" not in RETRYABLE_REASONS and "empty_question" not in RETRYABLE_REASONS
    r = _Fixed("a", "b")
    k = build_knowledge_graph(build_faq_graph(embedder, ExtractiveFaqModel(), settings()), r, settings())
    out = k.invoke({"question": "  "}, CFG)
    assert out["result"]["abstained"] and out["attempt"] == 1


def test_search_query_changes_retrieval_but_the_model_is_asked_the_original_question(embedder, ingested):
    model = CountingExtractive()
    faq = build_faq_graph(embedder, model, settings())
    faq.invoke({"question": "Tell me everything", "search_query": "Can I pay the fee in instalments?"}, CFG)
    prompt = str(model.calls[0])
    assert "Tell me everything" in prompt


def test_missing_tenant_fails_closed(embedder, ingested):
    import pytest

    from app.tools.scope import ScopeError

    k = build_knowledge_graph(build_faq_graph(embedder, ExtractiveFaqModel(), settings()), GlossaryRewriter(), settings())
    with pytest.raises(ScopeError):
        k.invoke({"question": "fees"}, {"configurable": {}})
