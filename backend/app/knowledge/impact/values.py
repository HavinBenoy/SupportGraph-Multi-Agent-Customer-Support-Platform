"""Finding and rewriting VALUES (amounts, time ranges, names) inside document text.

The same fact is written many ways: "₹15,000", "Rs. 15000/-", "INR 15,000", "15k"; "10 AM–1 PM", "10:00 am to 1:00 pm". A change described once ("from ₹15,000 to
₹18,000") must find every spelling, and the replacement must keep the style the document already uses. This module is deterministic and has no model in it, so
it is the same safety net for every model: a proposed replacement is checked against it, never trusted blindly."""

import re
from dataclasses import dataclass

_CUR = r"(?:₹|Rs\.?|INR)"
_MONEY_WORDS = re.compile(r"\b(fees?|cost|costs|price|priced|tuition|payable|amount|charges?|rupees?|rs|inr|installments?|instalments?|deposit|total|pay)\b|₹", re.I)
_MONEY = re.compile(rf"(?<![\w.,])(?P<cur>{_CUR}\s*)?(?P<num>\d{{1,3}}(?:,\d{{2,3}})+|\d+)(?P<dec>\.\d+)?(?P<k>\s?[kK](?![A-Za-z]))?(?P<slash>\s?/-)?(?![\d])")
_MER = r"(?:[ap]\.m\.|[ap]m)"                  # "a.m." with both dots, or "am": a lone trailing dot belongs to the sentence, not the time
_TIME = re.compile(rf"(?<![\w:])(?P<h1>\d{{1,2}})(?::(?P<m1>\d{{2}}))?\s?(?P<p1>{_MER})?\s*(?P<sep>-|–|—|\bto\b|\buntil\b)\s*(?P<h2>\d{{1,2}})(?::(?P<m2>\d{{2}}))?\s?(?P<p2>{_MER})(?![\w])", re.I)
_TIME1 = re.compile(rf"(?<![\w:])(?P<h>\d{{1,2}})(?::(?P<m>\d{{2}}))?\s?(?P<p>{_MER})(?![\w])", re.I)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str


def _norm(s: str) -> str:
    return " ".join(str(s).split())


# -------------------------------------------------------------------------------------------------------------------------------- amounts
def _amount(value: str) -> int | None:
    """The whole-rupee amount a value string stands for, or None if it is not an amount."""
    m = _MONEY.search(value or "")
    if not m or (not m.group("cur") and not m.group("k") and not re.fullmatch(r"\s*\d[\d,]*(?:\.\d+)?\s*", value or "")):
        return None
    return _money_of(m)


def _money_of(m: re.Match) -> int:
    n = float(m.group("num").replace(",", "") + (m.group("dec") or ""))
    return int(round(n * 1000)) if m.group("k") else int(round(n))


def _is_time_range(value: str) -> bool:
    return _TIME.fullmatch(_norm(value)) is not None


def _minutes(h: int, m: int | None, p: str | None) -> int:
    h = h % 12 if p else h
    if p and p.lower().replace(".", "").startswith("p"):
        h += 12
    return h * 60 + (m or 0)


def _range_of(m: re.Match) -> tuple[int, int] | None:
    h1, h2 = int(m.group("h1")), int(m.group("h2"))
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23) or int(m.group("m1") or 0) > 59 or int(m.group("m2") or 0) > 59:
        return None
    p2 = m.group("p2")
    p1 = m.group("p1")
    if p1 is None:                                                           # "10-1 PM": the first time borrows a meridiem that keeps the range forward
        end = _minutes(h2, int(m.group("m2") or 0), p2)
        for cand in ("am", "pm"):
            start = _minutes(h1, int(m.group("m1") or 0), cand)
            if start < end:
                return start, end
        return None
    return _minutes(h1, int(m.group("m1") or 0), p1), _minutes(h2, int(m.group("m2") or 0), p2)


def _single_of(m: re.Match) -> int | None:
    h = int(m.group("h"))
    return None if not 1 <= h <= 12 or int(m.group("m") or 0) > 59 else _minutes(h, int(m.group("m") or 0), m.group("p"))


# --------------------------------------------------------------------------------------------------------------------------------- finding
def kind_of(value: str) -> str:
    v = _norm(value)
    if _is_time_range(v):
        return "time_range"
    if _TIME1.fullmatch(v):
        return "time"
    if _amount(v) is not None:
        return "money"
    return "text"


def find_value(text: str, value: str) -> list[Span]:
    """Every occurrence of `value` in `text`, whatever spelling it uses. A bare number is only an amount when its line talks about money."""
    v = _norm(value)
    if not v:
        return []
    out: list[Span] = []
    kind = kind_of(v)
    if kind == "money":
        want = _amount(v)
        marked = bool(_MONEY.search(v) and (_MONEY.search(v).group("cur") or _MONEY.search(v).group("k")))
        for m in _MONEY.finditer(text):
            if _money_of(m) != want:
                continue
            if not (m.group("cur") or m.group("k") or m.group("slash")):
                line = text[text.rfind("\n", 0, m.start()) + 1: (text.find("\n", m.end()) if text.find("\n", m.end()) >= 0 else len(text))]
                if marked and not _MONEY_WORDS.search(line):
                    continue
                if len(m.group("num").replace(",", "")) < 4:               # "15" in a sentence is not an amount
                    continue
            out.append(Span(m.start("cur") if m.group("cur") else m.start("num"), m.end(), m.group(0).strip() if not m.group("cur") else text[m.start("cur"):m.end()]))
    elif kind == "time_range":
        want = _range_of(_TIME.fullmatch(v))
        out = [Span(m.start(), m.end(), m.group(0)) for m in _TIME.finditer(text) if want and _range_of(m) == want]
    elif kind == "time":
        want = _single_of(_TIME1.fullmatch(v))
        taken = [(m.start(), m.end()) for m in _TIME.finditer(text)]
        out = [Span(m.start(), m.end(), m.group(0)) for m in _TIME1.finditer(text)
               if _single_of(m) == want and not any(a <= m.start() < b for a, b in taken)]
    else:
        pat = re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in v.split()) + r"(?!\w)", re.I)
        out = [Span(m.start(), m.end(), m.group(0)) for m in pat.finditer(text)]
    return out


# -------------------------------------------------------------------------------------------------------------------------------- rewriting
def _group(n: int, style: str) -> str:
    s = str(n)
    if style == "plain" or len(s) <= 3:
        return s
    if style == "indian":
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        return ",".join(parts + [tail])
    return f"{n:,}"


def _fmt_time(mins: int, *, colon: bool, upper: bool, space: bool, dots: bool, meridiem: bool) -> str:
    h24, m = divmod(mins % 1440, 60)
    p = "pm" if h24 >= 12 else "am"
    h = h24 % 12 or 12
    core = f"{h}:{m:02d}" if (colon or m) else f"{h}"
    if not meridiem:
        return core
    mer = (p[0] + "." + p[1] + ".") if dots else p
    return core + (" " if space else "") + (mer.upper() if upper else mer)


def render_new(matched: str, old_value: str, new_value: str) -> str | None:
    """`new_value` written in the style of `matched` (the spelling found in the document). None when the new value cannot be written in that kind."""
    new = _norm(new_value)
    kind = kind_of(old_value)
    if kind == "money":
        mm = _MONEY.search(matched)
        nm = _MONEY.search(new)
        if mm is None or nm is None:
            return None
        n = _money_of(nm)
        num = mm.group("num")
        style = "plain" if "," not in num else ("indian" if re.search(r",\d{2},", num) or re.fullmatch(r"\d{1,2},\d{3}", num) is None and re.search(r",\d{2}\b", num) else "western")
        if mm.group("k"):
            body = f"{n / 1000:g}" + (mm.group("k") if n % 1000 == 0 else "")
            if n % 1000:
                body = _group(n, "plain")
        else:
            body = _group(n, style) + (nm.group("dec") or "" if mm.group("dec") else "")
        return (mm.group("cur") or "") + body + (mm.group("slash") or "")
    if kind == "time_range":
        mm = _TIME.fullmatch(_norm(matched))
        nm = _TIME.fullmatch(new)
        if mm is None or nm is None:
            return None
        r = _range_of(nm)
        if r is None:
            return None
        colon = bool(mm.group("m1") or mm.group("m2"))
        raw = mm.group("p2")
        flags = dict(colon=colon, upper=raw.isupper(), space=bool(re.search(r"\d\s" + _MER, matched, re.I)), dots="." in raw)
        sep = re.search(r"\d\s?(?:[ap]\.?m\.?)?(\s*(?:-|–|—|to|until)\s*)\d", matched, re.I)
        return _fmt_time(r[0], meridiem=mm.group("p1") is not None, **flags) + (sep.group(1) if sep else "–") + _fmt_time(r[1], meridiem=True, **flags)
    if kind == "time":
        mm = _TIME1.fullmatch(_norm(matched))
        nm = _TIME1.fullmatch(new)
        if mm is None or nm is None:
            return None
        return _fmt_time(_single_of(nm), colon=bool(mm.group("m")), upper=mm.group("p").isupper(), space=bool(re.search(r"\d\s" + _MER, matched, re.I)),
                         dots="." in mm.group("p"), meridiem=True)
    if matched.isupper() and len(matched) > 3:
        return new.upper()
    return new
