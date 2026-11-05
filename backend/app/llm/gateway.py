"""Model gateway: the ONLY module that knows which LLM provider is in use.

Everything else asks for a model by *role* ("action", "triage", "qa", ...) and gets a
LangChain chat model back. Swapping Ollama for a paid API, or giving the QA critic a
stronger model than triage, is then a config change, not a code change.
"""

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings, get_settings
from app.llm.embeddings import EmbeddingClient
from app.llm.guard import check_endpoint_allowed


def _role_model(role: str, s: Settings) -> str:
    return getattr(s, f"llm_model_{role}", "") or s.llm_model


def get_chat_model(role: str = "default", settings: Settings | None = None) -> BaseChatModel:
    """Return the chat model for `role`, with the configured timeout, output cap, context size and thinking mode.

    Raises ExternalEndpointRefused if the endpoint is not local and SG_ALLOW_EXTERNAL_LLM is not true.
    """
    s = settings or get_settings()

    if s.llm_provider == "fake":
        if role == "faq":  # the RAG FAQ bot (build step 2) gets an offline extractive model
            from app.llm.fake_faq import ExtractiveFaqModel

            return ExtractiveFaqModel()
        from app.llm.fake import ScriptedChatModel

        return ScriptedChatModel()

    check_endpoint_allowed(s.llm_base_url, s.allow_external_llm, what="chat model")
    model = _role_model(role, s)

    if s.llm_provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as e:
            raise RuntimeError('Ollama provider needs: pip install -e "backend[ollama]"') from e
        return ChatOllama(
            model=model, base_url=s.llm_base_url, temperature=0,
            num_ctx=s.llm_num_ctx, num_predict=s.llm_max_output_tokens, keep_alive=s.llm_keep_alive,
            reasoning=s.llm_thinking,
            client_kwargs={"timeout": s.llm_timeout_seconds},
        )

    if s.llm_provider == "openai_compat":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise RuntimeError('OpenAI-compatible provider needs: pip install -e "backend[openai]"') from e
        return ChatOpenAI(
            model=model, base_url=s.llm_base_url, api_key=s.llm_api_key or "none", temperature=0,
            request_timeout=s.llm_timeout_seconds, max_retries=s.llm_max_retries, max_tokens=s.llm_max_output_tokens,
        )

    raise ValueError(f"Unknown LLM provider: {s.llm_provider}")


def get_embedding_client(settings: Settings | None = None) -> EmbeddingClient:
    """Return the configured embedding model, wrapped so vectors are always the width the database stores.

    Like get_chat_model, this is the only place that knows which provider is in use. The returned client's `name`
    is stored next to every vector: retrieval only compares vectors that came from the same model.
    """
    from app.db.models import EMBEDDING_DIM

    s = settings or get_settings()
    if s.embedding_provider != "fake":
        check_endpoint_allowed(s.embedding_base_url, s.allow_external_llm, what="embedding model")
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


def get_proposer(settings: Settings | None = None):
    """Turns a message into an action PROPOSAL (build step 4): offline rules with the fake provider, else structured output."""
    s = settings or get_settings()
    if s.llm_provider == "fake":
        from app.actions.proposer import RuleProposer

        return RuleProposer()
    from app.actions.proposer import LlmProposer

    return LlmProposer(get_chat_model("proposer", s))


def get_critic(settings: Settings | None = None):
    """The optional LLM critic of the QA loop. None with the fake provider: the deterministic checks still run."""
    s = settings or get_settings()
    if s.llm_provider == "fake":
        return None
    from app.qa.critic import LlmCritic

    return LlmCritic(get_chat_model("critic", s))


def get_reviser(settings: Settings | None = None):
    s = settings or get_settings()
    if s.llm_provider == "fake":
        from app.qa.revise import DeterministicReviser

        return DeterministicReviser()
    from app.qa.critic import LlmReviser

    return LlmReviser(get_chat_model("reviser", s))


def get_judge(settings: Settings | None = None):
    """The groundedness/relevance judge used by evaluations (build step 6). See app/evals/judge.py."""
    from app.evals.judge import get_judge as _get

    return _get(settings)
