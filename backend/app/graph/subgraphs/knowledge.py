"""Knowledge agent: corrective RAG over the tenant's public documents (build step 3).

    START -> rewrite -> answer --(abstained for a retrieval reason, attempts left, new query?)--> rewrite ...
                                \\--(otherwise)----------------------------------------------> END

`answer` is the step 2 FAQ graph unchanged (retrieve -> generate -> validate/abstain), so every guarantee from step 2 holds
here: public documents only, cite-or-abstain, the model never called when nothing relevant is retrieved.
What this adds: when the first search finds nothing usable, ONE more search is tried with a rewritten query, instead of
giving up on a vocabulary mismatch. The retry is capped (`knowledge_max_attempts`) and skipped if the rewrite is identical.

"Grading" here is the FAQ graph's own verdict (score threshold + cite-or-abstain), not a separate LLM grader. An LLM relevance
grader is real-model work (see README, known limits); a wrong-but-cited answer is not retried, because nothing flags it.
"""

from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.config import Settings, get_settings
from app.llm.gateway import get_rewriter
from app.rag.faq import build_faq_graph
from app.rag.rewrite import Rewriter
from app.tenants.loader import load_tenant_config
from app.tools.scope import ScopeError

# Abstentions where a different search might help. Not retried: empty/too-long question, nothing ingested (a retry cannot
# fix those), or an answer that was uncited / cited an unknown source (a model fault, handled by the QA critic in step 4).
RETRYABLE_REASONS = frozenset({"no_relevant_context", "model_abstained"})


class KnowledgeState(TypedDict, total=False):
    question: str
    attempt: int
    query: str
    queries: list[str]
    result: dict[str, Any]


def build_knowledge_graph(faq_graph=None, rewriter: Rewriter | None = None, settings: Settings | None = None):
    s = settings or get_settings()
    faq = faq_graph or build_faq_graph(settings=s)
    rewriter = rewriter or get_rewriter(s)

    def rewrite(state: KnowledgeState, config: RunnableConfig) -> dict:
        tenant_id = ((config or {}).get("configurable") or {}).get("tenant_id")
        if not tenant_id:
            raise ScopeError("Knowledge graph run without tenant_id in config['configurable']")
        synonyms = load_tenant_config(tenant_id).query_synonyms
        attempt = state.get("attempt", 0)
        query = rewriter.rewrite(state["question"], attempt=attempt, synonyms=synonyms)
        return {"query": query, "queries": [*state.get("queries", []), query]}

    def duplicate_query(state: KnowledgeState) -> bool:
        return state["query"] in state.get("queries", [])[:-1]

    def after_rewrite(state: KnowledgeState) -> str:
        return "stop" if duplicate_query(state) else "answer"  # searching the same words again cannot find anything new

    def answer(state: KnowledgeState, config: RunnableConfig) -> dict:
        out = faq.invoke({"question": state["question"], "search_query": state["query"]}, config)
        return {"result": out, "attempt": state.get("attempt", 0) + 1}

    def after_answer(state: KnowledgeState) -> str:
        r = state["result"]
        retry = r.get("abstained") and r.get("reason") in RETRYABLE_REASONS and state["attempt"] < s.knowledge_max_attempts
        return "rewrite" if retry else "stop"

    g = StateGraph(KnowledgeState)
    g.add_node("rewrite", rewrite)
    g.add_node("answer", answer)
    g.add_edge(START, "rewrite")
    g.add_conditional_edges("rewrite", after_rewrite, {"answer": "answer", "stop": END})
    g.add_conditional_edges("answer", after_answer, {"rewrite": "rewrite", "stop": END})
    return g.compile()
