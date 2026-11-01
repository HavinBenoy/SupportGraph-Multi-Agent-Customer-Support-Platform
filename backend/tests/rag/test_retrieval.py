"""Retrieval (app/rag/retrieval.py): ranking, and the three filters every query carries (tenant, audience, model).
These run on the SQLite path; tests/db/test_postgres_rag.py proves the pgvector path returns the same results."""

import pytest
from sqlalchemy import select, text

from app.db.models import EMBEDDING_DIM, Chunk
from app.db.session import session_scope
from app.rag.ingest import ingest_tenant
from app.rag.retrieval import _cosine, count_chunks, retrieve, search
from tests.rag.conftest import doc_text, make_tenant


def find(embedder, question, tenant="qspiders_sample", top_k=4, model=None):
    with session_scope() as db:
        return search(db, tenant_id=tenant, query_vector=embedder.embed_query(question),
                      model_name=model or embedder.name, top_k=top_k)


def test_the_right_article_ranks_first_for_clear_questions(embedder, ingested):
    assert find(embedder, "Who can see my attendance records and scores?")[0].doc_id == "privacy-and-student-data"
    assert find(embedder, "What payment methods do you accept?")[0].doc_id == "fees-and-payments"
    assert find(embedder, "Do you provide mock interviews and resume support?")[0].doc_id == "placement-and-career-support"


def test_results_are_best_first_with_scores_and_respect_top_k(embedder, ingested):
    hits = find(embedder, "return window for refunds", top_k=3)
    assert len(hits) == 3 and [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert all(h.source_id == f"{h.doc_id}#{h.chunk_index}" and h.heading.startswith(h.doc_title) for h in hits)


def test_retrieve_drops_everything_below_the_minimum_score(embedder, ingested):
    q = embedder.embed_query("What is the weather in Mumbai today?")
    with session_scope() as db:
        raw = search(db, tenant_id="qspiders_sample", query_vector=q, model_name=embedder.name, top_k=4)
        kept = retrieve(db, tenant_id="qspiders_sample", query_vector=q, model_name=embedder.name, top_k=4, min_score=0.2)
    assert raw and max(h.score for h in raw) < 0.2 and kept == []


def test_another_tenants_passages_are_never_returned(embedder, ingested, tmp_path):
    root = make_tenant(tmp_path, "other_institute", {"x.md": doc_text("secret-faq", "Other Store FAQ",
                       "## Returns\nOther store returns: ZEBRA-SECRET-7 days, free return shipping on everything.")})
    with session_scope() as db:
        ingest_tenant(db, "other_institute", embedder, tenants_dir=root)
    question = "Other store returns ZEBRA-SECRET-7 days free return shipping"
    sample = find(embedder, question, "qspiders_sample", top_k=20)
    other = find(embedder, question, "other_institute", top_k=20)
    assert sample and not any("ZEBRA-SECRET" in h.text or h.doc_id == "secret-faq" for h in sample)
    assert [h.doc_id for h in other] == ["secret-faq"]  # and the other tenant sees only its own


def test_vectors_from_a_different_embedding_model_are_never_compared(embedder, ingested):
    assert find(embedder, "return policy", model="some-other-model") == []
    with session_scope() as db:
        assert count_chunks(db, tenant_id="qspiders_sample", model_name=embedder.name) > 0
        assert count_chunks(db, tenant_id="qspiders_sample", model_name="some-other-model") == 0


def test_a_staff_only_passage_is_filtered_by_the_query_even_if_it_got_into_the_table(embedder, ingested):
    """Layer 3. The CHECK constraint (layer 2) normally prevents this row; switch it off to prove the query alone holds."""
    secret = "Refund approval thresholds: over $500 requires a branch manager"
    with session_scope() as db:
        db.execute(text("PRAGMA ignore_check_constraints = ON"))
        db.add(Chunk(tenant_id="qspiders_sample", doc_id="fees-and-payments", chunk_index=99, heading="INTERNAL", text=secret,
                     token_count=9, audience="internal", embedding_model=embedder.name,
                     embedding=embedder.embed_documents([f"INTERNAL\n{secret}"])[0]))
        db.flush()
        assert db.scalar(select(Chunk.audience).where(Chunk.chunk_index == 99)) == "internal"  # it really is in the table
        db.execute(text("PRAGMA ignore_check_constraints = OFF"))
    hits = find(embedder, "Refund approval thresholds over $500 requires a branch manager", top_k=50)
    assert hits and not any("branch manager" in h.text for h in hits)


def test_an_empty_knowledge_base_returns_nothing_rather_than_failing(embedder):
    assert find(embedder, "anything at all") == []


@pytest.mark.parametrize("tenant", ["", None])
def test_a_missing_tenant_is_refused_instead_of_searching_everything(embedder, ingested, tenant):
    with pytest.raises(ValueError, match="tenant_id is required"), session_scope() as db:
        search(db, tenant_id=tenant, query_vector=embedder.embed_query("x"), model_name=embedder.name, top_k=4)


@pytest.mark.parametrize("vector, top_k, message", [
    ([0.1] * 10, 4, "dimensions"), ([0.1] * EMBEDDING_DIM, 0, "top_k"), ([0.1] * EMBEDDING_DIM, 51, "top_k")])
def test_bad_arguments_are_refused(embedder, ingested, vector, top_k, message):
    with pytest.raises(ValueError, match=message), session_scope() as db:
        search(db, tenant_id="qspiders_sample", query_vector=vector, model_name=embedder.name, top_k=top_k)


def test_cosine_helper_edge_cases():
    assert _cosine([1, 0], [1, 0]) == pytest.approx(1.0) and _cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert _cosine([1, 0], [-1, 0]) == pytest.approx(-1.0) and _cosine([0, 0], [1, 1]) == 0.0
    assert _cosine([3, 4], [6, 8]) == pytest.approx(1.0)  # scale does not matter


def test_ties_are_broken_deterministically(embedder, ingested):
    first = [h.source_id for h in find(embedder, "shipping", top_k=10)]
    assert first == [h.source_id for h in find(embedder, "shipping", top_k=10)]
