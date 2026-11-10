"""Adaptive context for the section editor: give the model the COMPLETE logical section a change belongs to, not a fixed number of neighbouring lines.

Three limits are kept apart (they are different resources):
  * INPUT capacity   `kb_ai_context_tokens`    how much document text one request may carry;
  * OUTPUT limit     `kb_ai_judge_max_tokens`  how much the model may write (num_predict). The model returns the WHOLE revised section, after thinking, so the section must be
                                               smaller than the output limit minus a thinking reserve, or the answer is cut off;
  * TOTAL window     `kb_ai_judge_num_ctx`     Ollama's num_ctx: prompt + thinking + answer must fit, and it sets the RAM of the attention cache.
`Budget.from_settings` turns them into the one number that matters (`section_tokens`) and never lets it exceed what any of the three allows.

Choosing a section for a candidate line (largest unit that fits, in this order):
  1. the WHOLE DOCUMENT when it fits (title, every heading, every list and table are context; one call per document);
  2. the SUBTREE of the heading the line sits under (the heading, its paragraphs, lists, tables and sub-sections);
  3. the SECTION itself (heading down to the next heading);
  4. when even that is too large: whole BLOCKS (paragraph, list, table, question+answer) around the line, widened block by block while they fit. A paragraph, a list with its lead-in,
     a question with its answer and a table row with its header are never separated.
Windows of the same section are merged; windows that overlap are never sent twice (two requests must not edit the same text)."""

import math
import re
from dataclasses import dataclass, field

from app.config import Settings
from app.knowledge.impact.passages import _HEADING, with_headings

CHARS_PER_TOKEN = 3.2                 # conservative for mixed prose / tables / Indian names; the real count is lower, so a section never overflows because of this estimate
PROMPT_OVERHEAD_TOKENS = 450          # instructions + change description + wrappers
_LIST = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_QUESTION = re.compile(r"^\s*(?:\*\*)?(?:Q|Question)\b[\s\d]*[:.)]", re.I)
_ANSWER = re.compile(r"^\s*(?:\*\*)?(?:A|Ans|Answer)\b[\s\d]*[:.)]", re.I)
_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def est_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Budget:
    section_tokens: int               # the largest section sent in one request
    context_tokens: int               # what was asked for (input capacity)
    max_output: int                   # num_predict
    num_ctx: int                      # Ollama total window
    limited_by: str                   # "input" | "output" | "window"

    @property
    def section_chars(self) -> int:
        return int(self.section_tokens * CHARS_PER_TOKEN)

    @staticmethod
    def from_settings(s: Settings) -> "Budget":
        thinking = s.kb_ai_thinking_reserve_tokens if s.kb_ai_judge_thinking else min(300, s.kb_ai_thinking_reserve_tokens)
        by_input = s.kb_ai_context_tokens
        by_output = int((s.kb_ai_judge_max_tokens - thinking) / 1.25)                    # the answer repeats the section (plus the edit)
        by_window = int((s.kb_ai_judge_num_ctx - PROMPT_OVERHEAD_TOKENS - s.kb_ai_judge_max_tokens) )   # prompt + the output cap must fit in num_ctx
        if s.kb_ai_context_mode == "fixed":
            by_input = est_tokens("x" * s.kb_ai_judge_window_chars)
        size = max(150, min(by_input, by_output, by_window))
        why = "input" if size == by_input else ("output" if size == by_output else "window")
        return Budget(size, by_input, s.kb_ai_judge_max_tokens, s.kb_ai_judge_num_ctx, why)

    def describe(self) -> str:
        return (f"section ≤ {self.section_tokens} tokens (input limit {self.context_tokens}, output limit {self.max_output}, Ollama window {self.num_ctx}; "
                f"limited by the {self.limited_by} limit)")


@dataclass(frozen=True)
class Row:
    start: int
    end: int
    text: str


@dataclass
class Section:
    rows: list[Row]
    heading: str                                         # "Parent > Child" of the first row
    kind: str                                            # document | subtree | section | blocks
    focus: set[int] = field(default_factory=set)         # absolute start offsets of the candidate lines inside
    partial: bool = False                                # the section is only a part of its logical section (it was too large)
    header: str = ""                                     # table header / column names shown as read-only context when the window does not include them
    note: str = ""
    headings: list[str] = field(default_factory=list)    # the heading chain above each row (parallel to `rows`)

    @property
    def text(self) -> str:
        return "\n".join(r.text for r in self.rows)

    @property
    def start(self) -> int:
        return self.rows[0].start

    @property
    def end(self) -> int:
        return self.rows[-1].end

    @property
    def tokens(self) -> int:
        return est_tokens(self.text)


# ----------------------------------------------------------------------------------------------------------------------------------- structure
def _kind(raw: str) -> str:
    t = raw.strip()
    if not t:
        return "blank"
    if _HEADING.match(raw):
        return "heading"
    if t.startswith("|"):
        return "table"
    if _LIST.match(raw):
        return "list"
    return "text"


@dataclass(frozen=True)
class Block:
    lo: int                                              # row indexes (inclusive)
    hi: int
    kind: str                                            # heading | text | list | table | qa


def blocks_of(rows: list[tuple[int, int, str, str]]) -> list[Block]:
    """Logical blocks. Consecutive lines of the same kind are one block; a text line ending in ':' (or directly followed by a list) stays with its list; a question stays with its
    answer; list continuation lines (indented) stay with their item."""
    kinds = [_kind(r[2]) for r in rows]
    out: list[Block] = []
    i = 0
    n = len(rows)
    while i < n:
        k = kinds[i]
        if k == "blank":
            i += 1
            continue
        j = i
        if k == "heading":
            out.append(Block(i, i, "heading"))
            i += 1
            continue
        while j + 1 < n and kinds[j + 1] == k:
            j += 1
        if k == "list":
            while j + 1 < n and kinds[j + 1] == "text" and rows[j + 1][2].startswith((" ", "\t")):
                j += 1                                                   # an indented continuation of a list item
        out.append(Block(i, j, k))
        i = j + 1
    merged: list[Block] = []
    for b in out:
        prev = merged[-1] if merged else None
        if prev and b.kind == "list" and prev.kind == "text" and b.lo == prev.hi + 1:
            merged[-1] = Block(prev.lo, b.hi, "list")                    # lead-in sentence + its list
        elif prev and prev.kind in ("text", "qa") and b.kind == "text" and _ANSWER.match(rows[b.lo][2]) and _QUESTION.match(rows[prev.lo][2]) and b.lo - prev.hi <= 2:
            merged[-1] = Block(prev.lo, b.hi, "qa")                      # a question with its answer (a blank line between them is allowed)
        else:
            merged.append(b)
    return merged


def _heading_level(raw: str) -> int:
    m = _HEADING.match(raw)
    return len(m.group(1)) if m else 0


def _size(rows: list[tuple[int, int, str, str]], lo: int, hi: int) -> int:
    return sum(len(rows[i][2]) + 1 for i in range(lo, hi + 1))


def _trim(rows, lo: int, hi: int, keep: set[int]) -> tuple[int, int]:
    """A window starts and ends on text (the model's answer is stripped of blank lines, so its lines line up), but never drops a candidate line."""
    while lo < hi and not rows[lo][2].strip() and lo not in keep:
        lo += 1
    while hi > lo and not rows[hi][2].strip() and hi not in keep:
        hi -= 1
    return lo, hi


def _section_bounds(rows, i: int) -> tuple[int, int, int, int]:
    """(section lo, section hi, subtree hi, heading level) for row i: the section runs from its nearest heading to the next heading of any level; the subtree to the next heading
    of the same or a higher level."""
    h = i
    while h >= 0 and not _HEADING.match(rows[h][2]):
        h -= 1
    if h < 0:
        nxt = next((j for j in range(i + 1, len(rows)) if _HEADING.match(rows[j][2])), len(rows))
        return 0, nxt - 1, len(rows) - 1, 0                                  # text before the first heading: it stops at the first heading
    level = _heading_level(rows[h][2])
    sec_hi = next((j - 1 for j in range(h + 1, len(rows)) if _HEADING.match(rows[j][2])), len(rows) - 1)
    sub_hi = next((j - 1 for j in range(h + 1, len(rows)) if _HEADING.match(rows[j][2]) and _heading_level(rows[j][2]) <= level), len(rows) - 1)
    if i == h:                                                              # the heading itself: its section
        pass
    return h, sec_hi, sub_hi, level


def _table_header(rows, i: int) -> tuple[int, str]:
    """(row index of the table start, header text) for a table row i."""
    t = i
    while t > 0 and rows[t - 1][2].strip().startswith("|"):
        t -= 1
    head = [rows[t][2]]
    if t + 1 < len(rows) and _SEP.match(rows[t + 1][2]):
        head.append(rows[t + 1][2])
    return t, "\n".join(head)


# ---------------------------------------------------------------------------------------------------------------------------------- sections
def sections_for(content: str, starts: list[int], budget: Budget) -> list[Section]:
    """The sections to send for the candidate lines `starts` (absolute offsets of their first character), non-overlapping, in document order."""
    rows = with_headings(content)
    if not rows:
        return []
    idx = {a: i for i, (a, _, _, _) in enumerate(rows)}
    marks = sorted({idx[s] for s in starts if s in idx})
    if not marks:
        return []
    limit = budget.section_chars
    if len(content) <= limit:                                                   # 1. the whole document
        lo, hi = _trim(rows, 0, len(rows) - 1, set(marks))
        return [_make(rows, lo, hi, "document", set(marks))]
    blocks = blocks_of(rows)
    ranges: list[tuple[int, int, str, bool, str]] = []                          # (lo, hi, kind, partial, header)
    for i in marks:
        lo, hi, sub_hi, _ = _section_bounds(rows, i)
        if _size(rows, lo, sub_hi) <= limit:
            ranges.append((lo, sub_hi, "subtree", False, ""))                   # 2.
        elif _size(rows, lo, hi) <= limit:
            ranges.append((lo, hi, "section", False, ""))                       # 3.
        else:
            a, b, header = _blocks_window(rows, blocks, i, lo, hi, limit)       # 4.
            ranges.append((a, b, "blocks", True, header))
    return _merge(rows, ranges, marks, limit)


def _blocks_window(rows, blocks: list[Block], i: int, lo: int, hi: int, limit: int) -> tuple[int, int, str]:
    """Whole blocks around row i inside the section [lo, hi], widened alternately (earlier first) while they fit. A table row is read with its header: the header rows are
    inside the window when they fit, otherwise they are returned as read-only context text."""
    inside = [b for b in blocks if b.lo >= lo and b.hi <= hi]
    cur = next((k for k, b in enumerate(inside) if b.lo <= i <= b.hi), None)
    if cur is None:
        return i, i, ""
    a, b = inside[cur].lo, inside[cur].hi
    if _size(rows, a, b) > limit:                                                # one block alone is too large (a long table or paragraph): cut it around the line
        a, b = _cut_around(rows, a, b, i, limit)
    else:
        left, right = cur - 1, cur + 1
        prefer_left = True
        while True:
            options = []
            if left >= 0 and _size(rows, inside[left].lo, b) <= limit:
                options.append(0)
            if right < len(inside) and _size(rows, a, inside[right].hi) <= limit:
                options.append(1)
            if not options:
                break
            side = options[0] if prefer_left or len(options) == 1 else options[-1]
            if side == 0 or (len(options) == 2 and not prefer_left and False):
                a, left = inside[left].lo, left - 1
            else:
                b, right = inside[right].hi, right + 1
            prefer_left = not prefer_left
        if a > lo and _HEADING.match(rows[lo][2]) and a - lo <= 2 and _size(rows, lo, b) <= limit:
            a = lo                                                                # the section's own heading when only a blank line separates it from the window
    header = ""
    if _kind(rows[i][2]) == "table":
        t, head = _table_header(rows, i)
        if t < a:
            header = head
    return a, b, header


def _cut_around(rows, a: int, b: int, i: int, limit: int) -> tuple[int, int]:
    """Rows of a long block around row i, as many as fit (half before, half after)."""
    spent = len(rows[i][2]) + 1
    lo = hi = i
    while True:
        moved = False
        if lo > a and spent + len(rows[lo - 1][2]) + 1 <= limit:
            lo -= 1
            spent += len(rows[lo][2]) + 1
            moved = True
        if hi < b and spent + len(rows[hi + 1][2]) + 1 <= limit:
            hi += 1
            spent += len(rows[hi][2]) + 1
            moved = True
        if not moved:
            return lo, hi


def _make(rows, lo: int, hi: int, kind: str, marks: set[int], partial: bool = False, header: str = "") -> Section:
    lo, hi = _trim(rows, lo, hi, marks)
    return Section([Row(a, b, raw) for a, b, raw, _ in rows[lo:hi + 1]], rows[lo][3], kind, {rows[m][0] for m in marks if lo <= m <= hi}, partial, header,
                   headings=[h for _, _, _, h in rows[lo:hi + 1]])


def _merge(rows, ranges, marks, limit) -> list[Section]:
    """Windows of the same logical section (overlapping or touching) are one request when together they still fit (a little slack); windows that overlap but would be too large
    together are cut so that no text is ever sent twice (two requests must not edit the same lines)."""
    ranges = sorted(ranges, key=lambda r: (r[0], r[1]))
    merged: list[list] = []
    for lo, hi, kind, partial, header in ranges:
        if merged and lo <= merged[-1][1] + 1:
            m = merged[-1]
            if _size(rows, m[0], max(m[1], hi)) <= limit * 1.15:
                m[1] = max(m[1], hi)
                m[2] = m[2] if kind == m[2] else ("blocks" if "blocks" in (kind, m[2]) else "section")
                m[3] = m[3] or partial
                m[4] = m[4] or header
                continue
            if lo <= m[1]:
                lo, partial, kind = m[1] + 1, True, "blocks"
                if lo > hi:
                    continue
        merged.append([lo, hi, kind, partial, header])
    out: list[Section] = []
    for lo, hi, kind, partial, header in merged:
        mk = {m for m in marks if lo <= m <= hi}
        if mk:
            out.append(_make(rows, lo, hi, kind, mk, partial, header))
    return out


def split_notes(sections: list[Section], budget: Budget) -> list[str]:
    return [f"“{s.heading or 'top of the document'}” was too large for one request and was read in parts ({budget.describe()})" for s in sections if s.partial]
