"""The embedding interface, the offline fake embedder, and the text normaliser they share."""

import math
import subprocess
import sys
from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings

from app.db.models import EMBEDDING_DIM
from app.llm.embeddings import EmbeddingClient, EmbeddingDimensionError
from app.llm.fake_embeddings import HashingEmbeddings
from app.llm.gateway import get_embedding_client
from app.llm.lexical import stem, tokens
from tests.rag.conftest import settings

BACKEND = Path(__file__).resolve().parents[2]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))  # vectors are unit length


def test_fake_embedder_is_unit_length_with_the_database_width():
    v = HashingEmbeddings(EMBEDDING_DIM).embed_query("How long do I have to return an item?")
    assert len(v) == EMBEDDING_DIM and math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)


def test_fake_embedder_is_deterministic_across_processes():
    """It must not use Python's per-process randomised hash(): ingestion and querying run in different processes."""
    code = ("from app.llm.fake_embeddings import HashingEmbeddings as H;"
            "v=H(768).embed_query('refund window');print(repr(v[:5]))")
    outs = {subprocess.run([sys.executable, "-I", "-c", code], cwd=BACKEND, capture_output=True, text=True,
                           env={"PYTHONPATH": str(BACKEND)}).stdout for _ in range(2)}
    assert len(outs) == 1 and next(iter(outs)).startswith("[")


def test_fake_embedder_ranks_related_text_above_unrelated_text():
    e = HashingEmbeddings(EMBEDDING_DIM)
    q = e.embed_query("Can I return an opened item for a refund?")
    related = e.embed_documents(["Returns and Refunds\nYou can return most items within 30 days for a refund."])[0]
    unrelated = e.embed_documents(["Warranty and Repairs\nElectronics carry a 12-month limited warranty."])[0]
    assert cos(q, related) > cos(q, unrelated)


def test_fake_embedder_handles_empty_text_without_dividing_by_zero():
    assert HashingEmbeddings(EMBEDDING_DIM).embed_query("") == [0.0] * EMBEDDING_DIM


class Recorder(Embeddings):
    def __init__(self, dim):
        self.dim, self.seen = dim, []

    def embed_documents(self, texts):
        self.seen += texts
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]

    def embed_query(self, text):
        self.seen.append(text)
        return [1.0] + [0.0] * (self.dim - 1)


def test_client_applies_model_specific_prefixes_to_queries_and_documents():
    rec = Recorder(EMBEDDING_DIM)
    c = EmbeddingClient(inner=rec, name="m", dim=EMBEDDING_DIM, query_prefix="search_query: ", document_prefix="search_document: ")
    c.embed_query("q")
    c.embed_documents(["d1", "d2"])
    assert rec.seen == ["search_query: q", "search_document: d1", "search_document: d2"]


def test_client_refuses_vectors_of_the_wrong_width_with_a_fix_in_the_message():
    c = EmbeddingClient(inner=Recorder(384), name="small-model", dim=EMBEDDING_DIM)
    with pytest.raises(EmbeddingDimensionError, match="384-dimensional.*768"):
        c.embed_query("x")
    with pytest.raises(EmbeddingDimensionError):
        c.embed_documents(["x"])


def test_client_with_no_documents_makes_no_call():
    rec = Recorder(EMBEDDING_DIM)
    assert EmbeddingClient(inner=rec, name="m", dim=EMBEDDING_DIM).embed_documents([]) == [] and rec.seen == []


def test_gateway_builds_the_fake_client_with_a_stable_name():
    c = get_embedding_client(settings())
    assert c.name == f"fake:hashing-v1-{EMBEDDING_DIM}" and c.dim == EMBEDDING_DIM


def test_gateway_gives_actionable_errors_for_missing_optional_packages(monkeypatch):
    import builtins

    real = builtins.__import__

    def deny(name, *a, **k):
        if name.startswith(("langchain_ollama", "langchain_openai")):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    from app.config import Settings

    with pytest.raises(RuntimeError, match=r"backend\[ollama\]"):
        get_embedding_client(Settings(_env_file=None, embedding_provider="ollama"))
    with pytest.raises(RuntimeError, match=r"backend\[openai\]"):
        get_embedding_client(Settings(_env_file=None, embedding_provider="openai_compat"))


@pytest.mark.parametrize("a, b", [("shipping", "shipped"), ("shipping", "ships"), ("returns", "returned"),
                                  ("cancellation", "cancelled"), ("delivery", "deliver")])
def test_inflections_normalise_to_the_same_token(a, b):
    assert stem(a) == stem(b)


def test_tokens_drop_stopwords_and_markdown_and_keep_numbers_and_prices():
    assert tokens("Do you ship to **Canada**? It costs $14.99 and takes 1-2 days") == \
        ["ship", "canada", "cost", "$14.99", "take", "1", "2", "day"]
