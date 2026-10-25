"""Application settings, read from environment variables (prefix SG_) or the repo-root .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SG_", env_file=_REPO_ROOT_ENV, extra="ignore")

    llm_provider: Literal["fake", "ollama", "openai_compat"] = "fake"
    llm_model: str = "qwen2.5:7b"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = ""
    default_tenant: str = "acme_store"


@lru_cache
def get_settings() -> Settings:
    return Settings()
