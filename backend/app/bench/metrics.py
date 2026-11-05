"""Scoring rules for the benchmark, kept separate from the code that calls models so they can be tested exactly.

FAQ rows get ONE label (the taxonomy) and one failure class:

  label                  meaning                                                              failure class
  ok                     passes the (unchanged) criteria: answered, cited, known ids, facts    -
  truncated              provider stopped it at the token limit (done_reason == "length")      generation
  answered_unanswerable  a must-abstain question was answered instead of refused               incorrect
  retrieval_gap          ANSWERABLE question whose required facts were never in the context    retrieval
  over_abstain           context had the facts; model refused (sentinel only)                  generation
  mixed_output           context had the facts; answer AND sentinel in one reply               generation
  bad_citation           cited an id that was not in the context                               citation
  no_citation            answered without any citation                                         citation
  missing_fact           answered and cited, but required facts are absent from the answer     incorrect
  other                  not correct for a reason not listed (should not happen)               incorrect

Order of the checks matters and is the order above. A correct refusal of a must-abstain question is `ok`, never a retrieval gap.
The pass criteria are the ones that existed before; the only addition is that a truncated reply never passes.
"""

from app.agents.schemas import TriageResult, low_confidence_triage
from app.bench.stats import rate, wilson
from app.config import Settings
from app.llm.fake_faq import SENTINEL
from app.rag.evaluate import _norm
from app.rag.prompts import CITATION

FAILURE_CLASS = {
    "ok": None, "truncated": "generation", "over_abstain": "generation", "mixed_output": "generation",
    "retrieval_gap": "retrieval", "bad_citation": "citation", "no_citation": "citation",
    "missing_fact": "incorrect", "answered_unanswerable": "incorrect", "other": "incorrect",
}
LABELS = list(FAILURE_CLASS)


def judge_faq_answer(case: dict, text: str, context_ids: set[str], done_reason: str | None = None) -> dict:
    """The original deterministic checks (no LLM judge) plus the documented truncation rule."""
    cited = list(dict.fromkeys(CITATION.findall(text)))
    abstained = (not text.strip()) or SENTINEL in text
    norm = _norm(text)
    truncated = done_reason == "length"
    r = {"abstained": abstained, "cited": bool(cited), "citations": cited,
         "unknown_citation": any(c not in context_ids for c in cited), "truncated": truncated}
    if case["must_abstain"]:
        r["correct"] = abstained and not truncated
    else:
        missing = [f for f in case["expected_facts"] if _norm(f) not in norm]
        r["facts_missing"] = missing
        r["correct"] = (not abstained) and bool(cited) and not r["unknown_citation"] and not missing and not truncated
    return r


def label_faq_row(case: dict, judged: dict, context_sufficient: bool | None) -> str:
    if judged["truncated"]:
        return "truncated"
    if case["must_abstain"]:
        return "ok" if judged["correct"] else "answered_unanswerable"
    if judged["correct"]:
        return "ok"
    if context_sufficient is False:
        return "retrieval_gap"
    if judged["abstained"]:
        return "mixed_output" if judged["cited"] else "over_abstain"
    if judged["unknown_citation"]:
        return "bad_citation"
    if not judged["cited"]:
        return "no_citation"
    if judged.get("facts_missing"):
        return "missing_fact"
    return "other"


def summarise_faq(rows: list[dict], speed_fn) -> dict:
    """One split's FAQ numbers. `rows` may contain error rows ({"error": ...}); `speed_fn(rows)` builds the speed block."""
    ok = [r for r in rows if "error" not in r]
    ans = [r for r in ok if not r["must_abstain"]]
    abst = [r for r in ok if r["must_abstain"]]
    suff = [r for r in ans if r["context_sufficient"]]
    gap = [r for r in ans if r["context_sufficient"] is False]
    labels = {k: sum(r["label"] == k for r in ok) for k in LABELS}
    classes: dict[str, int] = {}
    for r in ok:
        c = FAILURE_CLASS[r["label"]]
        if c:
            classes[c] = classes.get(c, 0) + 1
    return {
        "cases": len(rows), "errors": len(rows) - len(ok),
        "answerable": {
            "n": len(ans), "context_sufficient_n": len(suff), "retrieval_gap_n": len(gap),
            # criteria unchanged (plus the truncation rule); denominator = all answerable, as before
            "correct_rate": rate(sum(r["correct"] for r in ans), len(ans)),
            "correct_ci95": wilson(sum(r["correct"] for r in ans), len(ans)),  # at n~25-30 per split, differences under ~15 points are noise
            # same criteria, only cases where the context held the facts
            "correct_rate_context_sufficient": rate(sum(r["correct"] for r in suff), len(suff)),
            # as originally defined: any abstention / all answerable (includes abstentions that were right given the context)
            "over_abstain_rate_raw": rate(sum(r["abstained"] for r in ans), len(ans)),
            # corrected: refusals where the context DID hold the answer / context-sufficient cases
            "over_abstain_rate_corrected": rate(sum(r["label"] in ("over_abstain", "mixed_output") for r in suff), len(suff)),
            "unknown_citation_rate": rate(sum(r["unknown_citation"] for r in ans), len(ans)),
            "truncated_rate": rate(sum(r["truncated"] for r in ans), len(ans)),
        },
        "must_abstain": {"n": len(abst), "correct_rate": rate(sum(r["correct"] for r in abst), len(abst)),
                         "answered_unanswerable": sum(r["label"] == "answered_unanswerable" for r in abst),
                         "truncated": sum(r["truncated"] for r in abst)},
        "labels": labels, "failure_classes": classes,
        "speed": speed_fn(ok),
    }


# ---------- triage: routes come from the REAL route node, not a re-implementation ----------

_RECORD_TOPICS = {"attendance", "assessment", "schedule"}
_MAKEUP = ("make-up", "makeup", "make up", "catch-up", "catch up")


def expected_wants_own_records(t: dict) -> bool | None:
    """What `wants_own_records` should be, derived from existing dataset labels (no dataset edit). None = not scored.

    True  : the ticket's expected route is `action` (signed-in student asking for their own record), or an anonymous
            own-records request tagged `needs_sign_in` (those used to be scored as wrong when the model got them right).
    False : a record-topic question answered from policy documents ("what attendance do I need").
    None  : everything else, including vague signed-in requests where the route depends on confidence, make-up requests
            (a write, not a record lookup) and non-record topics.
    """
    topic = t.get("category")
    if t["expected_route"] == "action":
        return True
    if "needs_sign_in" in t.get("tags", []) and topic in _RECORD_TOPICS and not any(m in t["message"].lower() for m in _MAKEUP):
        return True
    if t["expected_route"] == "knowledge" and topic in _RECORD_TOPICS:
        return False
    return None


class _NoCall:
    def invoke(self, *a, **k):  # pragma: no cover - a route-only graph must never reach a branch
        raise AssertionError("route-only graph reached a branch node")


def get_route_fn(settings: Settings):
    """Return the support graph's actual `route` node function (the closure inside build_support_graph).

    Calling it runs decide_route + the model-independent escalation floor exactly as production does, with
    SG_TRIAGE_MIN_CONFIDENCE taken from `settings`. It touches no database and no model. If a future LangGraph or
    refactor hides the node, this raises instead of silently falling back to a copy of the logic."""
    from app.graph.support import build_support_graph

    fake = settings.model_copy(update={"llm_provider": "fake", "embedding_provider": "fake"})
    graph = build_support_graph(settings=fake, triager=object(), knowledge=_NoCall(), action=_NoCall())
    try:
        fn = graph.nodes["route"].bound.func
    except (AttributeError, KeyError) as e:
        raise RuntimeError("Cannot reach the support graph's route node (LangGraph internals or node name changed). "
                           "Refusing to approximate routing with a copy of the logic.") from e
    return fn


def real_route(route_fn, ticket: dict, triage: TriageResult, tenant_id: str) -> dict:
    from langchain_core.messages import HumanMessage

    state = {"messages": [HumanMessage(ticket["message"])], "triage": triage.model_dump()}
    config = {"configurable": {"tenant_id": tenant_id, "student_id": ticket["student_id"]}}
    out = route_fn(state, config)
    return {"route": out["route"], "route_reason": out["route_reason"]}


def triage_row(ticket: dict, split: str, triage: TriageResult, route: dict, expected_category: str | None, seconds: float) -> dict:
    want_own = expected_wants_own_records(ticket)
    return {
        "id": ticket["id"], "split": split, "message": ticket["message"], "signed_in": ticket["student_id"] != "anonymous",
        "expected_category": expected_category, "category": triage.category,
        "confidence": triage.confidence, "wants_own_records": triage.wants_own_records, "priority": triage.priority,
        "unusable": triage == low_confidence_triage(),  # provider error / invalid JSON / schema violation all land here
        "expected_route": ticket["expected_route"], "route": route["route"], "route_reason": route["route_reason"],
        "route_ok": route["route"] == ticket["expected_route"],
        "expected_wants_own_records": want_own,
        "own_records_ok": None if want_own is None else triage.wants_own_records == want_own,
        "total_s": seconds,
    }


def summarise_triage(rows: list[dict], min_confidence: float, latency_fn, conf_fn) -> dict:
    n = len(rows)
    esc = [r for r in rows if r["expected_route"] == "escalate"]
    non = [r for r in rows if r["expected_route"] != "escalate"]
    missed = [r for r in esc if r["route"] != "escalate"]
    false = [r for r in non if r["route"] == "escalate"]
    cat = [r for r in rows if r["expected_category"]]
    own = [r for r in rows if r["own_records_ok"] is not None]
    by_exp: dict[str, dict] = {}
    confusion: dict[str, dict[str, int]] = {}
    for r in rows:
        e = by_exp.setdefault(r["expected_route"], {"n": 0, "correct": 0})
        e["n"] += 1
        e["correct"] += r["route_ok"]
        confusion.setdefault(r["expected_route"], {}).setdefault(r["route"], 0)
        confusion[r["expected_route"]][r["route"]] += 1
    for e in by_exp.values():
        e["rate"] = rate(e["correct"], e["n"])
    return {
        "cases": n, "min_confidence_used": min_confidence,
        "route_accuracy": rate(sum(r["route_ok"] for r in rows), n), "route_accuracy_by_expected": by_exp, "route_confusion": confusion,
        "escalation": {"expected_n": len(esc), "missed_n": len(missed), "missed_rate": rate(len(missed), len(esc)),
                       "missed_ids": [r["id"] for r in missed],
                       "non_escalation_n": len(non), "false_n": len(false), "false_rate": rate(len(false), len(non)),
                       "false_ids": [r["id"] for r in false]},
        "category": {"scored_n": len(cat), "accuracy": rate(sum(r["category"] == r["expected_category"] for r in cat), len(cat))},
        "own_records": {"scored_n": len(own), "accuracy": rate(sum(r["own_records_ok"] for r in own), len(own)),
                        "wrong_ids": [r["id"] for r in own if not r["own_records_ok"]]},
        "unusable_output_rate": rate(sum(r["unusable"] for r in rows), n),
        "confidence": conf_fn([r["confidence"] for r in rows if not r["unusable"]]),
        "below_min_confidence_rate": rate(sum(r["confidence"] < min_confidence for r in rows if not r["unusable"]),
                                          sum(not r["unusable"] for r in rows)),
        "latency_s": latency_fn([r["total_s"] for r in rows]),
    }
