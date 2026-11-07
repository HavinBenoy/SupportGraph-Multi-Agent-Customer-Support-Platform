"""M3.1 finding from the first real trace: `ollama ps` listed only the chat model. The embedder was loaded without keep_alive, so Ollama unloaded it after its
5-minute default and the next question paid a reload. The gateway now sends the same keep_alive for embeddings as for chat (real langchain_ollama, mock server)."""

from app.config import Settings
from app.llm.gateway import get_embedding_client
from tests.bench.mock_ollama import MockOllama


def test_embedding_requests_carry_the_configured_keep_alive():
    with MockOllama() as m:
        s = Settings(_env_file=None, llm_provider="ollama", embedding_provider="ollama", llm_base_url=m.url, embedding_base_url=m.url, llm_keep_alive="45m")  # -> 2700 seconds
        client = get_embedding_client(s)
        client.embed_query("how do I pay fees")
        embeds = [b for p, b in m.calls if p == "/api/embed"]
        assert embeds, [p for p, _ in m.calls]
        assert all(b.get("keep_alive") == 2700 for b in embeds)


def test_keep_alive_strings_become_whole_seconds_for_the_embedder():
    import pytest

    from app.llm.gateway import keep_alive_seconds

    assert [keep_alive_seconds(v) for v in ("30m", "1h", "90s", "300", "-1", "0", " 2H ", "1.5m")] == [1800, 3600, 90, 300, -1, 0, 7200, 90]
    with pytest.raises(ValueError, match="KEEP_ALIVE"):
        keep_alive_seconds("soon")


def test_the_default_setting_works_with_the_real_embedder_class():
    """The default is "30m": a plain string would be rejected by OllamaEmbeddings (it wants an int), so this must construct and send 1800."""
    with MockOllama() as m:
        s = Settings(_env_file=None, llm_provider="ollama", embedding_provider="ollama", llm_base_url=m.url, embedding_base_url=m.url)
        assert s.llm_keep_alive == "30m"
        get_embedding_client(s).embed_query("hello")
        assert [b.get("keep_alive") for p, b in m.calls if p == "/api/embed"] == [1800]
