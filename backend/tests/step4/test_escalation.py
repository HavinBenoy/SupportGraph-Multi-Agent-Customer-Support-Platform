"""Escalation: ticket + brief are persisted, scoped, masked, honest; the staff queue is tenant-safe."""

import pytest

from app.db import write_repo
from app.db.session import session_scope
from app.escalation import queue
from app.escalation.brief import EscalationBrief
from tests.step4.conftest import AuditLog, Escalation, Ticket, rows


def test_payment_dispute_creates_a_ticket_and_a_scoped_brief(ask):
    out = ask("I paid the first instalment twice, my number is 9876543210 and email me at a@b.com. I want my money back!", student="stu_asha")
    assert out["route"] == "escalate" and out["needs_human"] and out["escalation_id"] in out["reply"] and "accounts" in out["reply"]
    (esc,) = rows(Escalation)
    brief = EscalationBrief.model_validate(esc.brief)
    assert (esc.team, esc.priority, brief.sla_hours) == ("accounts", "high", 24)
    assert brief.student_id == "stu_asha" and brief.student_name
    assert [(b.batch_id, b.attendance_pct) for b in brief.batches] == [("PYF-101", 80.0)]
    assert "9876543210" not in brief.excerpt and "a@b.com" not in brief.excerpt and "[phone]" in brief.excerpt and "[email]" in brief.excerpt
    assert brief.suggested_next_step and brief.trail["route"] == "escalate"
    assert rows(Ticket, id="tk-1")[0].status == "escalated"


def test_brief_contains_only_the_askers_own_records(ask):
    ask("I want to complain about my trainer", student="stu_asha")
    text = str(rows(Escalation)[0].brief)
    assert "stu_rahul" not in text and "stu_meera" not in text and "QAT-201" not in text and "PYD-301" not in text


def test_anonymous_escalation_is_recorded_without_a_student_and_says_so(ask):
    out = ask("I paid twice and want my money back", student="anonymous")
    assert "not signed in" in out["reply"]
    esc = rows(Escalation)[0]
    assert rows(Ticket, id="tk-1")[0].student_id is None and EscalationBrief.model_validate(esc.brief).signed_in is False


def test_reply_makes_no_promise_about_time_or_outcome(ask):
    reply = ask("I want to complain about my trainer", student="stu_asha")["reply"].lower()
    assert "can't tell you when" in reply and not any(w in reply for w in ("within", "24 hours", "refund", "will call"))


def test_same_ticket_escalates_once(ask):
    ask("I want to complain about my trainer", student="stu_asha")
    ask("I want to complain about my trainer again", student="stu_asha")
    assert len(rows(Escalation)) == 1


def test_urgent_safety_gets_the_management_team_and_top_priority(ask):
    ask("Another student keeps threatening me at the centre and I feel unsafe.", student="stu_asha")
    esc = rows(Escalation)[0]
    assert (esc.team, esc.priority, esc.reason) == ("management", "urgent", "safety")


def test_concession_requests_are_a_human_decision_not_a_write(ask):
    out = ask("Can I get a discount on the fee?", student="stu_asha")
    assert out["route"] == "escalate" and rows(Escalation)[0].team == "accounts" and "approved" not in out["reply"].lower()


def test_failure_to_record_is_reported_honestly(ask, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def boom(*a, **k):
        raise OperationalError("insert", {}, Exception("db down"))

    monkeypatch.setattr(write_repo, "create_escalation", boom)
    out = ask("I want to complain about my trainer", student="stu_asha")
    assert out["needs_human"] and out["escalation_id"] is None and "couldn't record it" in out["reply"] and "reference" not in out["reply"]
    assert not rows(Escalation)


def test_someone_elses_ticket_id_is_refused_not_merged(ask):
    ask("I want to complain about my trainer", student="stu_asha", ticket="shared")
    out = ask("I want to complain about my trainer", student="stu_rahul", ticket="shared")
    # The ticket row belongs to Asha: Rahul's write is refused and he gets the honest "could not record" reply.
    assert out["escalation_id"] is None and "couldn't record" in out["reply"]
    assert len(rows(Escalation)) == 1 and rows(Escalation)[0].brief["student_id"] == "stu_asha"


def test_queue_orders_claims_and_resolves(ask):
    ask("I want to complain about my trainer", student="stu_asha", ticket="a")          # high
    ask("Another student keeps threatening me at the centre and I feel unsafe.", student="stu_rahul", ticket="b")  # urgent
    with session_scope() as db:
        listed = queue.list_escalations(db, "qspiders_sample")
        assert [e.priority for e in listed] == ["urgent", "high"]
        assert [e.team for e in queue.list_escalations(db, "qspiders_sample", team="accounts")] == []
        assert queue.list_escalations(db, "other_institute") == []
        first = listed[0]
        queue.claim(db, "qspiders_sample", first.id, "s_1")
        with pytest.raises(write_repo.WriteError):
            queue.claim(db, "qspiders_sample", first.id, "s_2")
        with pytest.raises(write_repo.WriteError):
            queue.resolve(db, "qspiders_sample", first.id, "s_2", "x")
        queue.resolve(db, "qspiders_sample", first.id, "s_1", "called the student")
        with pytest.raises(write_repo.WriteError):
            queue.resolve(db, "qspiders_sample", first.id, "s_1", "again")
        with pytest.raises(write_repo.WriteError):
            queue.claim(db, "other_institute", listed[1].id, "s_9")   # another tenant cannot touch it
    actions = [r.action for r in rows(AuditLog)]
    assert "escalation.claimed" in actions and "escalation.resolved" in actions
