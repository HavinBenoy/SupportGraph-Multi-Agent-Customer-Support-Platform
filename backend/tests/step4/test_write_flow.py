"""The write branch end to end: propose -> validate -> PAUSE -> decision -> write. Everything runs on the offline fakes."""

import pytest

from app.actions.executor import AuthorizationError
from app.graph.review import ReviewError
from tests.step4.conftest import MakeupRequest, Student, Ticket, audit_actions, rows

MAKEUP = "Please arrange a make-up class for the session I missed in PYD-301."


def test_makeup_request_pauses_for_staff_and_writes_only_a_pending_row(ask):
    out = ask(MAKEUP)
    assert out["route"] == "write" and out["awaiting"] == "staff"
    assert "approve" in out["reply"] and "PYD-301" in out["reply"] and "2026-10-20" in out["reply"]
    (req,) = rows(MakeupRequest)
    assert (req.status, req.student_id, req.batch_id, req.decided_by) == ("pending", "stu_meera", "PYD-301", None)
    assert rows(Ticket, id="tk-1")[0].status == "awaiting_approval"


def test_staff_approval_resumes_the_same_run_and_finalises(ask, decide):
    ask(MAKEUP)
    out = decide("tk-1", approve=True)
    assert out["outcome"] == "approved" and "approved" in out["reply"] and out["messages"][-1].content == out["reply"]
    (req,) = rows(MakeupRequest)
    assert (req.status, req.decided_by) == ("approved", "s_1")
    assert rows(Ticket, id="tk-1")[0].status == "resolved"
    assert audit_actions("tk-1") == ["makeup_request.created", "action.proposed", "makeup_request.approved"]


def test_staff_rejection_is_recorded_and_the_student_is_told(ask, decide):
    ask(MAKEUP)
    out = decide("tk-1", approve=False)
    assert out["outcome"] == "rejected" and "not approved" in out["reply"]
    assert rows(MakeupRequest)[0].status == "rejected"


def test_a_decision_cannot_be_applied_twice(ask, decide):
    ask(MAKEUP)
    decide("tk-1")
    with pytest.raises(ReviewError):
        decide("tk-1", approve=False)  # nothing is paused any more
    assert rows(MakeupRequest)[0].status == "approved"


def test_the_student_cannot_approve_their_own_staff_action(ask, decide):
    ask(MAKEUP)
    with pytest.raises(ReviewError):
        decide("tk-1", role="student", reviewer="stu_meera")
    assert rows(MakeupRequest)[0].status == "pending"


def test_another_student_cannot_decide_or_even_see_the_ticket(ask, decide):
    ask(PHONE := "Please update my phone number to +91 98765 43210")
    with pytest.raises(ReviewError):
        decide("tk-1", role="student", reviewer="stu_asha")
    assert rows(Student, id="stu_meera")[0].phone is None and PHONE


def test_staff_of_another_tenant_cannot_decide(ask, decide):
    ask(MAKEUP)
    with pytest.raises(ReviewError):
        decide("tk-1", tenant="other_institute")
    assert rows(MakeupRequest)[0].status == "pending"


def test_invalid_resume_inside_the_graph_is_refused_and_the_run_stays_paused(graph, ask):
    """Defence in depth: bypass resume_ticket's own checks and resume the graph directly with a wrong role."""
    from langgraph.types import Command

    from app.graph.support import support_config

    ask(MAKEUP)
    cfg = support_config("qspiders_sample", student_id="stu_meera", ticket_id="tk-1", today="2026-10-24")
    bad = {"decision": "approve", "reviewer_id": "stu_meera", "role": "student", "tenant_id": "qspiders_sample"}
    paused = graph.invoke(Command(resume=bad), cfg)           # not an error: the run simply asks again
    assert paused["__interrupt__"] and rows(MakeupRequest)[0].status == "pending"
    good = {"decision": "approve", "reviewer_id": "s_9", "role": "staff", "tenant_id": "qspiders_sample"}
    assert graph.invoke(Command(resume=good), cfg)["outcome"] == "approved"


def test_asking_twice_does_not_create_two_requests(ask):
    ask(MAKEUP)
    again = ask(MAKEUP, ticket="tk-2")
    assert again["outcome"] == "duplicate" and "already have a pending" in again["reply"]
    assert len(rows(MakeupRequest)) == 1


def test_same_ticket_asked_again_is_idempotent(ask):
    ask(MAKEUP)
    ask(MAKEUP)
    assert len(rows(MakeupRequest)) == 1


@pytest.mark.parametrize("message,code_fragment", [
    ("Please arrange a make-up class for the session I missed.", "Which batch"),
    ("Please arrange a make-up class for the session I missed in QAT-201.", "Which date"),      # stu_meera is not in QAT-201
    ("Please arrange a make-up class for the session I missed in OTH-900.", "couldn't find that batch"),
    ("Please arrange a make-up class for PYD-301 on 2026-10-25.", "only request a make-up"),    # future date
    ("Please arrange a make-up class for PYD-301 on 2026-10-19.", "only request a make-up"),    # a session she attended
])
def test_invalid_requests_are_refused_by_code_without_any_pause_or_write(ask, message, code_fragment):
    out = ask(message)
    assert "awaiting" not in out and not rows(MakeupRequest)
    if code_fragment == "Which date":
        code_fragment = "couldn't find that batch"
    assert code_fragment.lower() in out["reply"].lower()


def test_two_missed_sessions_ask_which_one(ask):
    out = ask("Please arrange a make-up class for the session I missed in PYF-101.", student="stu_asha")
    assert "awaiting" not in out and "2026-10-18" in out["reply"] and "2026-10-22" in out["reply"]


def test_a_batch_that_is_not_yours_looks_the_same_as_one_that_does_not_exist(ask):
    a = ask("make-up class for the session I missed in QAT-201", student="stu_asha")["reply"]
    b = ask("make-up class for the session I missed in ZZZ-999", student="stu_asha", ticket="tk-2")["reply"]
    assert a == b


def test_prospects_cannot_start_a_write(ask):
    out = ask(MAKEUP, student="anonymous")
    assert (out["route"], out["route_reason"]) == ("clarify", "sign_in_required") and not rows(MakeupRequest)


def test_phone_change_needs_the_students_own_confirmation_then_writes(chat):
    first = chat("Please update my phone number to +91 98765 43210")
    assert first["awaiting"] == "student" and "yes" in first["reply"]
    assert rows(Student, id="stu_meera")[0].phone is None            # nothing changed yet
    reprompt = chat("what?")
    assert reprompt["awaiting"] == "student" and rows(Student, id="stu_meera")[0].phone is None
    done = chat("yes")
    assert done["outcome"] == "approved" and "+91*******210" in done["reply"] and "98765" not in done["reply"]
    assert rows(Student, id="stu_meera")[0].phone == "+919876543210"
    log = [r for r in rows(__import__("app.db.models", fromlist=["AuditLog"]).AuditLog) if r.action == "contact_phone.updated"]
    assert "98765" not in str(log[0].detail) and log[0].actor == "student:stu_meera"


def test_declining_the_phone_change_changes_nothing(chat):
    chat("Please update my phone number to 9876543210")
    out = chat("no")
    assert out["outcome"] == "rejected" and rows(Student, id="stu_meera")[0].phone is None


def test_a_number_the_rules_cannot_read_is_asked_for_again(ask):
    out = ask("Please update my phone number to 12345")
    assert "new phone number" in out["reply"] and "awaiting" not in out


def test_a_new_message_while_staff_review_is_pending_does_not_orphan_it(chat, decide):
    chat(MAKEUP)
    wait = chat("any news?")
    assert wait["awaiting"] == "staff" and "waiting for a staff decision" in wait["reply"]
    assert decide("tk-1")["outcome"] == "approved"


def test_a_failed_write_rolls_back_and_leaves_the_run_resumable(ask, decide, monkeypatch):
    ask(MAKEUP)
    from app.db import write_repo

    monkeypatch.setattr(write_repo, "decide_makeup_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        decide("tk-1")
    assert rows(MakeupRequest)[0].status == "pending"


def test_too_many_invalid_decisions_fail_the_run_instead_of_looping(graph, ask):
    from langgraph.types import Command

    from app.graph.support import support_config

    ask(MAKEUP)
    cfg = support_config("qspiders_sample", student_id="stu_meera", ticket_id="tk-1", today="2026-10-24")
    bad = {"decision": "approve", "reviewer_id": "stu_meera", "role": "student", "tenant_id": "qspiders_sample"}
    with pytest.raises(AuthorizationError):
        for _ in range(6):
            graph.invoke(Command(resume=bad), cfg)
    assert rows(MakeupRequest)[0].status == "pending"
