"""Chunking rules (app/rag/chunking.py), including on the real sample articles."""

import pytest

from app.rag.chunking import chunk_markdown
from app.rag.documents import load_public_documents

BODY = """# Title

Intro paragraph that belongs to no section.

## Alpha
First alpha sentence. Second alpha sentence.

### Alpha detail
Detail text here.

## Beta
Beta text.
"""


def test_headings_become_breadcrumbs_and_the_h1_is_not_a_chunk():
    chunks = chunk_markdown(BODY, doc_title="Doc")
    assert [c.heading for c in chunks] == ["Doc", "Doc > Alpha", "Doc > Alpha > Alpha detail", "Doc > Beta"]
    assert [c.index for c in chunks] == [0, 1, 2, 3]
    assert not any("# Title" in c.text for c in chunks)


def test_embedding_input_carries_the_breadcrumb_so_short_passages_keep_their_topic():
    c = chunk_markdown("## Exchanges\nWe do not process direct exchanges.", doc_title="Returns")[0]
    assert c.embedding_input == "Returns > Exchanges\nWe do not process direct exchanges."


def test_h3_resets_when_a_new_h2_starts():
    chunks = chunk_markdown("## A\n### A1\nx\n## B\ny", doc_title="D")
    assert chunks[-1].heading == "D > B"  # not "D > B > A1"


def test_deterministic():
    assert chunk_markdown(BODY, doc_title="Doc") == chunk_markdown(BODY, doc_title="Doc")


def test_empty_and_heading_only_bodies_give_no_chunks():
    assert chunk_markdown("", doc_title="D") == []
    assert chunk_markdown("# Only a title\n\n## Empty section\n", doc_title="D") == []


def test_short_sections_are_not_split():
    assert len(chunk_markdown("## A\n" + "word " * 50, doc_title="D", max_chars=900)) == 1


def test_long_section_is_split_on_sentence_boundaries_within_the_limit():
    sentences = [f"This is sentence number {i} of the long section." for i in range(40)]
    chunks = chunk_markdown("## Long\n" + " ".join(sentences), doc_title="D", max_chars=300)
    assert len(chunks) > 1
    assert all(len(c.text) <= 300 for c in chunks)
    assert " ".join(c.text for c in chunks).split() == " ".join(sentences).split()  # nothing lost or reordered
    assert all(c.text.rstrip().endswith(".") for c in chunks)  # never cut mid-sentence


def test_table_rows_are_never_cut_in_half():
    rows = "\n".join(f"| Option {i} | {i} business days | ${i}.99 |" for i in range(30))
    body = "## Options\n| Option | Time | Cost |\n|---|---|---|\n" + rows
    chunks = chunk_markdown(body, doc_title="D", max_chars=300)
    assert len(chunks) > 1
    for c in chunks:
        assert all(line.startswith("|") and line.endswith("|") for line in c.text.splitlines())


def test_bullet_items_are_never_cut_in_half():
    items = "\n".join(f"- Item number {i} which is not returnable" for i in range(40))
    chunks = chunk_markdown("## What cannot be returned\n" + items, doc_title="D", max_chars=250)
    assert len(chunks) > 1
    assert all(line.startswith("- Item number") for c in chunks for line in c.text.splitlines())


def test_rejects_a_limit_too_small_to_hold_a_passage():
    with pytest.raises(ValueError):
        chunk_markdown("## A\nx", doc_title="D", max_chars=50)


def test_real_articles_chunk_into_topic_sized_passages_with_their_facts_intact():
    by_doc = {d.doc_id: chunk_markdown(d.body, doc_title=d.title) for d in load_public_documents("qspiders_sample")}
    assert all(chunks for chunks in by_doc.values())
    assert all(c.text.strip() and len(c.text) <= 900 for chunks in by_doc.values() for c in chunks)
    refund = [c for c in by_doc["fees-and-payments"] if "7 days of the first class" in c.text]
    assert refund and refund[0].heading == "Fees and Payments (SAMPLE) > Fee refunds"
    table = [c for c in by_doc["fees-and-payments"] if "55,000" in c.text]
    assert table and "| Software Testing | 35,000 |" in table[0].text  # the fee table survived with its rows intact
    assert table[0].text.lstrip().startswith("|") or "Programme fees" in table[0].heading
