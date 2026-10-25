"""Model gateway: the ONLY module that knows which LLM provider is in use.

Everything else asks for a model by *role* ("action", "triage", "qa", ...) and gets a
LangChain chat model back. Swapping Ollama for a paid API, or giving the QA critic a
stronger model than triage, is then a config change, not a code change.
"""

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings, get_settings


def get_chat_model(role: str = "default", settings: Settings | None = None) -> BaseChatModel:
    """Return the chat model for `role`.

    `role` is not used to pick different models yet (all roles share one model today);
    it is part of the signature now so per-role routing lands later without touching callers.
    """
    s = settings or get_settings()

    if s.llm_provider == "fake":
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
