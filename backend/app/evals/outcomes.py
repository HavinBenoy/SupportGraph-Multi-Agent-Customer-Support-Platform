"""Run one labelled ticket through a system and record what happened, as plain data (build step 6).

Two systems are compared on the SAME tickets:
  graph     the full support graph (triage -> router -> knowledge / action / write / escalate -> QA)
  baseline  the step 2 single-pass RAG FAQ bot: no routing, no tools, no escalation, no actions

`handled_correctly` is the one outcome both are scored on: "did the person get the right kind of help for this ticket?"
  knowledge (answerable)   answered, with every expected fact, no leak
  knowledge (must_abstain) declined instead of inventing an answer
  action                   used exactly the expected read tools and the reply holds the expected facts
  write                    paused for the right approver (or asked a question when the ticket is tagged needs_clarification)
  escalate                 reached a human AND the hand-off was recorded (reference id)
  clarify                  asked/declined without using tools, leaking or answering
The baseline can only ever be right on the knowledge and clarify kinds: that gap IS the comparison.
"""

import time
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler

from app.rag.evaluate import _norm

WRITE_APPROVER = {"request_makeup_class": "staff", "update_contact_phone": "student"}

# Dataset category -> triage category. "other" (and anything not listed) is not scored for category accuracy: it covers several intents.
CATEGORY_MAP = {"courses": "courses", "eligibility": "eligibility", "fees": "fees", "admissions": "admissions", "placement": "placement",
                "attendance": "attendance", "assessment": "assessment", "schedule": "schedule", "privacy": "privacy_policy"}
TAG_CATEGORY = {"complaint": "complaint", "payment_dispute": "payment_dispute", "legal_threat": "legal_threat", "safety_incident": "safety"}


def expected_triage_category(t: dict) -> str | None:
    for tag in t.get("tags", []):
        if tag in TAG_CATEGORY:
            return TAG_CATEGORY[tag]
    return CATEGORY_MAP.get(t.get("category"))


class UsageMeter(BaseCallbackHandler):
    """Counts model calls and provider-reported tokens (input/output). With the offline fakes nothing is reported, so the metrics
    say "estimated" instead of pretending."""

    def __init__(self) -> None:
        self.calls = self.input_tokens = self.output_tokens = 0
        self.reported = False

    def on_chat_model_start(self, *a: Any, **k: Any) -> None:
        self.calls += 1

    def on_llm_start(self, *a: Any, **k: Any) -> None:
        self.calls += 1

    def on_llm_end(self, response: Any, **k: Any) -> None:
        for gens in getattr(response, "generations", []):
            for g in gens:
                u = getattr(getattr(g, "message", None), "usage_metadata", None)
                if u:
                    self.reported = True
                    self.input_tokens += int(u.get("input_tokens", 0))
                    self.output_tokens += int(u.get("output_tokens", 0))


def estimate_tokens(text: str) -> int:
    """~4 characters per token: a rough, clearly-labelled stand-in when the provider reports nothing."""
    return max(1, len(text) // 4) if text else 0


@dataclass
class TicketOutcome:
    id: str
    system: str
    expected_route: str
    route: str = ""                       # graph: the branch; baseline: "knowledge" if it answered, "abstain" if it declined
    route_reason: str = ""
    reply: str = ""
    needs_human: bool = False
    awaiting: str = ""
    abstained: bool = False
    tools_called: list[str] = field(default_factory=list)
    escalation_id: str | None = None
    qa_blocked: bool = False
    sources: list[dict] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    triage: dict = field(default_factory=dict)
    latency_ms: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_estimated: bool = False
    error: str = ""
    # filled by score():
    handled_correctly: bool = False
    leaked: bool = False
    unsupported_numbers: list[str] = field(default_factory=list)
    unexpected_tools: bool = False
    unauthorized_tools: bool = False
    facts_missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_graph_ticket(graph, t: dict, *, run_id: str, today: str | None) -> TicketOutcome:
    from app.graph.support import _with_pause, support_config
    from langchain_core.messages import HumanMessage

    meter = UsageMeter()
    ticket_id = f"eval-{t['id']}-{run_id}"
    config = support_config(t["tenant_id"], student_id=t["student_id"], ticket_id=ticket_id, **({"today": today} if today else {}))
    config = {**config, "callbacks": [*(config.get("callbacks") or []), meter]}
    started = time.perf_counter()
    o = TicketOutcome(id=t["id"], system="graph", expected_route=t.get("expected_route", ""))
    try:
        st = _with_pause(graph, graph.invoke({"messages": [HumanMessage(t["message"])]}, config), config)
    except Exception as e:  # noqa: BLE001 - an exception is a finding, not a reason to stop the run
        o.error = f"{type(e).__name__}: {e}"[:300]
        o.route = "error"
        o.latency_ms = int((time.perf_counter() - started) * 1000)
        return o
    o.latency_ms = int((time.perf_counter() - started) * 1000)
    o.route, o.route_reason, o.reply = st.get("route", ""), st.get("route_reason", ""), st.get("reply", "")
    o.needs_human, o.awaiting, o.abstained = bool(st.get("needs_human")), st.get("awaiting", ""), bool(st.get("abstained"))
    o.tools_called, o.escalation_id, o.qa_blocked = list(st.get("tools_called", [])), st.get("escalation_id"), bool(st.get("qa_blocked"))
    o.sources, o.citations, o.triage = st.get("sources", []), st.get("citations", []), st.get("triage", {}) or {}
    _usage(o, meter, t)
    return o


def run_baseline_ticket(faq, t: dict, *, run_id: str, today: str | None = None) -> TicketOutcome:
    from app.rag.faq import answer_question

    meter = UsageMeter()
    started = time.perf_counter()
    o = TicketOutcome(id=t["id"], system="baseline", expected_route=t.get("expected_route", ""))
    try:
        a = answer_question(faq, t["message"], tenant_id=t["tenant_id"], ticket_id=f"eval-b-{t['id']}-{run_id}", student_id=t["student_id"])
    except Exception as e:  # noqa: BLE001
        o.error = f"{type(e).__name__}: {e}"[:300]
        o.route = "error"
        o.latency_ms = int((time.perf_counter() - started) * 1000)
        return o
    o.latency_ms = int((time.perf_counter() - started) * 1000)
    o.reply, o.abstained, o.route_reason = a.answer, a.abstained, a.reason
    o.route = "abstain" if a.abstained else "knowledge"
    o.citations, o.sources = a.citations, a.sources
    _usage(o, meter, t)
    return o


def _usage(o: TicketOutcome, meter: UsageMeter, t: dict) -> None:
    o.llm_calls = meter.calls
    if meter.reported:
        o.input_tokens, o.output_tokens = meter.input_tokens, meter.output_tokens
    else:
        o.tokens_estimated = True
        o.input_tokens, o.output_tokens = estimate_tokens(t["message"]), estimate_tokens(o.reply)


def score(o: TicketOutcome, t: dict, *, markers: list[str]) -> TicketOutcome:
    """Fill in the judgement fields from the label. Pure function of (outcome, label, markers): no I/O, easy to test."""
    from app.rag.evaluate import unsupported_numbers

    text = _norm(o.reply)
    o.leaked = any(_norm(s) in text for s in t["must_not_contain"]) or any(m in text for m in markers)
    o.unexpected_tools = not set(o.tools_called) <= set(t["expected_tools"])
    o.unauthorized_tools = bool(o.tools_called) and t["student_id"] == "anonymous"
    answered = o.route in ("knowledge",) and not o.abstained
    o.unsupported_numbers = unsupported_numbers(o.reply, o.sources) if answered else []
    exp = t["expected_route"]
    o.facts_missing = [f for f in t["expected_facts"] if _norm(f) not in text] if exp in ("knowledge", "action") else []
    ok = False
    if o.error:
        ok = False
    elif exp == "knowledge":
        if t["must_abstain"]:
            ok = o.abstained or o.route == "abstain"
        else:
            ok = answered and not o.facts_missing
        ok = ok and not o.leaked
    elif exp == "action":
        ok = o.route == "action" and set(o.tools_called) == set(t["expected_tools"]) and not o.facts_missing and not o.leaked
    elif exp == "write":
        want = WRITE_APPROVER.get(t.get("expected_write_action"))
        clarify = "needs_clarification" in t.get("tags", [])
        ok = o.route == "write" and ((not o.awaiting) if clarify else o.awaiting == want) and not o.leaked
    elif exp == "escalate":
        ok = o.needs_human and bool(o.escalation_id) and not o.leaked
    elif exp == "clarify":
        ok = (o.route in ("clarify", "abstain") or o.abstained) and not o.tools_called and not o.leaked
    o.handled_correctly = ok
    if not ok:
        o.problems.append(f"expected {exp}, got {o.route}" + (f" ({o.route_reason})" if o.route_reason else ""))
    if o.leaked:
        o.problems.append("leak")
    if o.unexpected_tools:
        o.problems.append(f"unexpected tools {o.tools_called}")
    if o.error:
        o.problems.append(o.error)
    return o


def run_dataset(system: str, runner: Callable[[dict], TicketOutcome], tickets: list[dict], *, markers: list[str]) -> list[TicketOutcome]:
    return [score(runner(t), t, markers=markers) for t in tickets]


def today_for(tenant_id: str) -> str:
    from datetime import timedelta

    from sqlalchemy import func, select

    from app.db.models import AttendanceRecord
    from app.db.session import session_scope

    with session_scope() as db:
        last = db.scalar(select(func.max(AttendanceRecord.session_date)).where(AttendanceRecord.tenant_id == tenant_id))
    return (last + timedelta(days=1)).isoformat() if last else date.today().isoformat()
