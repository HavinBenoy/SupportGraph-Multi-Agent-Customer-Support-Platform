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
    default_tenant: str = "acme_store"
    # Matches the defaults in docker-compose.yml. SQLite URLs also work (used by the fast tests).
    database_url: str = "postgresql+psycopg://supportgraph:supportgraph@localhost:5432/supportgraph"

    # --- Tracing (Langfuse). Off unless SG_TRACING_ENABLED=true AND both keys are set. ---
    # The keys use Langfuse's standard variable names, so the same .env also works for its tools.
    tracing_enabled: bool = False
    langfuse_public_key: str | None = Field(default=None, validation_alias=AliasChoices("LANGFUSE_PUBLIC_KEY", "langfuse_public_key"))
    langfuse_secret_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("LANGFUSE_SECRET_KEY", "langfuse_secret_key"))
    langfuse_base_url: str = Field(default="http://localhost:3100", validation_alias=AliasChoices("LANGFUSE_BASE_URL", "langfuse_base_url"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
