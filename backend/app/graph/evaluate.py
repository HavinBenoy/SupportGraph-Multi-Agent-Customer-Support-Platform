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
Build step 4 adds:
  write_route_rate      write tickets sent to the write branch
  pause_correct_rate    write tickets that paused for the RIGHT approver (or, if tagged needs_clarification, asked a question instead)
  escalations_recorded  share of runs that needed a human which got a persisted ticket + brief (a reference id)
  qa_blocked            tickets whose reply was replaced by the QA fallback (informational: a high number means the model is unreliable)
  writes_without_decision  rows changed with no recorded decision (computed from the audit log by the script; target 0)

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
    awaiting: str = ""                       # "staff" | "student" when the run paused for an approval
    escalation_id: str | None = None
    qa_blocked: bool = False


def from_state(state: dict) -> RoutedResult:
    return RoutedResult(route=state["route"], route_reason=state.get("route_reason", ""), reply=state.get("reply", ""),
                        tools_called=state.get("tools_called", []), needs_human=state.get("needs_human", False),
                        abstained=state.get("abstained", False), citations=state.get("citations", []),
                        sources=state.get("sources", []), queries=state.get("queries", []), awaiting=state.get("awaiting", ""),
                        escalation_id=state.get("escalation_id"), qa_blocked=state.get("qa_blocked", False))


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
    write_total = write_routed = pause_ok = 0
    human_runs = recorded = 0
    qa_blocked: list[str] = []
    for t in tickets:
        r = run_fn(t)
        right += r.route == t["expected_route"]
        confusion[f"{t['expected_route']}->{r.route}"] = confusion.get(f"{t['expected_route']}->{r.route}", 0) + 1
        if r.route != t["expected_route"]:
            wrong_route.append({"id": t["id"], "expected": t["expected_route"], "got": r.route, "reason": r.route_reason})
        if t["expected_route"] == "write":
            write_total += 1
            write_routed += r.route == "write"
            want = {"request_makeup_class": "staff", "update_contact_phone": "student"}.get(t["expected_write_action"])
            clarify = "needs_clarification" in t["tags"]
            pause_ok += r.route == "write" and ((not r.awaiting) if clarify else r.awaiting == want)
        if r.needs_human:
            human_runs += 1
            recorded += bool(r.escalation_id)
        if r.qa_blocked:
            qa_blocked.append(t["id"])
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
        "write_tickets": write_total, "write_route_rate": _rate(write_routed, write_total), "pause_correct_rate": _rate(pause_ok, write_total),
        "escalations_recorded": _rate(recorded, human_runs), "qa_blocked": qa_blocked,
        "unexpected_tool_calls": unexpected, "unauthorized_tool_calls": unauthorized, "leaks": leaks,
    }
