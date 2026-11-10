"""Generalized section editing (Part 2 final): adaptive context, free-form revisions and their validation, and the decision handling inside the analysis.

NO real model is used here: the chat is a scripted stub that returns the revised section a good (or a bad) model might return. What a real Qwen does with the same prompt is tested
separately, by hand, with `scripts/judge_probe.py` (see docs/PART2_FINAL.md)."""

from datetime import date

import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.knowledge.impact import context as C
from app.knowledge.impact import discover as D
from app.knowledge.impact import edit as E
from app.knowledge.impact import judge as J
from app.knowledge.impact.analyse import generalize, vague
from app.knowledge.impact.discover import Candidate
from app.knowledge.impact.evaluate import LlmImpactModel, apply_edits, gather, validate

TODAY = date(2026, 11, 10)
DS = """# Data Science

## Faculty

Machine Learning faculty is Santhosh sir.
He has a work experience of 3 years. Previously he worked in IT.

## Eligibility

- Any graduate
- Age under 30
- Online classes only

## Fees

| Course | Fee |
|---|---|
| Data Science | 15000 |
"""
EXP = "change the work experience of santhosh sir from 3 years to 5 years"


class Chat:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def with_structured_output(self, _):
        return self

    def invoke(self, msgs):
        self.calls.append(msgs[0].content)
        out = self.reply(msgs[0].content)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, AIMessage) else AIMessage(out)


@pytest.fixture(autouse=True)
def _fresh_cache():
    J._CACHE.clear()


def fenced(text):
    return "I thought about it.\n```text\n" + text + "\n```"


def run(doc, desc, reply, check=None, **kw):
    s = Settings(kb_ai_discovery="v2", kb_ai_judge="all", **kw)
    chat = Chat(reply)
    m = LlmImpactModel(chat, name="stub", judge_chat=chat, check_chat=check)
    spec = m.parse(desc)
    chat.calls.clear()                                                  # parsing may use the stub too; only the editor's calls are counted
    if not spec.actionable():
        spec = generalize(spec, desc)
        assert spec is not None, "a vague description must not reach the editor"
    c = Candidate(doc_id="ds", title="Data Science", path="", category="courses", tags=[], version=1, checksum="x", content=doc)
    cands, _ = D.rank([c], s, spec, groups={})
    assert cands, "discovery must nominate the document"
    cc = cands[0]
    ctx = m.judge(spec, cc, gather(spec, cc, s, None), s)
    res = validate(spec, cc, ctx, m.evaluate(spec, cc, ctx), s, TODAY)
    return chat, res, cc


def hunk_passage(res):
    return next(p for p in res.passages if "change" in p)


def patched(doc, res):
    return apply_edits(doc, [{"start": e.start, "end": e.end, "original": e.original, "replacement": e.replacement} for e in res.edits])


# ===================================================================================================================== adaptive context
def test_budget_is_the_smallest_of_input_output_and_window_limits():
    # explicit values: a developer's own .env (e.g. an older SG_KB_AI_JUDGE_MAX_TOKENS=2500) must not change what the defaults of this test mean
    b = C.Budget.from_settings(Settings(kb_ai_context_tokens=1800, kb_ai_judge_max_tokens=4096, kb_ai_judge_num_ctx=8192, kb_ai_judge_thinking=True, kb_ai_thinking_reserve_tokens=1500))
    assert b.section_tokens == 1800 and b.limited_by == "input" and b.num_ctx == 8192 and b.max_output == 4096
    small_out = C.Budget.from_settings(Settings(kb_ai_context_tokens=6000, kb_ai_judge_max_tokens=2000, kb_ai_judge_thinking=True, kb_ai_thinking_reserve_tokens=1500))
    assert small_out.limited_by == "output" and small_out.section_tokens < 500                      # the answer repeats the section: it must fit in what the model may write
    small_win = C.Budget.from_settings(Settings(kb_ai_context_tokens=6000, kb_ai_judge_max_tokens=4096, kb_ai_judge_num_ctx=4096))
    assert small_win.section_tokens <= 4096 - 4096 - C.PROMPT_OVERHEAD_TOKENS or small_win.section_tokens == 150    # prompt + output cap must fit in num_ctx (floor 150)
    big = C.Budget.from_settings(Settings(kb_ai_context_tokens=6000, kb_ai_judge_max_tokens=12000, kb_ai_judge_num_ctx=32768, kb_ai_judge_thinking=True, kb_ai_thinking_reserve_tokens=1500))
    assert big.section_tokens == 6000


def test_a_short_document_is_sent_whole_with_its_title_and_all_headings():
    (sec,) = C.sections_for(DS, [DS.index("He has")], C.Budget.from_settings(Settings()))
    assert sec.kind == "document" and sec.text == DS.strip("\n") and sec.heading == "Data Science"


BIG = ("# Course\n\n## Faculty\n\nMachine Learning faculty is Santhosh sir.\nHe has 3 years.\n\n## Eligibility\n\n- Any graduate\n- Age under 30\n\n## Fees\n\n| Course | Fee |\n|---|---|\n"
       + "\n".join(f"| C{i} | {i * 1000} |" for i in range(80)) + "\n\n## Notes\n\n" + "\n\n".join(f"Paragraph {i}. " + "filler words " * 12 for i in range(30)))


def test_a_large_document_is_cut_at_its_logical_section_not_at_a_fixed_line_count():
    b = C.Budget(400, 400, 4096, 8192, "input")
    (sec,) = C.sections_for(BIG, [BIG.index("He has")], b)
    assert sec.kind == "subtree" and sec.text.startswith("## Faculty") and "## Eligibility" not in sec.text and not sec.partial
    (el,) = C.sections_for(BIG, [BIG.index("- Age")], b)
    assert el.text.startswith("## Eligibility") and el.text.endswith("- Age under 30")


def test_an_oversized_section_is_split_on_blocks_and_a_table_row_keeps_its_header():
    b = C.Budget(200, 200, 4096, 8192, "input")
    at = BIG.index("| C40 ")
    (sec,) = C.sections_for(BIG, [at], b)
    assert sec.partial and sec.kind == "blocks" and at in {r.start for r in sec.rows}
    assert "| C40 | 40000 |" in sec.text and sec.header.startswith("| Course | Fee |") and sec.tokens <= 220
    para = BIG.index("Paragraph 12.")
    (p,) = C.sections_for(BIG, [para], b)
    assert "Paragraph 12." in p.text and p.partial and all(r.text.strip() == "" or r.text.startswith("Paragraph") or r.text.startswith("##") for r in p.rows)


def test_a_question_stays_with_its_answer_and_a_lead_in_with_its_list():
    doc = "# FAQ\n\n" + "\n\n".join(f"Q: Question {i}?\n\nA: Answer {i}. " + "x " * 30 for i in range(12)) + "\n\nThe eligibility is:\n- one\n- two\n"
    rows = C.with_headings(doc)
    blocks = C.blocks_of(rows)
    qa = [b for b in blocks if b.kind == "qa"]
    assert len(qa) == 12
    lst = [b for b in blocks if b.kind == "list"][0]
    assert rows[lst.lo][2] == "The eligibility is:"
    a = doc.index("A: Answer 5")
    (sec,) = C.sections_for(doc, [a], C.Budget(150, 150, 4096, 8192, "input"))
    assert "Q: Question 5?" in sec.text and "A: Answer 5." in sec.text


def test_windows_of_the_same_section_are_merged_and_never_overlap():
    starts = [BIG.index("He has"), BIG.index("Machine Learning faculty"), BIG.index("- Age"), BIG.index("| C3 ")]
    secs = C.sections_for(BIG, starts, C.Budget(400, 400, 4096, 8192, "input"))
    assert len(secs) == 2                                            # Faculty + Eligibility touch and fit together (one request); the table window is separate
    spans = [(s.start, s.end) for s in secs]
    assert all(spans[i][1] < spans[i + 1][0] for i in range(len(spans) - 1))
    assert sum(len(s.focus) for s in secs) == 4 and secs[0].text.startswith("## Faculty") and "- Age under 30" in secs[0].text


# ================================================================================================================ free-form validation (edit.py)
def revise(doc, desc, answer, news=(), olds=(), at="He has"):
    (sec,) = C.sections_for(doc, [doc.index(at)], C.Budget.from_settings(Settings()))
    return E.revise(sec, answer, E.Rules(desc, "Data Science", tuple(news), tuple(olds), TODAY))


def test_scenario_1_replace_a_value_keeping_the_history():
    o = revise(DS, EXP, DS.replace("3 years", "5 years"), ["5 years"], ["3 years"])
    assert o.state == "revised" and len(o.hunks) == 1 and o.hunks[0].kind == "modify"
    assert o.hunks[0].replacement == "He has a work experience of 5 years. Previously he worked in IT."


def test_scenario_2_add_a_qualification_that_is_absent():
    o = revise(DS, "add that santhosh sir holds an MTech in AI", DS.replace("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI."))
    assert o.state == "revised" and o.hunks[0].kind == "add" and o.hunks[0].added == ["He holds an MTech in AI."]
    assert o.hunks[0].original.endswith("Previously he worked in IT.")                          # anchored on the line before: the patch span is never empty


def test_scenario_3_online_to_hybrid_in_a_list_item():
    o = revise(DS, "change online classes to hybrid classes", DS.replace("- Online classes only", "- Hybrid classes (online and offline)"), ["hybrid classes"], ["online classes"], "- Online")
    assert o.state == "revised" and o.hunks[0].replacement == "- Hybrid classes (online and offline)"


def test_scenario_4_remove_an_outdated_restriction_and_leave_the_other_items():
    o = revise(DS, "remove the age restriction from eligibility", DS.replace("- Age under 30\n", ""), at="- Age")
    assert o.state == "revised" and o.hunks[0].kind == "remove" and o.hunks[0].removed == ["- Age under 30"]
    assert o.hunks[0].replacement == "- Any graduate"                                           # the neighbouring item is the anchor, kept as it was


def test_scenario_5_add_a_new_course_row_and_its_details():
    o = revise(DS, "add a new course Cloud Computing with fee 20000", DS.replace("| Data Science | 15000 |", "| Data Science | 15000 |\n| Cloud Computing | 20000 |"), at="| Data Science")
    assert o.state == "revised" and o.hunks[0].added == ["| Cloud Computing | 20000 |"]
    bad = revise(DS, "add a new course Cloud Computing with fee 20000", DS.replace("| Data Science | 15000 |", "| Data Science | 15000 |\n| Cloud Computing | 25000 |"), at="| Data Science")
    assert bad.state == "invalid" and "figures" in bad.why                                      # an invented fee is never accepted
    cols = revise(DS, "add a new course Cloud Computing with fee 20000", DS.replace("| Data Science | 15000 |", "| Data Science | 15000 |\n| Cloud Computing | 20000 | extra |"), at="| Data Science")
    assert cols.state == "invalid" and "columns" in cols.why


def test_scenario_6_several_related_statements_in_one_section_are_separate_hunks():
    ans = DS.replace("3 years", "5 years").replace("Machine Learning faculty is Santhosh sir.", "Machine Learning and Deep Learning faculty is Santhosh sir.")
    o = revise(DS, "santhosh sir now has 5 years of experience and also teaches deep learning", ans, ["5 years"], ["3 years"], at="Machine Learning faculty")
    assert o.state == "revised" and [h.kind for h in o.hunks] == ["modify", "modify"]         # line for line: each statement is its own patch (and judged on its own)
    assert "Deep Learning" in o.hunks[0].replacement and "5 years" in o.hunks[1].replacement


def test_scenario_7_historical_sentences_must_survive_and_history_sections_are_not_edited():
    gone = revise(DS, EXP, DS.replace("He has a work experience of 3 years. Previously he worked in IT.", "He has a work experience of 5 years."), ["5 years"], ["3 years"])
    assert gone.state == "invalid" and "historical" in gone.why
    rewritten = revise(DS, EXP, DS.replace("3 years. Previously he worked in IT.", "5 years. Earlier he worked in HR."), ["5 years"], ["3 years"])
    assert rewritten.state == "invalid"
    hist = "# Faculty\n\n## Archive\n\nSanthosh sir had 3 years of experience in 2020.\n"
    o = revise(hist, EXP, hist.replace("3 years", "5 years"), ["5 years"], ["3 years"], "Santhosh")
    assert o.state == "invalid" and "histor" in o.why


def test_scenario_8_an_unrelated_section_is_returned_unchanged_and_noise_is_not_a_change():
    assert revise(DS, "change online classes to hybrid", DS).state == "unchanged"
    assert revise(DS, "change online classes to hybrid", DS.replace("\n\n", "\n").replace("sir.\n", "sir.   \n")).state == "unchanged"      # whitespace only


def test_scenario_9_unrelated_edits_deletions_headings_and_duplicates_are_dropped():
    desc = "change online classes to hybrid"
    good = DS.replace("- Online classes only", "- Hybrid classes")
    for mutated, word in [(good.replace("15000", "16000"), "figures"), (good.replace("## Fees", "## Charges"), "heading"), (good.replace("- Any graduate\n", ""), "deletes"),
                          (good.replace("- Any graduate", "- Any graduate\n- Any graduate"), "duplicates"), (good.replace("| Course | Fee |", "| Course | Price |"), "table header")]:
        o = revise(DS, desc, mutated, ["hybrid classes"], ["online classes"], "- Online")
        assert o.state == "revised" and len(o.hunks) == 1 and o.hunks[0].replacement == "- Hybrid classes" and (any(word in x for x in o.rejected) or (word == "duplicates" and any("does not concern" in x for x in o.rejected))), (word, o.rejected)
        bad = revise(DS, desc, mutated.replace("- Hybrid classes", "- Online classes only"), ["hybrid classes"], ["online classes"], "- Online")
        if word == "deletes":                       # with no matching change at all, a deletion NEXT to the candidate line is kept only as a flagged question for a person
            assert bad.state == "revised" and any("only by meaning" in f for h in bad.hunks for f in h.flags)
            continue
        assert bad.state == "invalid" and word in bad.why, (word, bad.why)
    mixed = revise(DS, desc, DS.replace("- Online classes only", "- Hybrid classes").replace("- Any graduate\n", ""), ["hybrid classes"], ["online classes"], "- Online")
    assert mixed.state == "revised" and len(mixed.hunks) == 1 and any("dropped" in r for r in mixed.review)      # the good part is kept, the bad part reported and flagged


def test_scenario_10_headings_lists_tables_and_surrounding_content_survive_in_the_patch():
    ans = DS.replace("- Online classes only", "- Hybrid classes")
    (sec,) = C.sections_for(DS, [DS.index("- Online")], C.Budget.from_settings(Settings()))
    o = E.revise(sec, ans, E.Rules("change online classes to hybrid classes", "Data Science", ("hybrid classes",), ("online classes",), TODAY))
    patched_doc = apply_edits(DS, [{"start": h.start, "end": h.end, "original": h.original, "replacement": h.replacement} for h in o.hunks])
    assert patched_doc == ans and patched_doc.count("## ") == 3 and "| Course | Fee |\n|---|---|" in patched_doc


def test_failed_responses_are_not_mistaken_for_unchanged():
    assert E.extract("no block").state == "none" and E.extract("").state == "none" and E.extract(None).state == "none"
    assert E.extract("```text\nhalf an ans").state == "truncated" and E.extract("x", "length").state == "truncated"
    assert E.extract("<think>still thinking").state == "truncated"
    assert E.extract("```text\n\n```").state == "empty"
    assert E.extract("```text\nChange request: x\n<section>\n```").state == "malformed"
    assert E.extract("```text\nA\n```").text == "A"
    short = revise(DS, "change online classes to hybrid", "# Data Science\n\n## Faculty")
    assert short.state == "invalid" and "incomplete" in short.why


def test_a_rewrite_of_most_of_the_section_goes_to_a_person():
    doc = "# T\n\n" + "\n".join(f"- Online class item {i} for batch" for i in range(6)) + "\n"
    ans = "# T\n\n" + "\n".join(f"- Hybrid class item {i} for batch" for i in range(6)) + "\n"
    (sec,) = C.sections_for(doc, [doc.index("- Online class item 2")], C.Budget.from_settings(Settings()))
    o = E.revise(sec, ans, E.Rules("change online class to hybrid class", "T", (), (), TODAY))
    assert o.state == "revised" and any("large part" in r for r in o.review)


# ================================================================================================================== decisions in the analysis
def test_value_change_the_model_agrees_stays_definite_and_the_prompt_is_the_general_instruction():
    chat, r, _ = run(DS, EXP, lambda p: fenced(DS.replace("3 years", "5 years").strip("\n")))
    assert r.classification == "definite" and r.passages[0]["judge"] == "agrees"
    assert "understand what the requested change means, then make the corresponding modification wherever it applies" in chat.calls[0]
    assert "completely unchanged" in chat.calls[0] and "Preserve all unrelated information" in chat.calls[0] and "Change nothing except" not in chat.calls[0]
    assert EXP in chat.calls[0] and "<section>" in chat.calls[0] and "## Eligibility" in chat.calls[0]                  # the complete section, not isolated lines


def test_value_change_with_a_richer_model_revision_is_proposed_not_definite():
    ans = DS.replace("3 years.", "5 years and teaches senior batches.").strip("\n")
    _, r, cc = run(DS, EXP, lambda p: fenced(ans))
    assert r.edits and r.classification == "potential" and "different revision" in hunk_passage(r)["reason"]


def test_general_add_change_creates_a_potential_edit_which_a_human_must_approve():
    desc = "santhosh sir now also holds an MTech in AI"
    chat, r, cc = run(DS, desc, lambda p: fenced(DS.replace("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI.").strip("\n")))
    assert r.classification == "potential" and len(r.edits) == 1 and r.edits[0].kind == "add" and hunk_passage(r)["kind"] != "definite"
    assert "He holds an MTech in AI." in patched(DS, r) and patched(DS, r).count("## ") == 3
    assert hunk_passage(r)["change"]["kind"] == "add"


def test_general_remove_change_and_unrelated_section_unchanged():
    desc = "remove the age restriction from eligibility"
    _, r, _ = run(DS, desc, lambda p: fenced(DS.replace("- Age under 30\n", "").strip("\n")))
    assert [e.kind for e in r.edits] == ["remove"] and "Age under 30" not in patched(DS, r) and "Any graduate" in patched(DS, r)
    J._CACHE.clear()
    _, r2, _ = run(DS, desc, lambda p: fenced(DS.strip("\n")))
    assert not r2.edits and r2.classification in ("related_no_change", "not_relevant")                  # not applicable: the original is preserved


def test_model_and_rules_disagree_is_a_question_for_a_person():
    _, r, _ = run(DS, EXP, lambda p: fenced(DS.strip("\n")))
    p = r.passages[0]
    assert p["judge"] == "disagrees" and r.classification == "potential" and "does NOT apply" in p["reason"] and r.edits[0].replacement.startswith("He has a work experience of 5 years")


def test_invalid_or_cut_off_output_never_creates_an_edit():
    for reply in (lambda p: "I cannot help", lambda p: "```text\nhalf", lambda p: RuntimeError("ollama down"),
                  lambda p: fenced("# Data Science\n\n## Faculty"), lambda p: fenced(DS.replace("3 years", "5 years").replace("15000", "99999"))):
        _, r, _ = run(DS, "santhosh sir now also holds an MTech in AI", reply)
        assert not r.edits, reply


def test_a_truncated_answer_is_detected_from_the_finish_reason():
    msg = AIMessage("```text\n" + DS.strip("\n") + "\n```", response_metadata={"done_reason": "length"})
    assert E.extract(msg.content, J._finish(msg)).state == "ok"                                        # a complete block is complete even when the cap was reached
    assert E.extract("thinking only", None).state == "none"
    assert E.extract("some text", "length").state == "truncated"


def test_the_second_check_flags_a_revision_it_disagrees_with_and_never_approves_alone():
    ans = fenced(DS.replace("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI.").strip("\n"))
    check = Chat(lambda p: "FAIL - the qualification was not requested")
    _, r, _ = run(DS, "santhosh sir now also holds an MTech in AI", lambda p: ans, check=check, kb_ai_semantic_check="all")
    assert len(check.calls) == 1 and r.edits and r.classification == "potential" and "second check" in hunk_passage(r)["reason"]
    ok = Chat(lambda p: "PASS - matches")
    J._CACHE.clear()
    _, r2, _ = run(DS, "santhosh sir now also holds an MTech in AI", lambda p: ans, check=ok, kb_ai_semantic_check="all")
    assert r2.edits and "second check" not in hunk_passage(r2)["reason"]
    assert E.parse_semantic("<think>PASS</think>\nUNSURE - cannot tell") == ("unsure", "cannot tell") and E.parse_semantic("blah") == ("none", "")


def test_a_model_found_change_in_a_line_the_rules_never_nominated_is_reviewable():
    doc = "# Course\n\nMachine Learning faculty: Rao sir\n\n## Eligibility\n- Online classes only\n"
    desc = "change online classes to hybrid classes"
    _, r, _ = run(doc, desc, lambda p: fenced(doc.replace("Online classes only", "Hybrid classes").strip("\n")))
    assert r.edits and r.classification == "potential"


def test_calls_are_capped_cached_and_a_second_run_costs_nothing():
    chat, r, _ = run(DS, "santhosh sir now also holds an MTech in AI", lambda p: fenced(DS.strip("\n")))
    chat2, _, _ = run(DS, "santhosh sir now also holds an MTech in AI", lambda p: fenced("zzz"))
    assert len(chat.calls) == 1 and not chat2.calls
    many = "\n\n".join(f"## S{i}\n\nMachine Learning faculty line {i} " + "filler " * 40 for i in range(10))
    chat3, r3, _ = run(many, "santhosh sir now also teaches machine learning", lambda p: fenced("x"), kb_ai_max_sections=2, kb_ai_context_tokens=300, kb_ai_edit_retries=0)
    assert len(chat3.calls) <= 2 and any("not read" in u for u in r3.uncertainty)


def test_vague_requests_are_not_acted_on():
    assert vague("We changed the Python Full Stack fee.") and vague("update") and vague("the eligibility")
    assert not vague("remove the age restriction from eligibility") and not vague("santhosh sir now also holds an MTech in AI")
    from app.knowledge.impact.spec import ChangeSpec
    assert generalize(ChangeSpec(), "We changed the Python Full Stack fee.") is None
    g = generalize(ChangeSpec(), "remove the age restriction from eligibility")
    assert g is not None and g.general and "eligibility" in g.entity_terms and "age" in g.entity_terms


def test_judge_is_a_noop_without_a_judge_chat_or_when_switched_off():
    s = Settings(kb_ai_discovery="v2", kb_ai_judge="off")
    chat = Chat(lambda p: fenced("x"))
    m = LlmImpactModel(chat, name="stub", judge_chat=chat)
    spec = m.parse(EXP)
    c = Candidate(doc_id="ds", title="T", path="", category="", tags=[], version=1, checksum="x", content=DS)
    cands, _ = D.rank([c], s, spec, groups={})
    ctx = gather(spec, cands[0], s, None)
    assert m.judge(spec, cands[0], ctx, s) is ctx and not chat.calls


def test_gateway_separates_the_output_limit_from_the_ollama_window():
    from app.llm.gateway import get_check_chat, get_judge_chat
    assert get_judge_chat(Settings(kb_ai_judge="off")) is None and get_check_chat(Settings(kb_ai_judge="off")) is None
    pytest.importorskip("langchain_ollama")
    chat = get_judge_chat(Settings(llm_provider="ollama", kb_ai_judge="uncertain", kb_ai_judge_max_tokens=3000, kb_ai_judge_num_ctx=10000))
    assert chat.reasoning is True and chat.num_predict == 3000 and chat.num_ctx == 10000
    chk = get_check_chat(Settings(llm_provider="ollama"))
    assert chk.reasoning is False and chk.num_predict == 200


def test_a_general_change_without_the_section_editor_is_reported_not_crashed():
    s = Settings(kb_ai_discovery="v2", kb_ai_judge="off")
    chat = Chat(lambda p: fenced("x"))
    m = LlmImpactModel(chat, name="stub", judge_chat=None)
    spec = generalize(m.parse("remove the age restriction from eligibility"), "remove the age restriction from eligibility")
    c = Candidate(doc_id="ds", title="Data Science", path="", category="courses", tags=[], version=1, checksum="x", content=DS)
    cands, _ = D.rank([c], s, spec, groups={})
    ctx = m.judge(spec, cands[0], gather(spec, cands[0], s, None), s)
    res = validate(spec, cands[0], ctx, m.evaluate(spec, cands[0], ctx), s, TODAY)
    assert not res.edits and res.classification == "related_no_change" and "language model" in res.reason


# ---- a request is not worded like the text it concerns --------------------------------------------------------------------------------------------------
EDU = """# Data Science

## Faculty

Machine Learning faculty is Santhosh sir.
He completed his B.Tech in Computer Science from JNTU.
He has a work experience of 3 years.

## Fees

| Course | Fee |
|---|---|
| Data Science | 15000 |
"""


def test_removing_text_that_shares_no_word_with_the_request_is_proposed_for_review_not_rejected():
    desc = "Remove the education details of santhosh sir"
    ans = EDU.replace("He completed his B.Tech in Computer Science from JNTU.\n", "").strip("\n")
    chat, r, _ = run(EDU, desc, lambda p: fenced(ans))
    assert len(chat.calls) == 1 and [e.kind for e in r.edits] == ["remove"] and r.classification == "potential"
    assert "B.Tech" not in patched(EDU, r) and "work experience of 3 years" in patched(EDU, r)
    assert any("only by meaning" in f for f in r.edits[0].flags)


def test_a_deletion_far_from_the_candidate_lines_and_unrelated_to_the_request_is_still_rejected():
    far = EDU.replace("| Data Science | 15000 |\n", "")
    o = revise(EDU, "Remove the education details of santhosh sir", far, at="He completed")
    assert o.state == "invalid" and "does not concern" in o.why


def test_the_reviewer_can_tell_a_model_that_found_nothing_from_one_whose_answer_was_unusable():
    desc = "Remove the education details of santhosh sir"
    _, r, _ = run(EDU, desc, lambda p: fenced(EDU.strip("\n")))
    assert not r.edits and any("judged that the requested change does not apply" in p["reason"] for p in r.passages)
    J._CACHE.clear()
    _, r2, _ = run(EDU, desc, lambda p: "```text\nhalf an answer")
    assert not r2.edits and any("could not be used" in p["reason"] and "cut off" in p["reason"] for p in r2.passages)
    J._CACHE.clear()
    _, r3, _ = run(EDU, desc, lambda p: RuntimeError("down"))
    assert any("could not be used" in p["reason"] for p in r3.passages)


def test_a_line_that_continues_the_subject_line_is_a_candidate_for_a_general_change():
    from app.knowledge.impact.analyse import generalize as g
    from app.knowledge.impact.evaluate import _general_passages
    from app.knowledge.impact.spec import ChangeSpec
    spec = g(ChangeSpec(), "Remove the education details of santhosh sir")
    c = Candidate(doc_id="ds", title="Data Science", path="", category="", tags=[], version=1, checksum="x", content=EDU)
    texts = [p.passage.text for p in _general_passages(spec, c, Settings(), None)]
    assert "He completed his B.Tech in Computer Science from JNTU." in texts


def test_a_discovered_document_whose_lines_do_not_name_the_subject_is_still_read_by_the_model():
    from app.knowledge.impact.analyse import generalize as g
    from app.knowledge.impact.evaluate import _general_passages
    from app.knowledge.impact.spec import ChangeSpec
    doc = "# Faculty profile\n\n## Education\n\nB.Tech in Computer Science from JNTU.\nM.Tech in AI from IIT.\n\n## Contact\n\nCall the office.\n"
    spec = g(ChangeSpec(), "Remove the education details of santhosh sir")
    c = Candidate(doc_id="f", title="Faculty profile", path="", category="", tags=[], version=1, checksum="x", content=doc)
    assert [p.passage.text for p in _general_passages(spec, c, Settings(), None)][:1] == ["B.Tech in Computer Science from JNTU."]     # the heading names the topic, the lines do not
    other = Candidate(doc_id="o", title="Contact", path="", category="", tags=[], version=1, checksum="x", content="# Contact\n\nCall the office.\n")
    assert _general_passages(spec, other, Settings(), None) == []                                       # nothing there is about the request: honestly nothing
    spec2 = g(ChangeSpec(), "Remove the education details from the faculty profile")
    assert _general_passages(spec2, c, Settings(), None)


# ---- the reported single-line case, requests phrased in different ways --------------------------------------------------------------------------------
ONE = "# Data Science\n\n## Faculty\n\nMachine Learning faculty is Santhosh sir. He has a work experience of 3 years. Previously he worked in IT. He do have a M.Tech in Data Science.\n"
NO_MTECH = ONE.replace(" He do have a M.Tech in Data Science.", "")


@pytest.mark.parametrize("desc", ["Remove the education details of santhosh sir", "Delete Santhosh sir's qualification", "Take out the degree information of Santhosh sir",
                                  "Santhosh sir's M.Tech should not be mentioned any more", "remove his education (santhosh sir)"])
def test_the_same_removal_asked_in_different_words_is_accepted(desc):
    o = revise(ONE, desc, NO_MTECH, at="Machine Learning")
    assert o.state == "revised" and "M.Tech" not in o.revised and "Previously he worked in IT." in o.revised, (desc, o.why)


def test_information_spread_over_lines_with_a_pronoun_is_edited_where_it_is():
    doc = "# DS\n\n## Faculty\n\nMachine Learning faculty is Santhosh sir.\nHe holds an M.Tech in Data Science.\nHe has 3 years of experience.\n"
    _, r, _ = run(doc, "Remove the education details of santhosh sir", lambda p: fenced(doc.replace("He holds an M.Tech in Data Science.\n", "").strip("\n")))
    assert [e.kind for e in r.edits] == ["remove"] and "M.Tech" not in patched(doc, r) and "3 years" in patched(doc, r)


def test_a_correction_of_the_past_is_allowed_only_when_the_request_targets_it():
    corr = ONE.replace("Previously he worked in IT.", "Previously he worked in a mechanical company.")
    assert revise(ONE, "santhosh sir actually worked in a mechanical company before", corr, at="Machine").state == "revised"
    assert revise(ONE, "change the work experience of santhosh sir from 3 years to 5 years", corr.replace("3 years", "5 years"), ["5 years"], ["3 years"], at="Machine").state == "invalid"


def test_one_bad_line_does_not_sink_a_good_neighbour():
    doc = "# Fees\n\nIn 2021 the fee was 10000.\nThe fee is 10000.\n"
    o = revise(doc, "change the fee from 10000 to 12000", doc.replace("10000", "12000"), ["12000"], ["10000"], at="The fee")
    assert o.state == "revised" and o.revised.endswith("The fee is 12000.") and "In 2021 the fee was 10000." in o.revised and o.rejected


def test_a_semantic_failure_against_a_clean_deterministic_result_goes_to_a_person():
    ans = fenced(DS.replace("- Online classes only", "- Hybrid classes").strip("\n"))
    check = Chat(lambda p: "FAIL - the weekend batch was also meant")
    _, r, _ = run(DS, "change online classes to hybrid classes", lambda p: ans, check=check, kb_ai_semantic_check="all", kb_ai_edit_retries=0)
    assert r.edits and r.classification == "potential" and any("second check" in f for e in r.edits for f in e.flags)


def test_a_very_long_explanation_never_crashes_the_analysis():
    from app.knowledge.impact.evaluate import PassageVerdict
    v = PassageVerdict(passage=0, verdict="potential", confidence=0.5, reason="x" * 2000, replacement="y" * 30000)
    assert len(v.reason) <= 400 and v.reason.endswith("…") and len(v.replacement) == 30000
