"""Markdown-aware chunking.

Why chunk at all: an embedding is one vector per piece of text. A whole article averages many topics into one
vector ("returns" + "shipping fee" + "exchanges"), so a question about one of them matches weakly. Small,
single-topic chunks match sharply, and the model only has to read the relevant passage.
Example: "Do you accept PayPal?" should retrieve the "Accepted payment methods" passage, not the whole
Payments article.

Rules, in order of importance:
1. Split on headings first: a section is the natural unit of meaning in a help article.
2. Never cut a table row or a list item in half (the shipping prices live in a table row).
3. Only when one section is longer than `max_chars` split it further, on paragraph then sentence boundaries.
4. Every chunk carries a breadcrumb ("Returns and Refunds > Return window"); it is embedded together with the text
   so a short passage like "We do not process direct exchanges." still knows it is about returns.
"""

import re
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 900  # roughly 150 words: small enough to stay on one topic, big enough to hold a full answer

_HEADING = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class TextChunk:
    index: int
    heading: str  # breadcrumb, e.g. "Returns and Refunds > Return window"
    text: str

    @property
    def embedding_input(self) -> str:
        """What gets embedded: the breadcrumb plus the passage."""
        return f"{self.heading}\n{self.text}"

    @property
    def token_count(self) -> int:
        return len(self.text.split())


def _is_structured(block: str) -> bool:
    """Tables and bullet lists: their lines are independent items and must not be glued into sentences."""
    lines = [ln for ln in block.splitlines() if ln.strip()]
    return bool(lines) and all(ln.lstrip().startswith(("|", "- ", "* ")) for ln in lines)


def _pack(pieces: list[str], max_chars: int, joiner: str) -> list[str]:
    out: list[str] = []
    cur = ""
    for piece in pieces:
        candidate = f"{cur}{joiner}{piece}" if cur else piece
        if cur and len(candidate) > max_chars:
            out.append(cur)
            cur = piece
        else:
            cur = candidate
    if cur:
        out.append(cur)
    return out


def _hard_split(piece: str, max_chars: int) -> list[str]:
    """Last resort for one sentence/row longer than max_chars (no punctuation, a pasted URL list, ...): cut at
    word boundaries so no passage ever exceeds the limit. A single token longer than the limit is cut as-is."""
    if len(piece) <= max_chars:
        return [piece]
    words = [w[i:i + max_chars] for w in piece.split() for i in range(0, len(w), max_chars)]
    return _pack(words, max_chars, " ")


def _split_block(block: str, max_chars: int) -> list[str]:
    if len(block) <= max_chars:
        return [block]
    if _is_structured(block):
        pieces, joiner = [ln for ln in block.splitlines() if ln.strip()], "\n"
    else:
        pieces, joiner = [s for s in _SENTENCE_END.split(block) if s.strip()], " "
    return _pack([part for piece in pieces for part in _hard_split(piece, max_chars)], max_chars, joiner)


def _split_section(lines: list[str], max_chars: int) -> list[str]:
    text = "\n".join(lines).strip()
    if not text:
        return []
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    pieces = [part for b in blocks for part in _split_block(b, max_chars)]
    return _pack(pieces, max_chars, "\n\n")


def chunk_markdown(body: str, *, doc_title: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[TextChunk]:
    """Split a markdown article into heading-scoped chunks. Deterministic: same input, same chunks."""
    if max_chars < 100:
        raise ValueError("max_chars is too small to hold a meaningful passage")

    sections: list[tuple[str, list[str]]] = []  # (breadcrumb, lines)
    h2 = h3 = None
    current: list[str] = []

    def breadcrumb() -> str:
        return " > ".join(p for p in (doc_title, h2, h3) if p)

    def flush() -> None:
        if any(ln.strip() for ln in current):
            sections.append((breadcrumb(), list(current)))
        current.clear()

    for line in body.splitlines():
        m = _HEADING.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1:  # the article title; the caller already passes it as doc_title
                flush()
                continue
            flush()
            if level == 2:
                h2, h3 = title, None
            else:
                h3 = title
        else:
            current.append(line)
    flush()

    chunks: list[TextChunk] = []
    for crumb, lines in sections:
        for part in _split_section(lines, max_chars):
            chunks.append(TextChunk(index=len(chunks), heading=crumb, text=part))
    return chunks
