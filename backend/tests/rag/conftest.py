"""Shared fixtures for the RAG tests. All run on the fast in-memory SQLite store (tests/conftest.py seeds both
tenants) with the offline fake embedder and fake FAQ model: no Docker, no network, no LLM key."""

from pathlib import Path

import pytest
import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.config import Settings
from app.db.session import session_scope
from app.llm.fake_faq import ExtractiveFaqModel
from app.llm.gateway import get_embedding_client
from app.rag.faq import build_faq_graph
from app.rag.ingest import ingest_tenant


def settings(**kw) -> Settings:
    kw.setdefault("trial_purge_interval_min", 0)   # M5: the scheduled retention loop is off in tests (they call run_purge directly; the shared SQLite connection must not be written from a background task)
    return Settings(_env_file=None, embedding_provider="fake", llm_provider="fake", **kw)


def doc_text(doc_id: str, title: str, body: str, audience: str = "public") -> str:
    return f"---\ndoc_id: {doc_id}\ntitle: {title}\naudience: {audience}\nlast_reviewed: 2026-10-01\n---\n# {title}\n\n{body}\n"


def make_tenant(root: Path, tenant_id: str, docs: dict[str, str], *, name: str = "Test Institute", threshold: int = 75) -> Path:
    """Write tenants/<id>/config.yaml and docs/<file>.md under `root`; returns `root` (a tenants_dir)."""
    folder = root / tenant_id
    (folder / "docs").mkdir(parents=True, exist_ok=True)
    (folder / "config.yaml").write_text(yaml.safe_dump(
        {"tenant_id": tenant_id, "name": name, "timezone": "Asia/Kolkata", "attendance_threshold_pct": threshold}))
    for filename, text in docs.items():
        (folder / "docs" / filename).write_text(text, encoding="utf-8")
    return root


class StubChatModel(BaseChatModel):
    """Returns a fixed reply and records every call, so tests can assert the model was (not) used."""

    reply: str = ""
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "stub"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


class CountingExtractive(ExtractiveFaqModel):
    calls: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


@pytest.fixture
def embedder():
    return get_embedding_client(settings())


@pytest.fixture
def ingested(embedder):
    with session_scope() as db:
        return ingest_tenant(db, "qspiders_sample", embedder)


@pytest.fixture
def faq_graph(embedder, ingested):
    return build_faq_graph(embedder, ExtractiveFaqModel(), settings())
