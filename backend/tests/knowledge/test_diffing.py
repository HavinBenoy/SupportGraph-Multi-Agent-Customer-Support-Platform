"""Structured diff engine: the stored segments always reconstruct both texts exactly."""

import pytest

from app.knowledge import diffing


def seg_text(segs, ops):
    return "".join(s["text"] for s in segs if s["op"] in ops)


CASES = [("", ""), ("a", "a"), ("a\nb\nc\n", "a\nB\nc\n"), ("one two three\n", "one three\n"), ("x\n", ""), ("", "y\nz\n"), ("héllo wörld\n日本語 text\n", "héllo world\n日本語 test\n"),
         ("a b  c\n\n\nd", "a b c\n\nd"), ("line\r\nother", "line\nother")]


@pytest.mark.parametrize("old,new", CASES)
def test_equal_plus_delete_is_the_original_and_equal_plus_insert_is_the_proposal(old, new):
    segs = diffing.diff_text(old, new)
    assert seg_text(segs, ("equal", "delete")) == old
    assert seg_text(segs, ("equal", "insert")) == new
    assert diffing.verify(old, new, segs)


def test_deleted_text_is_kept_and_marked_not_removed():
    segs = diffing.diff_text("The fee is 50000 rupees.\n", "The fee is 55000 rupees.\n")
    deleted = [s["text"] for s in segs if s["op"] == "delete"]
    inserted = [s["text"] for s in segs if s["op"] == "insert"]
    assert deleted and inserted and any("50000" in d for d in deleted) and any("55000" in i for i in inserted)


def test_unchanged_text_is_all_equal():
    segs = diffing.diff_text("same\ntext\n", "same\ntext\n")
    assert {s["op"] for s in segs} == {"equal"}


def test_build_covers_title_and_body_with_stats():
    diff, st = diffing.build("Fees", "Fee schedule", "A\nB\n", "A\nC\nD\n")
    assert set(diff) == {"title", "body"}
    assert st["added_chars"] > 0 and st["deleted_chars"] > 0
    assert diffing.verify("A\nB\n", "A\nC\nD\n", diff["body"]) and diffing.verify("Fees", "Fee schedule", diff["title"])


def test_large_documents_stay_fast_and_exact():
    old = "".join(f"line {i} of the policy text\n" for i in range(4000))
    new = old.replace("line 2000 of", "line 2000 OF").replace("line 3999 of the policy text\n", "")
    segs = diffing.diff_text(old, new)
    assert diffing.verify(old, new, segs)
    assert len(segs) < 50
