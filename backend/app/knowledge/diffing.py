"""Structured text diffs for change requests.

A diff is a list of segments {"op": "equal" | "delete" | "insert", "text": str}. Reading `equal` + `delete` back gives the ORIGINAL text exactly; reading `equal` +
`insert` gives the PROPOSED text exactly (`verify` checks both, and a diff that fails the check is replaced by a coarse but correct one). It is plain data:
no HTML, no styling. The UI decides how to colour it (deleted = red with the text still visible, inserted = green).

Cost control: lines are compared first; only the lines that changed are compared word by word, and only when the changed block is small enough, so a large
document cannot make a diff slow."""

import difflib
import re

_TOKEN = re.compile(r"\s+|[^\s]+")
MAX_TOKENS_PER_BLOCK = 3000


def _merge(segs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in segs:
        if not s["text"]:
            continue
        if out and out[-1]["op"] == s["op"]:
            out[-1]["text"] += s["text"]
        else:
            out.append({"op": s["op"], "text": s["text"]})
    return out


def _word_diff(old: str, new: str) -> list[dict]:
    a, b = _TOKEN.findall(old), _TOKEN.findall(new)
    if len(a) > MAX_TOKENS_PER_BLOCK or len(b) > MAX_TOKENS_PER_BLOCK:
        return [{"op": "delete", "text": old}, {"op": "insert", "text": new}]
    segs: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            segs.append({"op": "equal", "text": "".join(a[i1:i2])})
        else:
            if tag in ("delete", "replace"):
                segs.append({"op": "delete", "text": "".join(a[i1:i2])})
            if tag in ("insert", "replace"):
                segs.append({"op": "insert", "text": "".join(b[j1:j2])})
    return segs


def _coarse(old: str, new: str) -> list[dict]:
    if old == new:
        return _merge([{"op": "equal", "text": old}])
    return _merge([{"op": "delete", "text": old}, {"op": "insert", "text": new}])


def diff_text(old: str, new: str) -> list[dict]:
    if old == new:
        return _merge([{"op": "equal", "text": old}])
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    segs: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            segs.append({"op": "equal", "text": "".join(a[i1:i2])})
        elif tag == "delete":
            segs.append({"op": "delete", "text": "".join(a[i1:i2])})
        elif tag == "insert":
            segs.append({"op": "insert", "text": "".join(b[j1:j2])})
        else:
            segs.extend(_word_diff("".join(a[i1:i2]), "".join(b[j1:j2])))
    segs = _merge(segs)
    return segs if verify(old, new, segs) else _coarse(old, new)


def original_of(segs: list[dict]) -> str:
    return "".join(s["text"] for s in segs if s["op"] in ("equal", "delete"))


def proposed_of(segs: list[dict]) -> str:
    return "".join(s["text"] for s in segs if s["op"] in ("equal", "insert"))


def verify(old: str, new: str, segs: list[dict]) -> bool:
    return original_of(segs) == old and proposed_of(segs) == new


def stats(segs: list[dict]) -> dict:
    return {"added_chars": sum(len(s["text"]) for s in segs if s["op"] == "insert"),
            "deleted_chars": sum(len(s["text"]) for s in segs if s["op"] == "delete"),
            "changed_blocks": sum(1 for s in segs if s["op"] != "equal")}


def build(old_title: str, new_title: str, old_body: str, new_body: str) -> tuple[dict, dict]:
    """(diff, stats) for one document edit."""
    title, body = diff_text(old_title, new_title), diff_text(old_body, new_body)
    st, sb = stats(title), stats(body)
    return ({"title": title, "body": body},
            {"added_chars": st["added_chars"] + sb["added_chars"], "deleted_chars": st["deleted_chars"] + sb["deleted_chars"],
             "changed_blocks": st["changed_blocks"] + sb["changed_blocks"], "title_changed": old_title != new_title})
