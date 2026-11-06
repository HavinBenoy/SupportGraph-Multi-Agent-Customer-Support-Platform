"""Never show a model's reasoning to a person.

Some models (and some builds of a model, e.g. a "thinking" build run with thinking switched off) write their chain of thought
into the reply, wrapped in <think>...</think> or ending with </think>. That text is not an answer: it can contain draft
sentences, quoted source ids and rules the student must not see, and a citation inside it proves nothing about the final answer.
`visible_answer` returns only what follows the reasoning; an UNCLOSED <think> means the reasoning never finished, so there is no answer.
"""

import re

_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def visible_answer(text: str) -> tuple[str, bool]:
    """(text a person may see, reasoning_unfinished). Reasoning that is closed is removed; if it never closes there is no answer."""
    text = text or ""
    text = _BLOCK.sub("", text)
    if re.search(r"</think>", text, re.I):  # a closer with no opener (the opener was part of the template): keep what follows it
        text = re.split(r"</think>", text, flags=re.I)[-1]
    unfinished = bool(re.search(r"<think>", text, re.I))
    if unfinished:
        text = re.split(r"<think>", text, flags=re.I)[0]
    return text.strip(), unfinished


def strip_message(message):
    """`message` (an AIMessage) with reasoning removed from its text content. Unfinished reasoning leaves no text (tool calls are kept)."""
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return message
    visible, unfinished = visible_answer(content)
    if visible == content and not unfinished:
        return message
    extra = {**(getattr(message, "additional_kwargs", None) or {}), **({"reasoning_unfinished": True} if unfinished else {})}
    return message.model_copy(update={"content": visible, "additional_kwargs": extra})
