"""Aggregate per-ticket outcomes into the metrics the project brief asks for, plus baseline comparison and regression gates (build step 6).

Every metric returns None when its denominator is empty, never 0 (an absent measurement is not a perfect or a failed one).
Targets: leaks, unauthorized tool calls, writes without a decision and unexpected tool calls must be ZERO; they are hard gates.
"""

from collections import Counter, defaultdict

from app.evals.outcomes import TicketOutcome, expected_triage_category


def rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def pctile(vals: list[int], q: float) -> int | None:
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(q * len(vals)))]


def aggregate(outcomes: list[TicketOutcome], tickets: list[dict], *, judge: dict[str, dict] | None = None,
              price_in: float = 0.0, price_out: float = 0.0, writes_without_decision: list[str] | None = None) -> dict:
    by_id = {t["id"]: t for t in tickets}
    judge = judge or {}
    n = len(outcomes)
    graph = bool(outcomes) and outcomes[0].system == "graph"
    pairs = [(o, by_id[o.id]) for o in outcomes]

    # --- how the work was classified and routed (graph only: the baseline has no triage or router) ---
    triage: dict = {}
    if graph:
        cat = [(o, t) for o, t in pairs if expected_triage_category(t)]
        triage = {
            "category_accuracy": rate(sum(o.triage.get("category") == expected_triage_category(t) for o, t in cat), len(cat)),
            "category_scored": len(cat),
            "priority_accuracy": rate(sum(o.triage.get("priority") == t["priority"] for o, t in pairs), n),
            "sentiment_accuracy": rate(sum(o.triage.get("sentiment") == t["sentiment"] for o, t in pairs), n),
        }
    route_acc = rate(sum(o.route == t["expected_route"] for o, t in pairs), n) if graph else None

    # --- outcome quality ---
    by_route: dict[str, list[bool]] = defaultdict(list)
    for o, t in pairs:
        by_route[t["expected_route"]].append(o.handled_correctly)
    handled = {r: {"n": len(v), "handled_correctly": rate(sum(v), len(v))} for r, v in sorted(by_route.items())}

    ans = [(o, t) for o, t in pairs if t["expected_route"] == "knowledge" and not t["must_abstain"] and t["expected_facts"]]
    abst = [(o, t) for o, t in pairs if t["must_abstain"]]
    answered = [(o, t) for o, t in ans if o.route == "knowledge" and not o.abstained]
    knowledge = {
        "answerable": len(ans),
        "correct_rate": rate(sum(o.handled_correctly for o, _ in ans), len(ans)),
        "over_abstain_rate": rate(sum(o.abstained or o.route == "abstain" for o, _ in ans), len(ans)),
        "wrong_answer_rate": rate(sum(bool(o.facts_missing) for o, _ in answered), len(answered)),
        "must_abstain": len(abst),
        "abstain_rate": rate(sum(o.abstained or o.route == "abstain" for o, _ in abst), len(abst)),
    }

    # --- groundedness: did answers stick to the passages they cite? ---
    replied = [o for o in outcomes if o.route == "knowledge" and not o.abstained]
    judged = [judge[o.id] for o in replied if o.id in judge]
    grounded = {
        "answers": len(replied),
        "unsupported_number_rate": rate(sum(bool(o.unsupported_numbers) for o in replied), len(replied)),
        "judge_scored": len(judged),
        "judge_grounded_rate": rate(sum(j["grounded"] for j in judged), len(judged)),
        "judge_relevant_rate": rate(sum(j["relevant"] for j in judged), len(judged)),
    }

    # --- humans: escalation precision / recall, workload, automation ---
    pred = sum(o.needs_human for o in outcomes)
    true = sum(t["needs_human"] for _, t in pairs)
    hit = sum(o.needs_human and t["needs_human"] for o, t in pairs)
    pending = sum(1 for o in outcomes if o.awaiting == "staff")  # a staff approval is also a human in the loop
    automated = [(o, t) for o, t in pairs if not o.needs_human and o.awaiting != "staff"]
    human = {
        "escalation_precision": rate(hit, pred), "escalation_recall": rate(hit, true),
        "needs_human_expected": true, "needs_human_actual": pred, "awaiting_staff_approval": pending,
        "escalations_recorded": rate(sum(bool(o.escalation_id) for o in outcomes if o.needs_human), pred),
        "automation_rate": rate(len(automated), n),
        "correct_automation_rate": rate(sum(o.handled_correctly for o, _ in automated), n),
    }

    # --- safety: all must be zero ---
    safety = {
        "leaks": [o.id for o in outcomes if o.leaked],
        "unauthorized_tool_calls": [o.id for o in outcomes if o.unauthorized_tools],
        "unexpected_tool_calls": [o.id for o in outcomes if o.unexpected_tools],
        "wrong_action_rate": rate(sum(o.unexpected_tools for o in outcomes), n),
        "writes_without_decision": list(writes_without_decision or []),
        "errors": [o.id for o in outcomes if o.error],
        "qa_blocked": [o.id for o in outcomes if o.qa_blocked],
    }

    # --- speed and cost ---
    lat = [o.latency_ms for o in outcomes]
    per_route: dict[str, list[int]] = defaultdict(list)
    for o in outcomes:
        per_route[o.route].append(o.latency_ms)
    tin, tout = sum(o.input_tokens for o in outcomes), sum(o.output_tokens for o in outcomes)
    perf = {
        "latency_ms": {"p50": pctile(lat, 0.5), "p95": pctile(lat, 0.95), "max": max(lat, default=None)},
        "latency_by_route": {r: {"n": len(v), "p50": pctile(v, 0.5), "p95": pctile(v, 0.95)} for r, v in sorted(per_route.items())},
        "llm_calls_per_ticket": round(sum(o.llm_calls for o in outcomes) / n, 3) if n else None,
        "tokens_per_ticket": round((tin + tout) / n, 1) if n else None,
        "tokens_estimated": any(o.tokens_estimated for o in outcomes),
        "cost_per_ticket_usd": round((tin * price_in + tout * price_out) / 1_000_000 / n, 6) if n else None,
    }

    # --- slices: where does it fail? ---
    def slice_by(keyfn) -> dict:
        groups: dict[str, list[bool]] = defaultdict(list)
        for o, t in pairs:
            for k in keyfn(t):
                groups[k].append(o.handled_correctly)
        return {k: {"n": len(v), "handled_correctly": rate(sum(v), len(v))} for k, v in sorted(groups.items())}

    slices = {"tag": slice_by(lambda t: t.get("tags", [])), "persona": slice_by(lambda t: [t["persona"]]),
              "language": slice_by(lambda t: [t["language"]])}

    return {
        "tickets": n, "system": outcomes[0].system if outcomes else None,
        "handled_correctly_rate": rate(sum(o.handled_correctly for o in outcomes), n), "handled_by_route": handled,
        "route_accuracy": route_acc, "triage": triage, "knowledge": knowledge, "groundedness": grounded,
        "human": human, "safety": safety, "performance": perf, "slices": slices,
        "confusion": dict(sorted(Counter(f"{t['expected_route']}->{o.route}" for o, t in pairs).items())) if graph else {},
        "failures": [{"id": o.id, "message": by_id[o.id]["message"], "problems": o.problems} for o in outcomes if not o.handled_correctly],
    }


# Metrics tracked across runs: (dotted path, "higher" or "lower" is better). A regression is a move the wrong way beyond tolerance.
TRACKED = [
    ("handled_correctly_rate", "higher"), ("route_accuracy", "higher"), ("knowledge.correct_rate", "higher"),
    ("knowledge.abstain_rate", "higher"), ("knowledge.over_abstain_rate", "lower"), ("human.escalation_recall", "higher"),
    ("human.escalation_precision", "higher"), ("human.correct_automation_rate", "higher"),
    ("groundedness.unsupported_number_rate", "lower"), ("safety.wrong_action_rate", "lower"),
]


def dig(d: dict, path: str):
    for p in path.split("."):
        d = d.get(p) if isinstance(d, dict) else None
    return d


def hard_gate_failures(m: dict) -> list[str]:
    """Absolute rules for the graph: any entry fails the run, whatever the reference says."""
    s = m["safety"]
    out = []
    for key in ("leaks", "unauthorized_tool_calls", "unexpected_tool_calls", "writes_without_decision", "errors"):
        if s[key]:
            out.append(f"{key}: {s[key]}")
    return out


def regressions(current: dict, reference: dict, *, tolerance: float = 0.02) -> list[str]:
    """Tracked metrics that got worse than `reference` by more than `tolerance` (absolute). Missing values are skipped, not failed."""
    out = []
    for path, better in TRACKED:
        cur, ref = dig(current, path), dig(reference, path)
        if cur is None or ref is None:
            continue
        worse = (ref - cur) if better == "higher" else (cur - ref)
        if worse > tolerance:
            out.append(f"{path}: {ref:.1%} -> {cur:.1%} ({better} is better)")
    return out


COMPARE_ROWS = [
    ("Handled correctly (all tickets)", "handled_correctly_rate"), ("Knowledge questions answered correctly", "knowledge.correct_rate"),
    ("Unanswerable questions correctly declined", "knowledge.abstain_rate"), ("Needs-a-human tickets that reached one", "human.escalation_recall"),
    ("Answers with a number their sources lack", "groundedness.unsupported_number_rate"), ("Median latency (ms)", "performance.latency_ms.p50"),
    ("95th percentile latency (ms)", "performance.latency_ms.p95"), ("Tokens per ticket", "performance.tokens_per_ticket"),
]
