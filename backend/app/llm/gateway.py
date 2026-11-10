"""Model gateway: the ONLY module that knows which LLM provider is in use.

Everything else asks for a model by *role* ("action", "triage", "qa", ...) and gets a
LangChain chat model back. Swapping Ollama for a paid API, or giving the QA critic a
stronger model than triage, is then a config change, not a code change.
"""

import hashlib

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings, get_settings
from app.llm.embeddings import EmbeddingClient
from app.llm.guard import check_endpoint_allowed


def _role_model(role: str, s: Settings) -> str:
    return getattr(s, f"llm_model_{role}", "") or s.llm_model


def _sampling(s: Settings, *, ollama: bool) -> dict:
    """Only the sampling options that were explicitly set are sent, so provider defaults stay untouched otherwise."""
    out = {}
    if s.llm_top_p is not None:
        out["top_p"] = s.llm_top_p
    if s.llm_seed is not None:
        out["seed"] = s.llm_seed
    if ollama:
        if s.llm_top_k is not None:
            out["top_k"] = s.llm_top_k
        if s.llm_repeat_penalty is not None:
            out["repeat_penalty"] = s.llm_repeat_penalty
    return out


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
        extra: dict = {}
        if s.llm_num_thread:
            extra["num_thread"] = s.llm_num_thread
        if s.obs_ollama_metrics:
            from app.observability.stages import OllamaMetricsHandler

            extra["callbacks"] = [OllamaMetricsHandler(role)]
        cap = s.triage_max_output_tokens if (role == "triage" and s.triage_max_output_tokens) else s.llm_max_output_tokens
        if role == "impact":
            cap = s.impact_max_output_tokens
        return ChatOllama(
            model=model, base_url=s.llm_base_url, temperature=s.llm_temperature, **_sampling(s, ollama=True),
            num_ctx=s.llm_num_ctx, num_predict=cap, keep_alive=s.llm_keep_alive,
            reasoning=s.llm_thinking,
            client_kwargs={"timeout": s.llm_timeout_seconds}, **extra,
        )

    if s.llm_provider == "openai_compat":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise RuntimeError('OpenAI-compatible provider needs: pip install -e "backend[openai]"') from e
        return ChatOpenAI(
            model=model, base_url=s.llm_base_url, api_key=s.llm_api_key or "none", temperature=s.llm_temperature,
            **({"top_p": s.llm_top_p} if s.llm_top_p is not None else {}), **({"seed": s.llm_seed} if s.llm_seed is not None else {}),
            request_timeout=s.llm_timeout_seconds, max_retries=s.llm_max_retries, max_tokens=s.llm_max_output_tokens,
        )

    raise ValueError(f"Unknown LLM provider: {s.llm_provider}")


NOMIC_QUERY_PREFIX = "search_query: "
NOMIC_DOCUMENT_PREFIX = "search_document: "


def effective_embedding_prefixes(s: Settings) -> tuple[str, str]:
    """(query_prefix, document_prefix) actually used. Explicit settings win; otherwise nomic-embed-text gets its documented
    prefixes in "auto" mode. The fake embedder never gets automatic prefixes."""
    if s.embedding_query_prefix or s.embedding_document_prefix:
        return s.embedding_query_prefix, s.embedding_document_prefix
    if s.embedding_provider != "fake" and s.embedding_prefix_mode == "auto" and s.embedding_model.lower().startswith("nomic-embed-text"):
        return NOMIC_QUERY_PREFIX, NOMIC_DOCUMENT_PREFIX
    return "", ""


def _prefix_tag(query_prefix: str, document_prefix: str) -> str:
    """Part of the stored embedding name. Vectors made with different prefixes live in different spaces, so a prefix change
    must look like a different model: stale vectors are then never searched and `make ingest` re-embeds. No prefixes = no tag
    (names, and therefore already-ingested vectors, stay as they were)."""
    if not (query_prefix or document_prefix):
        return ""
    return "@px-" + hashlib.sha1(f"{query_prefix}|{document_prefix}".encode()).hexdigest()[:6]


def keep_alive_seconds(value: str) -> int:
    """Ollama's keep_alive as a whole number of seconds: "30m" -> 1800, "1h" -> 3600, "90s" -> 90, "300" -> 300, "-1" -> -1 (forever), "0" -> 0 (unload now)."""
    v = str(value).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600}
    try:
        if v and v[-1] in units:
            return int(float(v[:-1]) * units[v[-1]])
        return int(float(v))
    except ValueError:
        raise ValueError(f"SG_LLM_KEEP_ALIVE must look like 30m, 1h, 90s, 300 or -1, not {value!r}") from None


def get_embedding_client(settings: Settings | None = None) -> EmbeddingClient:
    """Return the configured embedding model, wrapped so vectors are always the width the database stores.

    Like get_chat_model, this is the only place that knows which provider is in use. The returned client's `name`
    is stored next to every vector: retrieval only compares vectors that came from the same model.
    """
    from app.db.models import EMBEDDING_DIM

    s = settings or get_settings()
    if s.embedding_provider != "fake":
        check_endpoint_allowed(s.embedding_base_url, s.allow_external_llm, what="embedding model")
    qp, dp = effective_embedding_prefixes(s)
    common = dict(dim=EMBEDDING_DIM, query_prefix=qp, document_prefix=dp)
    tag = _prefix_tag(qp, dp)

    if s.embedding_provider == "fake":
        from app.llm.fake_embeddings import MODEL_NAME, HashingEmbeddings

        return EmbeddingClient(inner=HashingEmbeddings(EMBEDDING_DIM), name=f"fake:{MODEL_NAME}-{EMBEDDING_DIM}", **common)

    if s.embedding_provider == "ollama":
        try:
            from langchain_ollama import OllamaEmbeddings
        except ImportError as e:
            raise RuntimeError('Ollama provider needs: pip install -e "backend[ollama]"') from e
        # M3.1: without keep_alive Ollama unloads the embedder after its 5-minute default. OllamaEmbeddings takes SECONDS (an int), chat models take "30m".
        inner = OllamaEmbeddings(model=s.embedding_model, base_url=s.embedding_base_url, keep_alive=keep_alive_seconds(s.llm_keep_alive))
        return EmbeddingClient(inner=inner, name=f"ollama:{s.embedding_model}{tag}", **common)

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
        return EmbeddingClient(inner=inner, name=f"openai_compat:{s.embedding_model}{tag}", **common)

    raise ValueError(f"Unknown embedding provider: {s.embedding_provider}")


def get_triager(settings: Settings | None = None):
    """The triage classifier (build step 3): offline rules with the fake provider, otherwise structured output from the
    configured chat model."""
    s = settings or get_settings()
    if s.llm_provider == "fake":
        from app.llm.fake_triage import RuleTriage

        return RuleTriage()
    from app.agents.triage import LlmTriager

    return LlmTriager(get_chat_model("triage", s), cache_size=s.triage_cache_size, cache_ttl=s.triage_cache_ttl_seconds, prompt=s.triage_prompt)


def get_rewriter(settings: Settings | None = None):
    """The search-query rewriter for corrective RAG: glossary expansion offline, otherwise the chat model."""
    s = settings or get_settings()
    if s.llm_provider == "fake":
        from app.rag.rewrite import GlossaryRewriter

        return GlossaryRewriter()
    if s.rewriter_mode == "glossary":  # M1b: no model call for query rewriting unless asked for (unmeasured with the real model)
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
    if s.llm_provider == "fake" or not s.qa_llm_critic:  # M1b: the LLM critic is opt-in; the deterministic QA checks always run
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


def get_judge_chat(settings: Settings | None = None):
    """The chat model for the section editor: the "impact" model with thinking on (setting), the OUTPUT limit `kb_ai_judge_max_tokens` (num_predict: thinking + the revised section),
    Ollama's TOTAL window `kb_ai_judge_num_ctx` and a long timeout. The input size of a section is a separate limit (`kb_ai_context_tokens`, see impact/context.py). None when off."""
    s = settings or get_settings()
    if s.kb_ai_judge == "off":
        return None
    if s.llm_provider != "ollama":
        return get_chat_model("impact", s)
    s2 = s.model_copy(update={"llm_thinking": s.kb_ai_judge_thinking, "impact_max_output_tokens": s.kb_ai_judge_max_tokens, "llm_timeout_seconds": s.kb_ai_judge_timeout_s,
                              "llm_num_ctx": s.kb_ai_judge_num_ctx})
    return get_chat_model("impact", s2)


def get_check_chat(settings: Settings | None = None):
    """The short second opinion on a revision (PASS / FAIL / UNSURE and a reason): same model, thinking OFF, a small output cap. None when the check is off."""
    s = settings or get_settings()
    if s.kb_ai_judge == "off" or s.kb_ai_semantic_check == "off":
        return None
    if s.llm_provider != "ollama":
        return get_chat_model("impact", s)
    s2 = s.model_copy(update={"llm_thinking": False, "impact_max_output_tokens": 200, "llm_timeout_seconds": min(s.kb_ai_judge_timeout_s, 180.0), "llm_num_ctx": s.kb_ai_judge_num_ctx})
    return get_chat_model("impact", s2)


def get_impact_model(settings: Settings | None = None):
    """The knowledge impact-analysis model (Part 2). "rules" mode and the fake provider use the deterministic rule engine (no model call, fully offline); otherwise the
    chat model of role "impact" (SG_LLM_MODEL_IMPACT, falling back to SG_LLM_MODEL) fills validated schemas. The rule engine's checks run after EVERY model."""
    s = settings or get_settings()
    from app.knowledge.impact.evaluate import LlmImpactModel, RuleImpactModel

    if s.kb_ai_mode == "rules" or (s.kb_ai_mode == "auto" and s.llm_provider == "fake"):
        return RuleImpactModel()
    return LlmImpactModel(get_chat_model("impact", s), name=f"{s.llm_provider}:{_role_model('impact', s)}", judge_chat=get_judge_chat(s), check_chat=get_check_chat(s))
