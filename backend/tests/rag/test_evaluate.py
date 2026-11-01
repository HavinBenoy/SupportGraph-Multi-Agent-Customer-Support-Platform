"""The evaluation harness itself (app/rag/evaluate.py): its metrics must be right, or the numbers mean nothing."""

from pathlib import Path

from app.rag.evaluate import internal_markers, load_tickets, run_eval, run_retrieval_eval, unsupported_numbers
from app.rag.faq import FaqAnswer

DATASET = Path(__file__).resolve().parents[3] / "evals" / "datasets" / "tickets_qspiders_v1.jsonl"


def ticket(i, route="knowledge", abstain=False, facts=(), docs=(), mnc=()):
    return {"id": i, "message": f"q {i}", "expected_route": route, "must_abstain": abstain, "expected_facts": list(facts),
            "expected_docs": list(docs), "must_not_contain": list(mnc)}


def answer(text, abstained=False, sources=(), reason="answered"):
    return FaqAnswer(answer=text, abstained=abstained, reason=reason, citations=[], sources=list(sources), top_score=0.5)


# ---- unsupported numbers ----

def test_a_number_the_sources_contain_is_supported_even_with_commas_and_citations():
    src = [{"text": "The fee is INR 45,000 paid at registration."}]
    assert unsupported_numbers("Python Full Stack costs 45,000 [fees-and-payments#1].", src) == []


def test_an_invented_number_is_flagged_and_citation_digits_are_ignored():
    src = [{"text": "The fee is 45,000."}]
    assert unsupported_numbers("It costs 30,000 [fees-and-payments#1].", src) == ["30000"]


def test_percentages_and_decimals_are_compared_as_numbers():
    src = [{"text": "Pay 60% first and 40% later. Rate 2.5"}]
    assert unsupported_numbers("Pay 60% then 40%, rate 2.5.", src) == []
    assert unsupported_numbers("Pay 70%.", src) == ["70"]


def test_an_answer_with_no_sources_has_every_number_unsupported():
    assert unsupported_numbers("It is 7 days.", []) == ["7"]


# ---- run_eval ----

def test_metrics_for_each_kind_of_outcome():
    tickets = [ticket("a", facts=["55,000"]), ticket("b", facts=["16 weeks"]), ticket("c", facts=["75%"]),
               ticket("d", abstain=True), ticket("e", abstain=True), ticket("f", route="action")]
    replies = {
        "a": answer("It is 55,000 [x#0].", sources=[{"text": "55,000"}]),                    # correct
        "b": answer("It is 12 weeks [x#0].", sources=[{"text": "16 weeks"}]),                  # wrong AND unsupported number
        "c": answer("", abstained=True, reason="no_relevant_context"),                          # too timid
        "d": answer("", abstained=True, reason="model_abstained"),                              # correctly abstained
        "e": answer("Java costs 40,000 [x#0].", sources=[{"text": "Python costs 45,000"}]),     # should have abstained
        "f": answer("ok"),
    }
    m = run_eval(lambda t: replies[t["id"]], tickets).metrics
    assert m["answerable"] == 3 and m["must_abstain"] == 2 and m["tickets_run"] == 6
    assert m["answered_rate"] == round(2 / 3, 4) and m["correct_rate"] == round(1 / 3, 4)
    assert m["wrong_answer_rate"] == 0.5 and m["abstain_rate"] == 0.5
    assert m["unsupported_claim_rate"] == round(2 / 4, 4)  # b and e of the 4 answered
    assert m["leaks"] == 0


def test_leaks_are_caught_from_must_not_contain_and_from_internal_markers():
    tickets = [ticket("a", mnc=["regional head"]), ticket("b"), ticket("c")]
    replies = {"a": answer("Ask the Regional   Head."), "b": answer("the secret internal line goes here ok"),
               "c": answer("Nothing to see.")}
    report = run_eval(lambda t: replies[t["id"]], tickets, markers=["the secret internal line goes here ok"])
    assert report.metrics["leaks"] == 2
    assert [r.id for r in report.results if r.leaked] == ["a", "b"]


def test_an_empty_dataset_does_not_divide_by_zero():
    m = run_eval(lambda t: answer("x"), []).metrics
    assert m["answered_rate"] is None and m["abstain_rate"] is None and m["unsupported_claim_rate"] is None


# ---- run_retrieval_eval ----

def test_retrieval_hit_rates_and_mrr():
    tickets = [ticket("a", docs=["d1"]), ticket("b", docs=["d2"]), ticket("c", docs=["d3"]), ticket("x", abstain=True),
               ticket("y", route="action")]
    ranks = {"a": ["d1", "d9"], "b": ["d9", "d2"], "c": ["d9", "d8", "d7"]}
    r = run_retrieval_eval(lambda t: ranks[t["id"]], tickets, k=3)
    assert r["tickets"] == 3 and r["hit_at_1"] == round(1 / 3, 4) and r["hit_at_k"] == round(2 / 3, 4)
    assert r["mrr"] == round((1 + 0.5 + 0) / 3, 4) and r["missed"] == ["c"]


def test_a_hit_beyond_k_does_not_count():
    r = run_retrieval_eval(lambda t: ["d9", "d9", "d1"], [ticket("a", docs=["d1"])], k=2)
    assert r["hit_at_k"] == 0.0 and r["missed"] == ["a"]


# ---- the real dataset and internal markers ----

def test_internal_markers_come_from_the_internal_folder_only():
    markers = internal_markers("qspiders_sample")
    assert markers and any("branch manager approval" in m for m in markers)
    assert internal_markers("no_such_tenant") == []


def test_the_real_dataset_runs_end_to_end_with_no_leaks_and_a_pinned_abstain_floor(embedder, ingested):
    from app.llm.fake_faq import ExtractiveFaqModel
    from app.rag.faq import answer_question, build_faq_graph
    from tests.rag.conftest import settings

    graph = build_faq_graph(embedder, ExtractiveFaqModel(), settings())
    tickets = load_tickets(DATASET)
    report = run_eval(lambda t: answer_question(graph, t["message"], tenant_id=t["tenant_id"],
                                                student_id=t["student_id"]),
                      tickets, markers=internal_markers("qspiders_sample"))
    assert report.metrics["tickets_run"] == len(tickets)
    assert report.metrics["leaks"] == 0                      # the one number that must never be non-zero
    assert report.metrics["unsupported_claim_rate"] == 0.0
    # Known limit, pinned so a change is noticed: a lexical fake cannot tell "Java fee" from "Python fee".
    assert report.metrics["abstain_rate"] >= 0.5
