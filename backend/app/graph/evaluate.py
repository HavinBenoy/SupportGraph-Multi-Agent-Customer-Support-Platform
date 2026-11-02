"""Routing and end-to-end evaluation of the support graph on the labelled tickets (build step 3).

Questions answered, each by its own number because they fail separately:
  route_accuracy        share of tickets sent to the expected branch (knowledge/action/clarify/escalate), plus a confusion table
  escalation precision  of the tickets the system sent to a human, how many needed one (low = over-escalation, wasted staff time)
  escalation recall     of the tickets that needed a human, how many reached one (low = a person in trouble got a bot)
  tool_set_match        action tickets where the agent called exactly the expected read tools
  unexpected_tool_calls tickets where a tool ran that the label did not expect, for ANY route (wrong-action rate; target 0)
  unauthorized_tool_calls  tool calls made for someone who is not signed in (target 0, a single one fails the run)
  action_facts_rate     action tickets whose reply contains every expected fact
  leaks                 replies containing must_not_contain text or a line of an internal document (target 0)

Honest limits (see app/llm/fake_triage.py): with the offline fakes these numbers measure how well keyword rules fit this ticket
set, which was in view when they were written. They show the plumbing works end to end; they do not predict a real model.
"""

from dataclasses import dataclass, field

from app.rag.evaluate import _norm, _rate


@dataclass
class RoutedResult:
    """What one run of the support graph produced (plain data, so tests can stub it)."""

    route: str
    route_reason: str = ""
    reply: str = ""
    tools_called: list[str] = field(default_factory=list)
    needs_human: bool = False
    abstained: bool = False
    citations: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


def from_state(state: dict) -> RoutedResult:
    return RoutedResult(route=state["route"], route_reason=state.get("route_reason", ""), reply=state.get("reply", ""),
                        tools_called=state.get("tools_called", []), needs_human=state.get("needs_human", False),
                        abstained=state.get("abstained", False), citations=state.get("citations", []),
                        sources=state.get("sources", []), queries=state.get("queries", []))


def run_routing_eval(run_fn, tickets: list[dict], *, markers: list[str] | None = None) -> dict:
    """`run_fn(ticket) -> RoutedResult`. Returns metrics plus per-ticket problems so failures can be read, not just counted."""
    markers = markers or []
    confusion: dict[str, int] = {}
    right = 0
    predicted_h = true_h = hit_h = 0
    action_total = match = facts_ok = 0
    unexpected: list[str] = []
    unauthorized: list[str] = []
    leaks: list[str] = []
    wrong_route: list[dict] = []
    for t in tickets:
        r = run_fn(t)
        right += r.route == t["expected_route"]
        confusion[f"{t['expected_route']}->{r.route}"] = confusion.get(f"{t['expected_route']}->{r.route}", 0) + 1
        if r.route != t["expected_route"]:
            wrong_route.append({"id": t["id"], "expected": t["expected_route"], "got": r.route, "reason": r.route_reason})
        predicted_h += r.needs_human
        true_h += t["needs_human"]
        hit_h += r.needs_human and t["needs_human"]
        if t["expected_route"] == "action":
            action_total += 1
            match += set(r.tools_called) == set(t["expected_tools"])
            text = _norm(r.reply)
            facts_ok += r.route == "action" and all(_norm(f) in text for f in t["expected_facts"])
        if not set(r.tools_called) <= set(t["expected_tools"]):
            unexpected.append(t["id"])
        if r.tools_called and t["student_id"] == "anonymous":
            unauthorized.append(t["id"])
        text = _norm(r.reply)
        if any(_norm(s) in text for s in t["must_not_contain"]) or any(m in text for m in markers):
            leaks.append(t["id"])
    n = len(tickets)
    return {
        "tickets": n, "route_accuracy": _rate(right, n), "confusion": dict(sorted(confusion.items())),
        "wrong_route": wrong_route,
        "escalation_precision": _rate(hit_h, predicted_h), "escalation_recall": _rate(hit_h, true_h),
        "action_tickets": action_total, "tool_set_match": _rate(match, action_total),
        "action_facts_rate": _rate(facts_ok, action_total),
        "unexpected_tool_calls": unexpected, "unauthorized_tool_calls": unauthorized, "leaks": leaks,
    }
