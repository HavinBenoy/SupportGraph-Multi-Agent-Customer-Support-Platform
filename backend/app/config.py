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
    llm_model: str = "qwen3:1.7b"  # M1 decision (docs/decisions/0006): safest measured model; qwen3:4b-instruct-2507-q4_K_M is an opt-in candidate
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = ""
    # --- Model gateway hardening (milestone 0) ---
    # Per-role model override, e.g. SG_LLM_MODEL_TRIAGE=qwen3:1.7b. Roles: action, faq, triage, rewrite, proposer, critic,
    # reviser, judge. Empty = use SG_LLM_MODEL. A role override never changes the provider or endpoint.
    llm_model_action: str = ""
    llm_model_faq: str = ""
    llm_model_triage: str = ""  # empty = use llm_model. (The 4B instruct escalated 12-15% of ordinary tickets, so triage should stay on the 1.7B.)
    llm_model_rewrite: str = ""
    llm_model_proposer: str = ""
    llm_model_critic: str = ""
    llm_model_reviser: str = ""
    llm_model_judge: str = ""
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)   # one call; CPU models are slow, so generous
    llm_max_retries: int = Field(default=1, ge=0, le=3)              # transient network errors only (openai_compat)
    llm_max_output_tokens: int = Field(default=400, ge=16, le=4096)  # per call cap (num_predict / max_tokens)
    llm_num_ctx: int = Field(default=4096, ge=512, le=32768)         # Ollama context window; bigger = slower + more RAM
    llm_keep_alive: str = "30m"                                      # Ollama: keep the model loaded between calls
    llm_thinking: bool = False                                       # qwen3 "thinking" off: it multiplies latency on CPU
    # Sampling. Defaults keep the current behaviour (temperature 0, provider defaults for the rest). Qwen3's authors advise AGAINST
    # greedy decoding (it can loop until the token cap): for the experiment see docs/MODEL_SELECTION.md. Unset (None) = not sent.
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_top_p: float | None = Field(default=None, gt=0, le=1)
    llm_top_k: int | None = Field(default=None, ge=1)                # Ollama only
    llm_repeat_penalty: float | None = Field(default=None, ge=0.5, le=2)  # Ollama only
    llm_seed: int | None = None
    # Process-wide daily cap on tokens spent through guarded calls. 0 = unlimited (the right value for local models).
    llm_daily_token_budget: int = Field(default=0, ge=0)
    # Hard gate: a chat/embedding endpoint that is not on this machine is refused unless this is true. This is what stops
    # a typo or a copied .env from silently sending student text to a third party.
    allow_external_llm: bool = False
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
    # "auto": nomic-embed-text gets the prefixes its authors specify ("search_query: " / "search_document: ") unless you set
    # the two prefixes yourself; any other model gets none. "none": never add prefixes. Real providers only (the fake embedder is unaffected).
    embedding_prefix_mode: Literal["auto", "none"] = "auto"
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""
    rag_top_k: int = Field(default=4, ge=1, le=20)
    # Chunks scoring below this cosine similarity are not shown to the model, and if none remain the bot abstains.
    # Similarity scales differ per embedding model: calibrate with `make eval-rag` when you change the model.
    rag_min_score: float = Field(default=0.2, ge=0.0, le=1.0)

    # --- Triage, routing and corrective RAG (build step 3) ---
    # Below this triage confidence the router asks a clarifying question instead of acting.
    triage_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    # Category `other` with confidence from this value up to triage_min_confidence goes to the grounded FAQ (answer with a citation or
    # abstain) instead of a clarifying question. Below it, and for parse failures (0.0), the router still asks. Escalation always wins.
    triage_other_floor: float = Field(default=0.4, ge=0.0, le=1.0)
    # Knowledge agent: first search + at most (attempts - 1) corrective retries. The cap prevents runaway loops and cost.
    knowledge_max_attempts: int = Field(default=2, ge=1, le=3)

    # --- Actions, approvals, checkpointing, QA (build step 4) ---
    # Where paused graph runs (waiting for an approval) are stored: "memory" is lost on restart (fine for demos/tests);
    # "postgres" survives restarts and uses the same database as everything else.
    checkpointer: Literal["memory", "postgres"] = "memory"
    # QA loop: how many times a failing reply may be revised before it is replaced by a safe fallback.
    qa_max_revisions: int = Field(default=1, ge=0, le=3)
    # M1b: roles outside the agreed final model configuration stay deterministic / off until they are measured with the real model.
    qa_llm_critic: bool = False                      # the LLM groundedness critic on every answered reply (adds a model call; can block good answers)
    rewriter_mode: Literal["glossary", "llm"] = "glossary"  # search-query rewriting: tenant synonym glossary (no model call) or the chat model
    # Startup checks for a local Ollama: off | warn (log and continue) | strict (refuse to start). Warm-up loads the models in the background.
    llm_preflight: Literal["off", "warn", "strict"] = "warn"
    llm_warmup: bool = True

    # --- Evaluation (build step 6) ---
    # Price per 1M tokens, only used to turn measured token counts into "cost per ticket". 0 = local/free model (cost reported as 0).
    eval_price_per_m_input: float = Field(default=0.0, ge=0.0)
    eval_price_per_m_output: float = Field(default=0.0, ge=0.0)

    # --- API and sign-in (build step 5) ---
    # Signs login tokens and anonymous ticket ids. REQUIRED to start the API (>= 32 chars). Generate one with `make auth-init`;
    # never commit it. Rotating it signs everyone out.
    auth_secret: SecretStr | None = None
    token_ttl_minutes: int = Field(default=480, ge=5, le=60 * 24 * 7)
    login_max_failures: int = Field(default=5, ge=1, le=50)       # per (tenant, username, client) inside the window
    login_lockout_seconds: int = Field(default=300, ge=10, le=86400)
    chat_max_messages_per_minute: int = Field(default=30, ge=1, le=600)  # per client address or signed-in student
    # Comma-separated browser origins allowed to call the API directly. Empty = none (the frontend proxies /api, so it needs none).
    cors_origins: str = ""

    # --- Tracing (Langfuse). Off unless SG_TRACING_ENABLED=true AND both keys are set. ---
    # The keys use Langfuse's standard variable names, so the same .env also works for its tools.
    tracing_enabled: bool = False
    langfuse_public_key: str | None = Field(default=None, validation_alias=AliasChoices("LANGFUSE_PUBLIC_KEY", "langfuse_public_key"))
    langfuse_secret_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("LANGFUSE_SECRET_KEY", "langfuse_secret_key"))
    langfuse_base_url: str = Field(default="http://localhost:3100", validation_alias=AliasChoices("LANGFUSE_BASE_URL", "langfuse_base_url"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
