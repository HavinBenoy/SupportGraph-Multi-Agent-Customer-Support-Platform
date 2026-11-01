"""Offline stand-in for the FAQ language model: EXTRACTIVE, not generative.

It reads the <sources> and <question> blocks that app.rag.prompts builds, picks the source sentences that share the
most meaningful words with the question, and returns them with their [source-id] citations. It can therefore only
ever repeat what the sources say: ideal for deterministic tests of the pipeline (retrieval -> generation ->
validation), useless for judging how a real model phrases or reasons. If nothing in the sources overlaps enough with
the question it answers INSUFFICIENT_CONTEXT, the same sentinel a real model is told to use.

The prompt format is a contract between app/rag/prompts.py and this parser; tests/rag/test_prompts.py enforces it.
"""

import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.llm.lexical import tokens

SENTINEL = "INSUFFICIENT_CONTEXT"
MIN_COVERAGE = 0.4  # at least 40% of the question's meaningful words must appear in the chosen sentence (+ its heading)
MAX_SENTENCES = 3

_SOURCE = re.compile(r'<source id="([^"]+)" title="([^"]*)">\n(.*?)\n</source>', re.S)
_QUESTION = re.compile(r"<question>\n?(.*?)\n?</question>", re.S)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def _candidates(text: str) -> list[str]:
    """Split a passage into answerable units: sentences, list items, and table rows (rendered with their column names)."""
    out: list[str] = []
    header: list[str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue  # the |---|---| separator row
            if header is None:
                header = cells
                continue
            out.append(cells[0] + ": " + "; ".join(f"{h} {c}" for h, c in zip(header[1:], cells[1:])))
        elif s.startswith(("- ", "* ")):
            out.append(s[2:].strip())
        else:
            out.extend(p.strip() for p in _SENTENCE_END.split(s) if p.strip())
    return out


def extractive_answer(prompt: str) -> str:
    q = _QUESTION.search(prompt)
    sources = _SOURCE.findall(prompt)
    if not q or not sources:
        return SENTINEL
    want = set(tokens(_unescape(q.group(1))))
    if not want:
        return SENTINEL

    scored: list[tuple[float, int, int, str, str]] = []  # (coverage, source rank, position, source id, sentence)
    for rank, (sid, title, body) in enumerate(sources):
        heading_tokens = set(tokens(_unescape(title)))  # a passage inherits the topic of its heading
        for pos, sentence in enumerate(_candidates(_unescape(body))):
            coverage = len(want & (heading_tokens | set(tokens(sentence)))) / len(want)
            if coverage >= MIN_COVERAGE:
                scored.append((coverage, rank, pos, sid, sentence))
    if not scored:
        return SENTINEL

    best = max(s[0] for s in scored)
    keep = [s for s in scored if s[0] >= 0.6 * best]
    keep.sort(key=lambda s: (-s[0], s[1], s[2]))  # best match first; ties go to the better-ranked source
    chosen = keep[:MAX_SENTENCES]
    chosen.sort(key=lambda s: (s[1], s[2]))  # read in source order
    seen: set[str] = set()
    parts = []
    for _cov, _rank, _pos, sid, sentence in chosen:
        clean = sentence.replace("**", "").strip()
        if clean not in seen:
            seen.add(clean)
            parts.append(f"{clean} [{sid}]")
    return " ".join(parts)


class ExtractiveFaqModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "extractive-faq-fake"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        prompt = next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=extractive_answer(prompt)))])
