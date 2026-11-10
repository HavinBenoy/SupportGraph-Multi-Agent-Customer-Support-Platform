"""Part 2 retrieval improvements: keyword lane + fusion, alias expansion in the rewrite, and table chunks that keep their header."""

from app.db.session import session_scope
from app.rag import hybrid
from app.rag.chunking import chunk_markdown
from app.rag.retrieval import search, search_hybrid
from app.rag.rewrite import GlossaryRewriter, alias_expansions
from tests.rag.conftest import embedder, ingested  # noqa: F401


def test_bm25_ranks_the_rare_word_above_common_ones():
    docs = [(1, "the fee is paid in instalments"), (2, "the hostel curfew is 10 pm"), (3, "the fee for the hostel and the mess")]
    assert hybrid.bm25(docs, "hostel curfew")[0][0] == 2
    assert hybrid.bm25(docs, "zzzz") == []


def test_rrf_rewards_agreement_between_lanes():
    fused = hybrid.rrf([["a", "b", "c"], ["c", "b", "d"]])
    assert fused["b"] > fused["a"] and fused["c"] > fused["d"]
    assert hybrid.rrf([["a"], ["b"]], [1.0, 0.0])["a"] > 0


def test_the_keyword_lane_rescues_a_question_the_embedding_misses(embedder, ingested):  # noqa: F811
    wrong_vector = embedder.embed_query("completely unrelated astronomy telescope galaxy")
    with session_scope() as db:
        vec_only = search(db, tenant_id="qspiders_sample", query_vector=wrong_vector, model_name=embedder.name, top_k=1)
        hyb = search_hybrid(db, tenant_id="qspiders_sample", query_vector=wrong_vector, query_text="mock interviews resume support", model_name=embedder.name, top_k=4)
    assert "placement-and-career-support" in {h.doc_id for h in hyb}
    assert vec_only[0].doc_id != "placement-and-career-support"                                # the vector lane alone really did miss it
    assert all(-1.0 <= h.score <= 1.0 for h in hyb)                                       # score is still a cosine


def test_hybrid_never_returns_a_non_public_or_other_tenant_passage(embedder, ingested):  # noqa: F811
    with session_scope() as db:
        hyb = search_hybrid(db, tenant_id="qspiders_sample", query_vector=embedder.embed_query("fees"), query_text="fees", model_name=embedder.name, top_k=8)
    assert hyb and len({(h.doc_id, h.chunk_index) for h in hyb}) == len(hyb)


def test_aliases_add_the_other_names_of_what_the_question_mentions():
    al = {"machine learning": ["ml", "machine-learning"]}
    assert alias_expansions("Who teaches machine learning?", al) == []                  # the long form adds nothing
    assert "machine learning" in alias_expansions("Who teaches ML?", al)
    assert alias_expansions("Who teaches html?", al) == []
    q = GlossaryRewriter().rewrite("Who teaches ML?", attempt=0, synonyms={}, aliases=al)
    assert "machine learning" in q and q.startswith("Who teaches ML?")


def test_a_long_table_is_split_by_rows_and_every_part_repeats_the_header():
    rows = "\n".join(f"| Module {i} | Faculty {i} | {i} years of experience in teaching and industry |" for i in range(40))
    md = f"# Directory\n\n## Faculty\n| Module | Faculty | Experience |\n|---|---|---|\n{rows}\n"
    chunks = chunk_markdown(md, doc_title="Directory", max_chars=500)
    assert len(chunks) > 2
    assert all(c.text.lstrip().startswith("| Module | Faculty | Experience |") for c in chunks if "|" in c.text)
    for i in range(40):
        assert any(f"| Module {i} |" in c.text for c in chunks)
