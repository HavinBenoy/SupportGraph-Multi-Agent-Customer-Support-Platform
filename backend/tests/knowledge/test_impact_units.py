"""Unit tests for the impact engine (Part 2): value spellings, change parsing, passages, the deterministic guards that run after ANY model, patches, the LLM adapter with
stub models, and gateway selection. No database and no network."""

import random
from datetime import date

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.knowledge.changes import _lines_edits, check_transition, TRANSITIONS
from app.knowledge.errors import Conflict
from app.knowledge.impact import values
from app.knowledge.impact.discover import Candidate
from app.knowledge.impact.evaluate import (Evaluation, LlmImpactModel, PassageVerdict, RuleImpactModel, apply_edits, gather, is_historical, sibling_conflict, validate)
from app.knowledge.impact.passages import find_passages, with_headings
from app.knowledge.impact.spec import ChangeSpec, Fact, are_siblings, parse_rules, spec_from_edit

TODAY = date(2026, 11, 9)


def spans(text, value):
    return [text[s.start:s.end] for s in values.find_value(text, value)]


# ---- values ------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("The fee is ₹45,000 inclusive.", ["₹45,000"]),
    ("Fee: Rs. 45,000/- only", ["Rs. 45,000/-"]),
    ("INR 45000 per student", ["INR 45000"]),
    ("fee 45k plan", ["45k"]),
    ("fee is 45,000 rupees", ["45,000"]),
    ("the batch number 450001 is unrelated", []),
    ("price 45,0001", []),
    ("₹4,50,000 is ten times", []),
])
def test_money_is_found_in_any_spelling_and_only_where_it_really_is_that_amount(text, expected):
    assert spans(text, "₹45,000") == expected


def test_indian_grouping_matches_the_same_amount():
    assert spans("total ₹4,50,000 payable", "450000") == ["₹4,50,000"]


def test_time_ranges_and_single_times_are_normalised():
    assert spans("Timing 10:00 AM - 1:00 PM daily", "10 AM to 1 PM") == ["10:00 AM - 1:00 PM"]
    assert spans("starts at 7 pm sharp", "7:00 PM") == ["7 pm"]
    assert spans("starts at 7 pm", "8:00 PM") == []


def test_names_match_on_word_boundaries_only():
    assert spans("Dr. Mehta teaches", "Dr. Mehta") == ["Dr. Mehta"]
    assert spans("mehtaa teaches", "Mehta") == []


def test_replacement_is_written_in_the_documents_own_style():
    assert values.render_new("Rs. 45,000/-", "₹45,000", "₹48,000") == "Rs. 48,000/-"
    assert values.render_new("INR 45000", "₹45,000", "₹48,000") == "INR 48000"
    assert values.render_new("45k", "₹45,000", "₹48,000") == "48k"
    assert values.render_new("10:00 AM - 1:00 PM", "10:00 AM - 1:00 PM", "11:00 AM - 2:00 PM") == "11:00 AM - 2:00 PM"


# ---- parsing -----------------------------------------------------------------------------------------------------------------------------
def facts(text):
    sp = parse_rules(text)
    return [(f.old_value, f.new_value, f.effective) for f in sp.facts], sp


def test_parse_fee_with_effective_date():
    f, sp = facts("The Python Full Stack course fee changed from ₹45,000 to ₹48,000 starting 1 December.")
    assert f == [("₹45,000", "₹48,000", "1 December")] and {"python", "full", "stack"} <= set(sp.entity_terms)


@pytest.mark.parametrize("text", [
    "Dr. Mehta replaced by Dr. Rao as the Python Full Stack lead trainer.",
    "Replace Dr. Mehta with Dr. Rao for Python Full Stack",
    "Python Full Stack faculty changed from Dr. Mehta to Dr. Rao",
])
def test_parse_names_without_swallowing_the_course(text):
    f, sp = facts(text)
    assert f == [("Dr. Mehta", "Dr. Rao", None)] and "python" in sp.entity_terms


def test_a_description_without_old_and_new_values_yields_no_facts():
    assert facts("We changed the fee.")[0] == []
    assert facts("Update the weekend timings please")[0] == []
    assert facts("")[0] == []


def test_all_every_means_no_need_to_ask():
    assert parse_rules("Change every weekend batch timing from 10 AM to 11 AM").wants_all is True
    assert parse_rules("Change the weekend batch timing from 10 AM to 11 AM").wants_all is False


def test_workflow_a_derives_facts_from_the_staff_edit():
    diff = [{"op": "equal", "text": "The fee is "}, {"op": "delete", "text": "₹45,000"}, {"op": "insert", "text": "₹48,000"}, {"op": "equal", "text": " per student."}]
    sp = spec_from_edit("Python Full Stack Course Fees", "Courses/Python", diff, None)
    assert [(f.old_value, f.new_value) for f in sp.facts] == [("₹45,000", "₹48,000")] and "python" in sp.entity_terms


# ---- passages / siblings -----------------------------------------------------------------------------------------------------------------
def test_passages_carry_their_heading_chain_and_skip_headings():
    c = "# Fees\n\n## Current\nThe fee is ₹45,000.\n\n## Old\n### Archive\nWas ₹45,000.\n"
    ps = find_passages(c, "₹45,000")
    assert [(p.heading, p.text) for p in ps] == [("Fees > Current", "The fee is ₹45,000."), ("Fees > Old > Archive", "Was ₹45,000.")]
    assert all(c[p.start:p.end] == p.text for p in ps)
    assert [h for *_, h in with_headings(c)][0] == "Fees"


def test_siblings():
    assert are_siblings("Data Science Weekend Batch A", "Data Science Weekend Batch B")
    assert are_siblings("weekend batch", "weekday batch")
    assert are_siblings("Online course", "Classroom course")
    assert not are_siblings("Python Fees", "Python FAQ")
    assert not are_siblings("Weekend Batch A", "Weekend Batch A")


# ---- the guards that run after ANY model -------------------------------------------------------------------------------------------------
def cand(content, title="Python Full Stack Course Fees", path="Courses/Python"):
    return Candidate("d1", title, path, "fees", [], 1, "x", content)


SPEC = ChangeSpec(summary="fee", facts=[Fact(attribute="fee", old_value="₹45,000", new_value="₹48,000")], entity_terms=["python", "full", "stack"], key_terms=["python"])
S = Settings()
DOC = "# Fees\n\n## Current fee\nThe Python Full Stack fee is ₹45,000.\n"


def judge(content, verdicts, spec=SPEC, title="Python Full Stack Course Fees", path="Courses/Python"):
    c = cand(content, title, path)
    ctx = gather(spec, c, S, None)
    return validate(spec, c, ctx, Evaluation(verdicts=verdicts), S, TODAY), ctx


def pv(verdict="definite", conf=0.9, rep="The Python Full Stack fee is ₹48,000.", n=0, reason="x"):
    return PassageVerdict(passage=n, verdict=verdict, confidence=conf, reason=reason, replacement=rep)


def test_a_valid_proposal_becomes_a_definite_patch_on_the_exact_line():
    res, _ = judge(DOC, [pv()])
    assert res.classification == "definite" and len(res.edits) == 1
    assert apply_edits(DOC, [{"start": e.start, "end": e.end, "original": e.original, "replacement": e.replacement} for e in res.edits]) == DOC.replace("45,000", "48,000")


@pytest.mark.parametrize("rep,flag", [
    ("The Python Full Stack fee is ₹45,000.", "unsupported"),               # identical
    ("The Python Full Stack fee will rise.", "unsupported"),                  # lacks the new value
    ("The fee is ₹48,000 (was ₹45,000).", "unsupported"),                    # still has the old value
    ("The fee is ₹48,000.\nExtra line.", "unsupported"),                      # more than one line
    ("x" * 600 + " ₹48,000", "unsupported"),                                  # absurdly long
])
def test_unsupported_replacements_are_downgraded_never_applied(rep, flag):
    res, _ = judge(DOC, [pv(rep=rep)])
    assert res.edits == [] and res.classification == "potential" and res.flags[flag] == 1 and res.confidence <= 0.3


def test_a_model_that_proposes_no_replacement_gets_potential_with_no_patch():
    res, _ = judge(DOC, [pv(rep=None)])
    assert res.edits == [] and res.classification == "potential"


def test_passages_the_model_invented_are_ignored():
    res, _ = judge(DOC, [pv(n=7), pv(n=0), pv(n=0, rep="The Python Full Stack fee is ₹49,000.")])
    assert len(res.edits) == 1 and res.edits[0].replacement.endswith("₹48,000.")           # the repeated verdict for passage 0 is ignored too


def test_low_confidence_definite_becomes_potential():
    res, _ = judge(DOC, [pv(conf=0.5)])
    assert res.classification == "potential" and any("threshold" in u for u in res.uncertainty) and len(res.edits) == 1


@pytest.mark.parametrize("content", [
    "# Fee History\n\nThe Python Full Stack fee is ₹45,000.\n",                                   # historical heading
    "# Fees\n\nIn 2019 the Python Full Stack fee was ₹45,000.\n",                                  # explicit past year
    "# Fees\n\nThe Python Full Stack fee was ₹45,000 until March.\n",                              # "was"/"until"
    "# Fees\n\n## Previous fees\nThe Python Full Stack fee is ₹45,000.\n",
])
def test_historical_records_are_never_changed_even_if_the_model_says_so(content):
    res, _ = judge(content, [pv()])
    assert res.edits == [] and res.classification == "related_no_change" and res.flags["historical"] == 1


def test_a_passage_about_a_different_subject_is_never_changed():
    res, _ = judge("# Fees\n\nThe Java Full Stack fee is ₹45,000.\n", [pv(rep="The Java Full Stack fee is ₹48,000.")], title="Java Fees", path="Courses/Java")
    assert res.edits == [] and res.flags["scope"] == 1


def test_sibling_alternative_is_a_different_subject():
    sp = parse_rules("Data Science weekend batch timing changed from 10:00 AM - 1:00 PM to 11:00 AM - 2:00 PM")
    assert sibling_conflict(sp, "Data Science Batches > Weekday batch meets 10:00 AM") and not sibling_conflict(sp, "Data Science Batches > Weekend batch meets")


def test_is_historical():
    assert is_historical("Fee history", "x", TODAY) and is_historical("", "In 2024 it cost", TODAY) and not is_historical("Current fee", "In 2026 it costs", TODAY)


def test_a_patch_never_applies_to_a_document_that_moved_on():
    e = [{"start": 0, "end": 3, "original": "abc", "replacement": "x"}]
    assert apply_edits("abcdef", e) == "xdef"
    with pytest.raises(ValueError):
        apply_edits("abXdef", e)
    with pytest.raises(ValueError):
        apply_edits("abcdef", [{"start": 0, "end": 3, "original": "abc", "replacement": "x"}, {"start": 2, "end": 4, "original": "cd", "replacement": "y"}])


def test_line_patches_round_trip_for_any_edit():
    rng = random.Random(7)
    for _ in range(2000):
        a = "\n".join(rng.choice("abcd") for _ in range(rng.randint(1, 8)))
        b = "\n".join(rng.choice("abcd") for _ in range(rng.randint(1, 8)))
        assert apply_edits(a, _lines_edits(a, b)) == b


# ---- rule model --------------------------------------------------------------------------------------------------------------------------
def test_rule_model_is_deterministic():
    c = cand(DOC)
    ctx = gather(SPEC, c, S, None)
    m = RuleImpactModel()
    assert m.evaluate(SPEC, c, ctx) == m.evaluate(SPEC, c, ctx)


# ---- LLM adapter with stub models --------------------------------------------------------------------------------------------------------
class Chain:
    def __init__(self, fn):
        self.fn = fn

    def invoke(self, messages):
        return self.fn(messages)


class StubChat:
    def __init__(self, spec=None, ev=None):
        self.spec, self.ev, self.calls = spec, ev, []

    def with_structured_output(self, schema):
        def run(messages):
            self.calls.append(schema.__name__)
            out = self.spec if schema is ChangeSpec else self.ev
            if isinstance(out, Exception):
                raise out
            return out
        return Chain(run)


def test_llm_adapter_uses_a_valid_structured_answer():
    c = cand(DOC)
    ctx = gather(SPEC, c, S, None)
    chat = StubChat(ev=Evaluation(verdicts=[pv()]))
    out = LlmImpactModel(chat, name="stub").evaluate(SPEC, c, ctx)
    assert out.verdicts[0].replacement.endswith("₹48,000.") and chat.calls == ["Evaluation"]


@pytest.mark.parametrize("bad", [ValidationError.from_exception_data("X", []), ValueError("bad json"), RuntimeError("ollama down"), TimeoutError()])
def test_llm_failure_or_invalid_output_means_potential_for_staff_to_confirm_never_definite(bad):
    """A model outage no longer hides an affected passage: the grounded rule-engine proposal is offered as POTENTIAL (low confidence, says where it came from) for staff to
    accept, edit or discard. It is never 'definite' and never applied without the human decision."""
    c = cand(DOC)
    ctx = gather(SPEC, c, S, None)
    out = LlmImpactModel(StubChat(ev=bad), name="stub").evaluate(SPEC, c, ctx)
    res = validate(SPEC, c, ctx, out, S, TODAY)
    assert res.classification == "potential" and res.confidence <= 0.5
    assert all(e.verdict == "potential" and "rule engine" in e.reason and "48,000" in e.replacement for e in res.edits) and len(res.edits) == 1


def test_llm_parse_falls_back_to_the_rule_parser():
    desc = "The Python Full Stack course fee changed from ₹45,000 to ₹48,000."
    assert LlmImpactModel(StubChat(spec=RuntimeError("down")), name="stub").parse(desc).actionable()[0].new_value == "₹48,000"
    assert LlmImpactModel(StubChat(spec=ChangeSpec()), name="stub").parse(desc).actionable()                      # an empty model answer also falls back
    # a value the model made up (it is not in the description) is rejected, and the rule parser's reading of the plain sentence wins
    got = LlmImpactModel(StubChat(spec=ChangeSpec(facts=[Fact(old_value="₹45,000", new_value="₹49,000")])), name="stub").parse(desc)
    assert got.actionable()[0].new_value == "₹48,000" and "python" in got.entity_terms
    # a sentence the rules cannot read: the model's answer is used when every value in it occurs in the description
    odd = "Priya mam takes over as the Python Full Stack lead trainer from Dr. Mehta."
    good = ChangeSpec(facts=[Fact(attribute="trainer", old_value="Dr. Mehta", new_value="Priya mam")], entity_terms=["python full stack"])
    got = LlmImpactModel(StubChat(spec=good), name="stub").parse(odd)
    assert (got.actionable()[0].old_value, got.actionable()[0].new_value) == ("Dr. Mehta", "Priya mam") and got.entity_terms == ["python", "full", "stack"]
    bad = ChangeSpec(facts=[Fact(attribute="trainer", old_value="Dr. Mehta", new_value="Someone Else")], entity_terms=["python"])
    assert not LlmImpactModel(StubChat(spec=bad), name="stub").parse(odd).actionable()


# ---- gateway ---------------------------------------------------------------------------------------------------------------------------
def test_gateway_selection_is_configurable():
    from app.llm.gateway import get_impact_model
    s = Settings()
    s.kb_ai_mode, s.llm_provider = "auto", "fake"
    assert isinstance(get_impact_model(s), RuleImpactModel)
    s.kb_ai_mode = "rules"
    s.llm_provider = "ollama"
    assert isinstance(get_impact_model(s), RuleImpactModel)
    s.kb_ai_mode, s.llm_model, s.llm_model_impact = "llm", "qwen3:1.7b", "qwen3:4b"
    m = get_impact_model(s)
    assert isinstance(m, LlmImpactModel) and m.name == "ollama:qwen3:4b"


# ---- state machine ---------------------------------------------------------------------------------------------------------------------
def test_state_machine_is_total_and_closed():
    from app.db.models import KB_REQUEST_STATUSES
    assert set(TRANSITIONS) == set(KB_REQUEST_STATUSES)
    assert all(t in KB_REQUEST_STATUSES for to in TRANSITIONS.values() for t in to)
    for dead in ("rejected", "published", "cancelled", "applied"):
        assert TRANSITIONS[dead] == set()
    for frm in KB_REQUEST_STATUSES:
        for to in KB_REQUEST_STATUSES:
            if to in TRANSITIONS[frm]:
                check_transition(frm, to)
            else:
                with pytest.raises(Conflict):
                    check_transition(frm, to)


def test_publishing_is_only_reachable_through_processing_and_approval():
    inbound = {t: {f for f, to in TRANSITIONS.items() if t in to} for t in TRANSITIONS}
    assert inbound["processing"] == {"approved", "failed"} and inbound["publishing"] == {"processing"} and inbound["published"] == {"publishing"}
    assert inbound["approved"] == {"submitted", "under_review"}


def test_a_sentence_ending_dot_is_not_part_of_the_time():
    t = "The batch meets on Saturday, 10:00 AM - 1:00 PM."
    got = spans(t, "10:00 AM - 1:00 PM")
    assert got == ["10:00 AM - 1:00 PM"]
    assert values.render_new(got[0], "10:00 AM - 1:00 PM", "11:00 AM - 2:00 PM") == "11:00 AM - 2:00 PM"
    t2 = "at 10 a.m. - 1 p.m. daily"
    g2 = spans(t2, "10 AM - 1 PM")
    assert g2 == ["10 a.m. - 1 p.m."] and values.render_new(g2[0], "10 AM - 1 PM", "11 AM - 2 PM") == "11 a.m. - 2 p.m."
