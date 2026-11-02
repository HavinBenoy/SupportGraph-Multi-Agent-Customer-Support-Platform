"""Model gateway: the ONLY module that knows which LLM provider is in use.

Everything else asks for a model by *role* ("action", "triage", "qa", ...) and gets a
LangChain chat model back. Swapping Ollama for a paid API, or giving the QA critic a
stronger model than triage, is then a config change, not a code change.
"""

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings, get_settings
from app.llm.embeddings import EmbeddingClient


def get_chat_model(role: str = "default", settings: Settings | None = None) -> BaseChatModel:
    """Return the chat model for `role`.

    `role` is not used to pick different models yet (all roles share one model today);
    it is part of the signature now so per-role routing lands later without touching callers.
    """
    s = settings or get_settings()

    if s.llm_provider == "fake":
        if role == "faq":  # the RAG FAQ bot (build step 2) gets an offline extractive model
            from app.llm.fake_faq import ExtractiveFaqModel

            return ExtractiveFaqModel()
        from app.llm.fake import ScriptedChatModel

        return ScriptedChatModel()

    if s.llm_provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as e:
            raise RuntimeError('Ollama provider needs: pip install -e "backend[ollama]"') from e
        return ChatOllama(model=s.llm_model, base_url=s.llm_base_url, temperature=0)

    if s.llm_provider == "openai_compat":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise RuntimeError('OpenAI-compatible provider needs: pip install -e "backend[openai]"') from e
        return ChatOpenAI(model=s.llm_model, base_url=s.llm_base_url, api_key=s.llm_api_key or "none", temperature=0)

    raise ValueError(f"Unknown LLM provider: {s.llm_provider}")


def get_embedding_client(settings: Settings | None = None) -> EmbeddingClient:
    """Return the configured embedding model, wrapped so vectors are always the width the database stores.

    Like get_chat_model, this is the only place that knows which provider is in use. The returned client's `name`
    is stored next to every vector: retrieval only compares vectors that came from the same model.
    """
    from app.db.models import EMBEDDING_DIM

    s = settings or get_settings()
    common = dict(dim=EMBEDDING_DIM, query_prefix=s.embedding_query_prefix, document_prefix=s.embedding_document_prefix)

    if s.embedding_provider == "fake":
        from app.llm.fake_embeddings import MODEL_NAME, HashingEmbeddings

        return EmbeddingClient(inner=HashingEmbeddings(EMBEDDING_DIM), name=f"fake:{MODEL_NAME}-{EMBEDDING_DIM}", **common)

    if s.embedding_provider == "ollama":
        try:
            from langchain_ollama import OllamaEmbeddings
        except ImportError as e:
            raise RuntimeError('Ollama provider needs: pip install -e "backend[ollama]"') from e
        inner = OllamaEmbeddings(model=s.embedding_model, base_url=s.embedding_base_url)
        return EmbeddingClient(inner=inner, name=f"ollama:{s.embedding_model}", **common)

    if s.embedding_provider == "openai_compat":
        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as e:
            raise RuntimeError('OpenAI-compatible provider needs: pip install -e "backend[openai]"') from e
        extra = {"dimensions": EMBEDDING_DIM} if s.embedding_model.startswith("text-embedding-3") else {}
        inner = OpenAIEmbeddings(
            model=s.embedding_model, base_url=s.embedding_base_url, api_key=s.embedding_api_key or "none",
            check_embedding_ctx_length=False,  # non-OpenAI servers do not accept pre-tokenised input
            **extra,
        )
        return EmbeddingClient(inner=inner, name=f"openai_compat:{s.embedding_model}", **common)

    raise ValueError(f"Unknown embedding provider: {s.embedding_provider}")


def get_triager(settings: Settings | None = None):
    """The triage classifier (build step 3): offline rules with the fake provider, otherwise structured output from the
    configured chat model."""
    s = settings or get_settings()
    if s.llm_provider == "fake":
        from app.llm.fake_triage import RuleTriage

        return RuleTriage()
    from app.agents.triage import LlmTriager

    return LlmTriager(get_chat_model("triage", s))


def get_rewriter(settings: Settings | None = None):
    """The search-query rewriter for corrective RAG: glossary expansion offline, otherwise the chat model."""
    s = settings or get_settings()
    if s.llm_provider == "fake":
        from app.rag.rewrite import GlossaryRewriter

        return GlossaryRewriter()
    from app.rag.rewrite import LlmRewriter

    return LlmRewriter(get_chat_model("rewrite", s))
