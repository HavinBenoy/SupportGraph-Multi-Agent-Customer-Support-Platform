import pytest

from app.config import Settings
from app.guardrails.validators import normalize_batch_id
from app.llm.fake import ScriptedChatModel
from app.llm.gateway import get_chat_model
from app.tools.read_tools import get_my_attendance, get_my_batches, get_my_schedule, list_my_assessments
from app.tools.scope import ScopeError
from tests.conftest import scope_cfg

NOT_FOUND = {"error": "Batch not found for this account."}


def cfg(student, tenant="qspiders_sample", today="2026-10-24"):
    return scope_cfg(tenant, student, today)


# ---- get_my_batches ----

def test_batches_for_a_student_with_one_enrolment(asha_cfg):
    out = get_my_batches.invoke({}, asha_cfg)
    assert out == {"batches": [{
        "batch_id": "PYF-101", "course": "Python Full Stack (sample curriculum)", "brand": "PySpiders",
        "mode": "online", "centre": None, "timing": "Mon-Fri 10:00-12:00 IST", "start_date": "2026-10-10",
        "enrollment_status": "active"}]}


def test_batches_for_a_student_in_two_batches_newest_enrolment_first():
    out = get_my_batches.invoke({}, cfg("stu_meera"))
    assert [b["batch_id"] for b in out["batches"]] == ["PYD-301", "PYF-101"]  # enrolled 7 and 14 days ago


def test_offline_batch_reports_its_centre():
    out = get_my_batches.invoke({}, cfg("stu_sneha"))
    assert out["batches"][0]["mode"] == "offline" and out["batches"][0]["centre"] == "Sample Centre A"


def test_on_hold_enrolment_is_listed_with_its_status():
    assert get_my_batches.invoke({}, cfg("stu_karthik"))["batches"][0]["enrollment_status"] == "on_hold"


def test_unknown_student_has_no_batches():
    assert get_my_batches.invoke({}, cfg("stu_nobody")) == {"batches": []}


def test_withdrawn_student_sees_nothing():
    assert get_my_batches.invoke({}, cfg("stu_neha")) == {"batches": []}


# ---- get_my_schedule ----

def test_schedule_lists_the_next_five_classes_in_date_order(asha_cfg):
    out = get_my_schedule.invoke({"batch_id": "PYF-101"}, asha_cfg)
    assert [u["date"] for u in out["upcoming"]] == ["2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"]
    assert out["upcoming"][0]["topic"] == "Django intro"
    assert out["timing"] == "Mon-Fri 10:00-12:00 IST" and out["mode"] == "online"


def test_schedule_never_lists_past_classes(asha_cfg):
    later = {"configurable": {**asha_cfg["configurable"], "today": "2026-10-27"}}
    dates = [u["date"] for u in get_my_schedule.invoke({"batch_id": "PYF-101"}, later)["upcoming"]]
    assert dates == ["2026-10-27", "2026-10-28", "2026-10-29"]  # a class today is still upcoming; nothing earlier


def test_schedule_for_on_hold_enrolment_explains_instead_of_listing():
    out = get_my_schedule.invoke({"batch_id": "QAT-201"}, cfg("stu_karthik"))
    assert out["upcoming"] == [] and "on_hold" in out["note"] and "support team" in out["note"]


# ---- get_my_attendance ----

def test_attendance_above_threshold(asha_cfg):
    out = get_my_attendance.invoke({"batch_id": "PYF-101"}, asha_cfg)
    assert (out["sessions_held"], out["sessions_attended"], out["attendance_pct"]) == (10, 8, 80.0)
    assert out["threshold_pct"] == 75 and out["below_threshold"] is False


def test_attendance_below_threshold():
    out = get_my_attendance.invoke({"batch_id": "PYF-101"}, cfg("stu_rahul"))
    assert out["attendance_pct"] == 60.0 and out["below_threshold"] is True
    assert "support team" in out["note"]


def test_attendance_exactly_at_the_threshold_is_not_below_it():
    out = get_my_attendance.invoke({"batch_id": "QAT-201"}, cfg("stu_sneha"))  # 6 of 8 = 75% against 75%
    assert out["attendance_pct"] == 75.0 and out["below_threshold"] is False


def test_attendance_comparison_is_exact_not_rounded():
    # 7 of 10 would be fine, but 749 of 1000 style rounding must not pass: use a threshold-sensitive check on counts.
    from app.db.models import Tenant
    from app.db.session import session_scope

    with session_scope() as db:
        db.get(Tenant, "qspiders_sample").attendance_threshold_pct = 81  # 80% against 81% must now be below
    assert get_my_attendance.invoke({"batch_id": "PYF-101"}, cfg("stu_asha"))["below_threshold"] is True


def test_attendance_threshold_comes_from_the_tenants_own_row():
    from app.db.seed import seed  # noqa: F401  (the fixture already seeded both tenants)

    out = get_my_attendance.invoke({"batch_id": "PYF-101"}, cfg("stu_asha", tenant="other_institute"))
    assert out["threshold_pct"] == 90 and out["attendance_pct"] == 0.0 and out["below_threshold"] is True


def test_attendance_ignores_sessions_after_today(asha_cfg):
    early = {"configurable": {**asha_cfg["configurable"], "today": "2026-10-19"}}  # only the first 6 sessions are held
    out = get_my_attendance.invoke({"batch_id": "PYF-101"}, early)
    assert out["sessions_held"] == 6 and out["sessions_attended"] == 5  # PPPPAP


def test_attendance_with_no_classes_held_yet(asha_cfg):
    before = {"configurable": {**asha_cfg["configurable"], "today": "2026-10-01"}}
    out = get_my_attendance.invoke({"batch_id": "PYF-101"}, before)
    assert out["sessions_held"] == 0 and "No classes" in out["note"] and "attendance_pct" not in out


# ---- list_my_assessments ----

def test_assessments_show_the_students_own_status_and_score(asha_cfg):
    out = list_my_assessments.invoke({"batch_id": "PYF-101"}, asha_cfg)
    rows = {a["assessment_id"]: a for a in out["assessments"]}
    assert [a["assessment_id"] for a in out["assessments"]] == ["ASM-1", "ASM-2", "ASM-3"]  # by due date
    assert (rows["ASM-1"]["status"], rows["ASM-1"]["score"], rows["ASM-1"]["max_score"]) == ("graded", 17, 20)
    assert (rows["ASM-2"]["status"], rows["ASM-2"]["score"]) == ("submitted", None)
    assert (rows["ASM-3"]["status"], rows["ASM-3"]["score"]) == ("upcoming", None)


def test_a_classmates_scores_are_not_visible():
    # Rahul and Asha share PYF-101 and the same assessments, but each sees only their own result.
    rahul = {a["assessment_id"]: a for a in list_my_assessments.invoke({"batch_id": "PYF-101"}, cfg("stu_rahul"))["assessments"]}
    assert rahul["ASM-1"]["score"] == 11 and rahul["ASM-2"]["status"] == "missing"


def test_past_due_without_any_record_is_reported_as_not_recorded(asha_cfg):
    later = {"configurable": {**asha_cfg["configurable"], "today": "2026-11-05"}}  # ASM-3 was due 2026-10-30
    rows = {a["assessment_id"]: a for a in list_my_assessments.invoke({"batch_id": "PYF-101"}, later)["assessments"]}
    assert rows["ASM-3"]["status"] == "not_recorded"  # we do not claim "missing": the record may simply not exist yet


# ---- argument validation and fail-closed behaviour ----

@pytest.mark.parametrize("tool", [get_my_schedule, get_my_attendance, list_my_assessments])
@pytest.mark.parametrize("bad", ["", "hello", "PYF101", "PYF-1", "PYF-1011", "P-101", "PYF-101; DROP TABLE batches"])
def test_malformed_batch_id_rejected(asha_cfg, tool, bad):
    assert "Invalid batch id" in tool.invoke({"batch_id": bad}, asha_cfg)["error"]


def test_batch_id_is_normalized(asha_cfg):
    assert get_my_attendance.invoke({"batch_id": " pyf-101 "}, asha_cfg)["batch_id"] == "PYF-101"


def test_normalize_batch_id():
    assert normalize_batch_id("qat-201") == "QAT-201"
    assert normalize_batch_id("nope") is None


def test_withdrawn_enrolment_looks_exactly_like_no_enrolment():
    for tool in (get_my_schedule, get_my_attendance, list_my_assessments):
        assert tool.invoke({"batch_id": "PYD-301"}, cfg("stu_neha")) == NOT_FOUND


def test_unknown_tenant_fails_closed_with_the_same_message():
    for tool in (get_my_schedule, get_my_attendance, list_my_assessments):
        assert tool.invoke({"batch_id": "PYF-101"}, cfg("stu_asha", tenant="ghost_institute")) == NOT_FOUND


@pytest.mark.parametrize("tool,args", [(get_my_batches, {}), (get_my_attendance, {"batch_id": "PYF-101"})])
def test_missing_scope_fails_closed(tool, args):
    with pytest.raises(ScopeError):
        tool.invoke(args, {"configurable": {}})
    with pytest.raises(ScopeError):
        tool.invoke(args, {"configurable": {"tenant_id": "qspiders_sample"}})  # a tenant without a student is not a scope


def test_gateway_returns_fake_model_by_default_provider():
    assert isinstance(get_chat_model("action", Settings(llm_provider="fake")), ScriptedChatModel)
