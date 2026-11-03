"""Policy validation and the executor's authorization boundary, tested directly (no graph)."""

from datetime import date

import pytest

from app.actions.executor import WRITE_ACTIONS, AuthorizationError, authorize_decision, execute_action, prepare_action
from app.actions.policy import APPROVAL, validate_proposal
from app.actions.proposer import LlmProposer, RuleProposer
from app.actions.schemas import ActionProposal
from app.db import write_repo
from app.db.session import session_scope
from app.tenants.loader import load_tenant_config
from app.tenants.scope import Reviewer, Scope
from app.tools.registry import get_tools

CFG = load_tenant_config("qspiders_sample")
MEERA = Scope("qspiders_sample", "stu_meera", date(2026, 10, 24))


def check(proposal, scope=MEERA):
    with session_scope() as db:
        return validate_proposal(db, scope, proposal, CFG)


def test_write_actions_are_not_llm_tools():
    exposed = {t.name for t in get_tools()}
    assert not exposed & WRITE_ACTIONS and not any("makeup" in n or "phone" in n for n in exposed)


def test_every_action_has_an_approver_and_none_is_automatic():
    assert set(APPROVAL) == WRITE_ACTIONS and set(APPROVAL.values()) <= {"staff", "student"}


def test_single_missed_session_is_picked_automatically():
    v = check(ActionProposal(action="request_makeup_class", batch_id="pyd-301"))
    assert v.ok and v.args["batch_id"] == "PYD-301" and v.args["session_date"] == date(2026, 10, 20)


def test_window_is_enforced():
    # stu_meera missed 2026-10-20; a 2-day window (today 10-24) no longer contains it
    from app.tenants.schema import TenantConfig

    short = TenantConfig(**{**CFG.model_dump(), "makeup_window_days": 1})
    with session_scope() as db:
        assert validate_proposal(db, MEERA, ActionProposal(action="request_makeup_class", batch_id="PYD-301"), short).code == "no_missed_session"


@pytest.mark.parametrize("batch", ["PYF-101_x", "pyf101", "", None])
def test_malformed_batch_is_treated_as_missing(batch):
    assert check(ActionProposal(action="request_makeup_class", batch_id=batch)).code == "missing_batch"


def test_withdrawn_or_other_tenant_enrolment_gives_no_access():
    neha = Scope("qspiders_sample", "stu_neha", date(2026, 10, 24))  # withdrawn from PYD-301
    assert check(ActionProposal(action="request_makeup_class", batch_id="PYD-301"), neha).code == "batch_not_found"
    other = Scope("other_institute", "stu_asha", date(2026, 10, 24))  # same ids in another tenant: only that tenant's data
    assert check(ActionProposal(action="request_makeup_class", batch_id="QAT-201"), other).code == "batch_not_found"


def test_reason_is_cleaned_and_bounded():
    v = check(ActionProposal(action="request_makeup_class", batch_id="PYD-301", reason="had\x00 fever\n" + "x" * 280))
    assert v.ok and "\x00" not in v.args["reason"] and len(v.args["reason"]) <= 300


def test_an_over_long_reason_is_rejected_by_the_schema():
    with pytest.raises(ValueError):
        ActionProposal(action="request_makeup_class", batch_id="PYD-301", reason="x" * 301)


@pytest.mark.parametrize("phone,ok", [("+91 98765-43210", True), ("9876543210", True), ("12345", False), ("98765abc4321", False), ("+" + "9" * 20, False)])
def test_phone_validation(phone, ok):
    assert check(ActionProposal(action="update_contact_phone", phone=phone)).ok is ok


def staff(tenant="qspiders_sample"):
    return Reviewer(tenant, "s_1", "staff")


def test_authorize_decision_rules():
    authorize_decision(MEERA, "request_makeup_class", staff())
    authorize_decision(MEERA, "update_contact_phone", Reviewer("qspiders_sample", "stu_meera", "student"))
    for action, reviewer in [
        ("request_makeup_class", Reviewer("qspiders_sample", "stu_meera", "student")),   # student approving a staff action
        ("update_contact_phone", staff()),                                                 # staff confirming a student's change
        ("update_contact_phone", Reviewer("qspiders_sample", "stu_asha", "student")),     # someone else's change
        ("request_makeup_class", staff("other_institute")),                                # another tenant
        ("delete_everything", staff()),                                                    # unknown action
    ]:
        with pytest.raises(AuthorizationError):
            authorize_decision(MEERA, action, reviewer)


def test_executor_writes_nothing_when_unauthorized():
    with session_scope() as db:
        rid = prepare_action(db, MEERA, ticket_id="t1", action="request_makeup_class",
                             args={"batch_id": "PYD-301", "session_date": date(2026, 10, 20), "reason": "x"})
    with pytest.raises(AuthorizationError), session_scope() as db:
        execute_action(db, MEERA, ticket_id="t1", action="request_makeup_class", args={}, request_id=rid, approve=True,
                       reviewer=Reviewer("qspiders_sample", "stu_meera", "student"))
    with session_scope() as db:
        assert write_repo.decide_makeup_request(db, "qspiders_sample", rid, approve=False, reviewer_id="s_1").status == "rejected"
        with pytest.raises(write_repo.WriteError):
            write_repo.decide_makeup_request(db, "qspiders_sample", rid, approve=True, reviewer_id="s_1")  # already decided


def test_decision_on_another_tenants_request_id_is_unknown():
    with session_scope() as db:
        rid = prepare_action(db, MEERA, ticket_id="t1", action="request_makeup_class",
                             args={"batch_id": "PYD-301", "session_date": date(2026, 10, 20), "reason": "x"})
        with pytest.raises(write_repo.WriteError):
            write_repo.decide_makeup_request(db, "other_institute", rid, approve=True, reviewer_id="s_1")


def test_ticket_ids_cannot_be_borrowed_by_another_student():
    with session_scope() as db:
        write_repo.ensure_ticket(db, "qspiders_sample", "t-9", "stu_meera")
        with pytest.raises(write_repo.WriteError):
            write_repo.ensure_ticket(db, "qspiders_sample", "t-9", "stu_asha")
        with pytest.raises(write_repo.WriteError):
            write_repo.ensure_ticket(db, "qspiders_sample", "t-9", None)


def test_rule_proposer_examples():
    p = RuleProposer().propose("make-up class for pyf-101 on 2026-10-18 because I was ill, call 9876543210")
    assert (p.action, p.batch_id, str(p.session_date)) == ("request_makeup_class", "pyf-101", "2026-10-18") and "9876543210" not in p.reason
    assert RuleProposer().propose("change my phone number to +91 98765 43210").phone.startswith("+91")
    assert RuleProposer().propose("what is the fee") is None


class _Chain:
    def __init__(self, out=None, err=None):
        self.out, self.err = out, err

    def with_structured_output(self, _):
        return self

    def invoke(self, _):
        if self.err:
            raise self.err
        return self.out


@pytest.mark.parametrize("out,err", [({"action": "drop_tables"}, None), ({"action": "update_contact_phone", "admin": 1}, None),
                                    ("junk", None), (None, None), (None, RuntimeError("503"))])
def test_llm_proposer_never_turns_bad_output_into_a_proposal(out, err):
    assert LlmProposer(_Chain(out, err)).propose("anything") is None


def test_llm_proposer_accepts_a_valid_dict():
    assert LlmProposer(_Chain({"action": "request_makeup_class", "batch_id": "PYD-301"})).propose("x").batch_id == "PYD-301"
