"""The one embedding interface the rest of the app uses. Built by app.llm.gateway (the only module that knows
which provider is configured)."""

from dataclasses import dataclass

from langchain_core.embeddings import Embeddings


class EmbeddingDimensionError(ValueError):
    """The model produced vectors of a width the database column cannot hold."""


@dataclass
class EmbeddingClient:
    """Wraps a LangChain `Embeddings` with the three things retrieval needs to stay correct:
    a stable `name` (stored with every vector, so vectors from different models are never compared),
    the expected `dim`, and the model's query/document prefixes."""

    inner: Embeddings
    name: str
    dim: int
    query_prefix: str = ""
    document_prefix: str = ""

    def _check(self, vectors: list[list[float]]) -> list[list[float]]:
        for v in vectors:
            if len(v) != self.dim:
                raise EmbeddingDimensionError(
                    f"Embedding model '{self.name}' returned {len(v)}-dimensional vectors but the database stores "
                    f"{self.dim}. Pick a {self.dim}-dimension model/setting, or add a migration and re-ingest."
                )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._check(self.inner.embed_documents([self.document_prefix + t for t in texts]))

    def embed_query(self, text: str) -> list[float]:
        return self._check([self.inner.embed_query(self.query_prefix + text)])[0]
