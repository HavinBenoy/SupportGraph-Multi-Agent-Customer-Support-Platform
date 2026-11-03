"""Application settings, read from environment variables (prefix SG_) or the repo-root .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SG_", env_file=_REPO_ROOT_ENV, extra="ignore", populate_by_name=True)

    llm_provider: Literal["fake", "ollama", "openai_compat"] = "fake"
    llm_model: str = "qwen2.5:7b"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = ""
    default_tenant: str = "qspiders_sample"
    # Matches the defaults in docker-compose.yml. SQLite URLs also work (used by the fast tests).
    database_url: str = "postgresql+psycopg://supportgraph:supportgraph@localhost:5432/supportgraph"

    # --- Embeddings + retrieval (build step 2) ---
    # "fake" is a deterministic offline embedder (feature hashing): good for tests and the no-LLM demo, NOT for
    # judging real retrieval quality. Vectors must be 768-wide (see app/db/models.py EMBEDDING_DIM).
    embedding_provider: Literal["fake", "ollama", "openai_compat"] = "fake"
    embedding_model: str = "nomic-embed-text"
    embedding_base_url: str = "http://localhost:11434"
    embedding_api_key: str = ""
    # Some embedding models want a task prefix (nomic-embed-text: "search_query: " / "search_document: ").
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""
    rag_top_k: int = Field(default=4, ge=1, le=20)
    # Chunks scoring below this cosine similarity are not shown to the model, and if none remain the bot abstains.
    # Similarity scales differ per embedding model: calibrate with `make eval-rag` when you change the model.
    rag_min_score: float = Field(default=0.2, ge=0.0, le=1.0)

    # --- Triage, routing and corrective RAG (build step 3) ---
    # Below this triage confidence the router asks a clarifying question instead of acting.
    triage_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    # Knowledge agent: first search + at most (attempts - 1) corrective retries. The cap prevents runaway loops and cost.
    knowledge_max_attempts: int = Field(default=2, ge=1, le=3)

    # --- Actions, approvals, checkpointing, QA (build step 4) ---
    # Where paused graph runs (waiting for an approval) are stored: "memory" is lost on restart (fine for demos/tests);
    # "postgres" survives restarts and uses the same database as everything else.
    checkpointer: Literal["memory", "postgres"] = "memory"
    # QA loop: how many times a failing reply may be revised before it is replaced by a safe fallback.
    qa_max_revisions: int = Field(default=1, ge=0, le=3)

    # --- Tracing (Langfuse). Off unless SG_TRACING_ENABLED=true AND both keys are set. ---
    # The keys use Langfuse's standard variable names, so the same .env also works for its tools.
    tracing_enabled: bool = False
    langfuse_public_key: str | None = Field(default=None, validation_alias=AliasChoices("LANGFUSE_PUBLIC_KEY", "langfuse_public_key"))
    langfuse_secret_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("LANGFUSE_SECRET_KEY", "langfuse_secret_key"))
    langfuse_base_url: str = Field(default="http://localhost:3100", validation_alias=AliasChoices("LANGFUSE_BASE_URL", "langfuse_base_url"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
