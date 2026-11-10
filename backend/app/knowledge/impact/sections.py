"""A document as addressable UNITS: a paragraph line, a list item or a table row, each with the heading chain above it, the question line above an "A:" answer, and (for a table row)
the column names and the position of every cell. Offsets are absolute positions in the document body, exactly like `passages.Passage`, so a unit can be edited as a precise
patch. A table row is never separated from its header: "| Machine Learning | Jyothi sir |" is understood as Faculty = Jyothi sir because the header says so."""

import re
from dataclasses import dataclass

from app.knowledge.impact.passages import _HEADING, with_headings

_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LIST = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_ANSWER = re.compile(r"^\s*(?:A|Ans|Answer)\s*[:.]\s*", re.I)
_QUESTION = re.compile(r"^\s*(?:Q|Question)\s*[:.]\s*", re.I)


@dataclass(frozen=True)
class Cell:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Unit:
    start: int
    end: int
    text: str
    heading: str
    kind: str                                  # paragraph | list | table_row
    lead: str = ""                             # the question above an answer line ("Q: Who teaches ML?" above "A: Jyothi sir.")
    columns: tuple[str, ...] = ()              # table header cell texts, aligned with `cells`
    cells: tuple[Cell, ...] = ()

    @property
    def context_text(self) -> str:
        return " ".join(x for x in (self.heading, self.lead, " ".join(self.columns)) if x)


def _cells(line: str, base: int) -> tuple[Cell, ...]:
    out, pos = [], 0
    parts = line.split("|")
    for i, raw in enumerate(parts):
        a = pos
        pos += len(raw) + 1
        if (i == 0 and not raw.strip()) or (i == len(parts) - 1 and not raw.strip()):
            continue
        t = raw.strip()
        if t:
            lead = len(raw) - len(raw.lstrip())
            out.append(Cell(base + a + lead, base + a + lead + len(t), t))
    return tuple(out)


def units(content: str) -> list[Unit]:
    rows = with_headings(content)
    out: list[Unit] = []
    columns: tuple[str, ...] = ()
    last_question = ""
    in_table = False
    for i, (a, b, raw, h) in enumerate(rows):
        if not raw.strip() or _HEADING.match(raw):
            in_table, columns = False, ()
            if _HEADING.match(raw):
                last_question = ""
            continue
        if raw.lstrip().startswith("|"):
            nxt = rows[i + 1][2] if i + 1 < len(rows) else ""
            if not in_table and _SEPARATOR.match(nxt or "") is not None and nxt.strip():
                in_table, columns = True, tuple(c.text for c in _cells(raw, a))      # the header row itself carries no value
                continue
            if _SEPARATOR.match(raw):
                continue
            cells = _cells(raw, a)
            out.append(Unit(a, b, raw, h, "table_row", columns=columns if in_table else (), cells=cells))
            continue
        in_table, columns = False, ()
        if _QUESTION.match(raw):
            last_question = _QUESTION.sub("", raw).strip()
        lead = last_question if _ANSWER.match(raw) else ""
        out.append(Unit(a, b, raw, h, "list" if _LIST.match(raw) else "paragraph", lead=lead))
    return out
