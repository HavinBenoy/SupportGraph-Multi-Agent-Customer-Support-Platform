"""The single-agent RAG FAQ baseline (build step 2).

    START -> retrieve --(any relevant passage?)--> generate -> validate -> END
                  \\--(none)--------------------> abstain ---------------> END

Deliberately simple: no query rewriting, no retries, no grading, no tools. It is the benchmark the multi-agent
system (step 3 onwards) has to beat, so it must be a fair, plain RAG bot, not a straw man.
It is still a LangGraph graph (not a bare function) so that Langfuse shows each stage as its own span, exactly as it
does for the step 0 agent, and so the step 3 knowledge subgraph can reuse these nodes.

Where "hallucinated policy" is stopped (all in code, none of it trusting the model):
  1. retrieve   - only passages above `rag_min_score` reach the model; if none do, the model is never called.
  2. generate   - the prompt says "answer only from the sources or reply INSUFFICIENT_CONTEXT" (app/rag/prompts.py).
  3. retry      - ONLY an `uncited_answer` is regenerated, once, with an explicit reminder to cite (a small model often writes a correct
                  but uncited sentence). The retry goes through exactly the same validate step; if it fails too, the bot abstains.
                  No other failure is retried, and code never attaches a citation the model did not write.
  4. validate   - a reply cut off at the output-token limit is never accepted (even with a valid citation); otherwise the
                  answer is accepted only if it cites at least one source AND every cited source was retrieved.
                  An uncited answer, an invented citation, or the sentinel all become a safe abstention.
What this does NOT catch: a model that cites a real source but misstates what it says. Judging that needs a
groundedness critic (build step 4) and evals (step 6); the baseline's numbers will show how often it matters.

The tenant comes from trusted run config (config["configurable"]["tenant_id"]), never from the question.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.config import Settings, get_settings
from app.db.session import session_scope
from app.llm.embeddings import EmbeddingClient
from app.llm.fake_faq import SENTINEL
from app.llm.gateway import get_chat_model, get_embedding_client
from app.llm.guard import invoke_guarded
from app.llm.reasoning import visible_answer
from app.llm.truncation import finish_reason, is_truncated
from app.observability import stages
from app.observability.tracing import instrument_config
from app.rag.prompts import CITATION, build_messages
from app.rag.retrieval import RetrievedChunk, count_chunks, search, search_hybrid
from app.tenants.loader import load_tenant_config
from app.tools.scope import ScopeError

MAX_SEARCH_CHARS = 800  # a rewritten query may be longer than the question (it carries expansions)
CITATION_REMINDER = (
    "Your previous reply contained no source citation, so it cannot be shown. Answer the same question again using only the sources. "
    "End every sentence that states a fact with the id of its source in square brackets, for example [{example}]. "
    "Use only ids from the sources. If the sources do not contain the answer, reply exactly {sentinel} and nothing else.")
MAX_QUESTION_CHARS = 500  # a help-centre question is a sentence or two; this bounds cost and the injection surface

# What the user sees when the bot will not answer. It makes no promise it cannot keep (no "I'll escalate").
ABSTAIN_MESSAGE = (
    "I'm sorry, I couldn't find that in our help articles, so I don't want to guess. "
    "Please contact our support team and they will be able to help."
)


# A model told to emit SENTINEL when the sources lack the answer sometimes says so in its own words ("The sources do not mention X").
# That is an honest non-answer, but it must take the standard abstention path (handoff wording, logged as unanswered), not be
# shown as a sourced answer. Detection is deliberately narrow and only ever moves a reply TOWARDS abstention (fail closed).
_NO_INFO = re.compile(
    r"\b(?:sources?|context|documents?|provided (?:text|information|material))\b[^.\n]{0,40}?\b(?:do(?:es)? not|don't|doesn't|did not|never)\b"
    r"[^.\n]{0,20}?\b(?:mention|contain|provide|say|specify|include|cover|address|state|have|list|give)\b"
    r"|\bnot (?:mentioned|covered|specified|stated|provided) in the (?:sources?|context|documents?)\b"
    r"|\bno (?:information|details?|mention) (?:is |are |was )?(?:available |provided |given |found )?(?:about|on|regarding|in the (?:sources?|context))\b"
    r"|\b(?:i )?(?:cannot|can't|could not|couldn't|am unable to|am not able to) (?:find|determine|confirm)\b"
    r"|\bi (?:do not|don't) have (?:any |enough )?information\b", re.I)


_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


def declares_no_information(text: str) -> bool:
    """True if any part of the reply says, in its own words, that the sources do not contain the answer."""
    return bool(_NO_INFO.search(CITATION.sub(" ", text or "")))


def declines_without_answer(text: str) -> bool:
    """A refusal in the model's own words with nothing else of substance. A reply that declines one part but ALSO gives a cited answer
    to another part (a two-part question) is not a refusal: its cited part still goes through the normal citation checks."""
    sentences: list[str] = []
    for s in (x for x in _SENTENCES.split(text or "") if x.strip()):
        if sentences and not CITATION.sub("", s).strip(" .;:-"):  # a citation standing alone after the full stop belongs to the sentence before
            sentences[-1] += " " + s
        else:
            sentences.append(s)
    declines = [s for s in sentences if _NO_INFO.search(CITATION.sub(" ", s))]
    if not declines:
        return False
    answered = [s for s in sentences if s not in declines and CITATION.search(s)]
    return not answered


def trim_context(found: list[RetrievedChunk], s: Settings) -> list[RetrievedChunk]:
    """M3 (opt-in, both settings default to 0 = off): send the model fewer passages. `rag_context_band` keeps only passages scoring within
    that distance of the best one; `rag_max_context_chars` drops the lowest-ranked passages until the total text fits. The best passage is
    ALWAYS kept, and a dropped passage can no longer be cited (a citation of it fails validation), so trimming can only turn an answer into
    an abstention, never into an ungrounded answer. Passages are never cut mid-text."""
    if not found:
        return found
    keep = list(found)
    if s.rag_context_band > 0:
        top = max(c.score for c in keep)
        keep = [c for c in keep if c.score >= top - s.rag_context_band] or keep[:1]
    if s.rag_max_context_chars > 0:
        while len(keep) > 1 and sum(len(c.text) for c in keep) > s.rag_max_context_chars:
            keep.pop()
    return keep


class FaqState(TypedDict, total=False):
    question: str
    search_query: str  # optional: what to SEARCH for (a rewrite); the model is still asked `question`
    chunks: list[dict]  # passages above the score threshold, best first (RetrievedChunk.to_dict)
    top_score: float | None  # best similarity seen, even if below the threshold (for calibrating rag_min_score)
    raw_answer: str  # what a person may see: the model's reply with any reasoning removed
    reasoning_unfinished: bool
    generation_attempts: int  # 1 normally; 2 when an uncited answer was regenerated once
    truncated: bool  # the model stopped at the output-token limit: the answer is incomplete and must never be shown
    finish_reason: str | None
    answer: str
    abstained: bool
    reason: str
    citations: list[str]


@dataclass(frozen=True)
class FaqAnswer:
    answer: str
    abstained: bool
    reason: str  # answered | no_relevant_context | no_documents_ingested | empty_question | question_too_long |
    #              model_abstained | model_declined | unfinished_reasoning | truncated_answer | uncited_answer | unknown_citation
    citations: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # the cited passages (id, title, heading, text, score)
    top_score: float | None = None


def _tenant_of(config: RunnableConfig) -> str:
    tenant_id = ((config or {}).get("configurable") or {}).get("tenant_id")
    if not tenant_id:
        raise ScopeError("FAQ graph run without tenant_id in config['configurable']")  # fail closed
    return tenant_id


def build_faq_graph(embedder: EmbeddingClient | None = None, model: BaseChatModel | None = None,
                    settings: Settings | None = None):
    s = settings or get_settings()
    embedder = embedder or get_embedding_client(s)
    model = model or get_chat_model("faq", s)

    def retrieve(state: FaqState, config: RunnableConfig) -> dict:
        tenant_id = _tenant_of(config)
        question = " ".join((state.get("question") or "").split())  # collapse whitespace and newlines
        if not question:
            return {"question": question, "chunks": [], "top_score": None, "reason": "empty_question"}
        if len(question) > MAX_QUESTION_CHARS:
            return {"question": question[:80] + "...", "chunks": [], "top_score": None, "reason": "question_too_long"}

        search_text = " ".join((state.get("search_query") or question).split())[:MAX_SEARCH_CHARS]
        with stages.stage("embed"):
            vector = embedder.embed_query(search_text)
        with stages.stage("search"):
            with session_scope() as db:
                if s.rag_retrieval_mode == "hybrid":
                    found = search_hybrid(db, tenant_id=tenant_id, query_vector=vector, query_text=search_text, model_name=embedder.name, top_k=s.rag_top_k)
                else:
                    found = search(db, tenant_id=tenant_id, query_vector=vector, model_name=embedder.name, top_k=s.rag_top_k)
                relevant = trim_context([c for c in found if c.score >= s.rag_min_score], s)
                never_ingested = not found and count_chunks(db, tenant_id=tenant_id, model_name=embedder.name) == 0
        stages.note("context_chunks", len(relevant))
        stages.note("context_chars", sum(len(c.text) for c in relevant))
        out: dict[str, Any] = {"question": question, "chunks": [c.to_dict() for c in relevant],
                               "top_score": round(max(c.score for c in found), 4) if found else None}
        if never_ingested:
            out["reason"] = "no_documents_ingested"
        return out

    def route(state: FaqState) -> str:
        return "generate" if state.get("chunks") else "abstain"

    def generate(state: FaqState, config: RunnableConfig) -> dict:
        with stages.stage("context"):
            institute_name = load_tenant_config(_tenant_of(config)).name
            chunks = [RetrievedChunk.from_dict(c) for c in state["chunks"]]
            messages = build_messages(state["question"], chunks, institute_name=institute_name)
        with stages.stage("llm"):
            reply = invoke_guarded(model, messages, s)
        visible, unfinished = visible_answer(str(reply.content))
        return {"raw_answer": visible, "reasoning_unfinished": unfinished, "finish_reason": finish_reason(reply),
                "truncated": is_truncated(reply, s.llm_max_output_tokens), "generation_attempts": 1}

    def regenerate(state: FaqState, config: RunnableConfig) -> dict:
        """The one citation retry. Same prompt, same model, plus a reminder; its output is validated like any other."""
        institute_name = load_tenant_config(_tenant_of(config)).name
        chunks = [RetrievedChunk.from_dict(c) for c in state["chunks"]]
        messages = build_messages(state["question"], chunks, institute_name=institute_name)
        messages.append(HumanMessage(CITATION_REMINDER.format(example=chunks[0].source_id, sentinel=SENTINEL)))
        stages.note("citation_regenerated", True)
        with stages.stage("llm_retry"):
            reply = invoke_guarded(model, messages, s)
        visible, unfinished = visible_answer(str(reply.content))
        return {"raw_answer": visible, "reasoning_unfinished": unfinished, "finish_reason": finish_reason(reply),
                "truncated": is_truncated(reply, s.llm_max_output_tokens), "generation_attempts": 2}

    def after_validate(state: FaqState) -> str:
        retry = (state.get("reason") == "uncited_answer" and state.get("generation_attempts", 1) < 2 and s.faq_citation_retries > 0)
        return "regenerate" if retry else "end"

    def validate(state: FaqState) -> dict:
        with stages.stage("validate"):
            return _validate(state)

    def _validate(state: FaqState) -> dict:
        raw = state.get("raw_answer", "")
        retrieved = {c["source_id"]: c for c in state["chunks"]}
        cited = list(dict.fromkeys(CITATION.findall(raw)))  # unique, in order of first use
        if state.get("truncated"):  # checked FIRST: a cut-off reply can still contain a valid citation, which proves nothing
            reason = "truncated_answer"
        elif state.get("reasoning_unfinished"):  # the model never finished thinking: there is no answer to show
            reason = "unfinished_reasoning"
        elif not raw or SENTINEL in raw:  # "answer + sentinel" is treated as a refusal too: fail closed
            reason = "model_abstained"
        elif declines_without_answer(raw):  # a refusal in the model's own words, even with a citation attached
            reason = "model_declined"
        elif not cited:
            reason = "uncited_answer"
        elif any(c not in retrieved for c in cited):
            reason = "unknown_citation"
        else:
            return {"answer": raw, "abstained": False, "reason": "answered", "citations": cited}
        return {"answer": ABSTAIN_MESSAGE, "abstained": True, "reason": reason, "citations": []}

    def abstain(state: FaqState) -> dict:
        return {"answer": ABSTAIN_MESSAGE, "abstained": True, "citations": [],
                "reason": state.get("reason") or "no_relevant_context"}

    graph = StateGraph(FaqState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("validate", validate)
    graph.add_node("abstain", abstain)
    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", route, {"generate": "generate", "abstain": "abstain"})
    graph.add_edge("generate", "validate")
    graph.add_node("regenerate", regenerate)
    graph.add_conditional_edges("validate", after_validate, {"regenerate": "regenerate", "end": END})
    graph.add_edge("regenerate", "validate")
    graph.add_edge("abstain", END)
    return graph.compile()


def faq_config(tenant_id: str, *, ticket_id: str | None = None, student_id: str = "anonymous") -> dict:
    """Run config for the FAQ graph: trusted tenant scope plus (if enabled) a Langfuse trace.
    Trace attributes: session "<tenant>:<ticket>", user = student_id, tag "tenant:<tenant>"."""
    config = {"configurable": {"tenant_id": tenant_id}, "recursion_limit": 10}
    return instrument_config(config, tenant_id=tenant_id, student_id=student_id,
                             ticket_id=ticket_id or f"faq-{uuid.uuid4().hex[:8]}")


def answer_question(graph, question: str, *, tenant_id: str, ticket_id: str | None = None,
                    student_id: str = "anonymous") -> FaqAnswer:
    out = graph.invoke({"question": question}, faq_config(tenant_id, ticket_id=ticket_id, student_id=student_id))
    by_id = {c["source_id"]: c for c in out.get("chunks", [])}
    return FaqAnswer(
        answer=out["answer"], abstained=out["abstained"], reason=out["reason"], citations=out.get("citations", []),
        sources=[by_id[i] for i in out.get("citations", []) if i in by_id], top_score=out.get("top_score"),
    )
