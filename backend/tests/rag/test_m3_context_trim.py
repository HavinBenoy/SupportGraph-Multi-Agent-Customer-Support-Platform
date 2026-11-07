"""M3 (opt-in) prompt-size controls: fewer passages sent to the model. They may only ever move an answer towards abstention, never towards
an ungrounded answer, and they are off by default."""

from app.config import Settings
from app.rag.faq import trim_context
from app.rag.retrieval import RetrievedChunk


def chunks(*pairs):
    return [RetrievedChunk(doc_id="d", chunk_index=i, doc_title="t", heading="h", text="x" * n, score=s) for i, (s, n) in enumerate(pairs)]


def S(**kw):
    return Settings(_env_file=None, **kw)


def test_both_controls_are_off_by_default_and_change_nothing():
    c = chunks((0.9, 500), (0.5, 500), (0.3, 500), (0.25, 500))
    s = S()
    assert s.rag_context_band == 0 and s.rag_max_context_chars == 0 and trim_context(c, s) == c


def test_the_band_keeps_only_passages_close_to_the_best_one():
    c = chunks((0.90, 100), (0.85, 100), (0.60, 100), (0.30, 100))
    assert [x.chunk_index for x in trim_context(c, S(rag_context_band=0.1))] == [0, 1]
    assert [x.chunk_index for x in trim_context(c, S(rag_context_band=0.01))] == [0]            # the best passage is always kept


def test_the_character_cap_drops_the_lowest_ranked_passages_and_never_cuts_text():
    c = chunks((0.9, 600), (0.8, 600), (0.7, 600))
    out = trim_context(c, S(rag_max_context_chars=1300))
    assert [x.chunk_index for x in out] == [0, 1] and all(len(x.text) == 600 for x in out)
    assert [x.chunk_index for x in trim_context(c, S(rag_max_context_chars=10))] == [0]         # even an oversized best passage is kept, whole


def test_both_controls_combine_and_an_empty_list_stays_empty():
    c = chunks((0.9, 400), (0.88, 400), (0.87, 400))
    assert [x.chunk_index for x in trim_context(c, S(rag_context_band=0.05, rag_max_context_chars=850))] == [0, 1]
    assert trim_context([], S(rag_context_band=0.1)) == []


def test_a_trimmed_passage_can_no_longer_be_cited(monkeypatch):
    """The state the validator sees contains only the passages that were sent, so citing a dropped one fails validation (fail closed)."""
    from app.db.session import session_scope
    from app.graph.support import ask_support, build_support_graph
    from app.llm.gateway import get_embedding_client
    from app.rag.ingest import ingest_tenant

    s = S(rag_context_band=0.0001)                                                              # keep (almost) only the best passage
    with session_scope() as db:
        ingest_tenant(db, "qspiders_sample", get_embedding_client(s))
    out = ask_support(build_support_graph(settings=s), "Can I pay the fee in instalments?", tenant_id="qspiders_sample", ticket_id="trim1")
    assert out["perf"]["flags"]["context_chunks"] == 1
    assert out["abstained"] or all(c in {x["source_id"] for x in out["sources"]} for c in out["citations"])
