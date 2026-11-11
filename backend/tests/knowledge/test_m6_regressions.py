"""Regression cases from real use (staff console + real Qwen probe runs, 2026-11-11). Scripted model; the real-model counterparts are in scripts/judge_probe.py --suite."""

from datetime import date

from app.config import Settings
from app.knowledge.impact import context as C
from app.knowledge.impact import discover as D
from app.knowledge.impact import edit as E
from app.knowledge.impact.discover import Candidate
from app.knowledge.impact.spec import parse_rules, split_intents
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.test_generalized_flow import analysed, release, seed
from tests.knowledge.test_pipeline_trace import Model, events, sub, use
from tests.rag.conftest import embedder, ingested  # noqa: F401

FAQ = "Fcaulty of gen ai is vinu sir. He does have Bsc in Data science.Fees of gen ai course is 25000 rupees"
COURSES = ("Machine Learning faculty is Santhosh sir. He has a work experience of 3 years. Previously he worked in IT. He do have a M.Tech in Data Science. Fee for Machine "
           "learning course is 9999 rupees. The most interesting part of his class is now only the practical aspect of machine learning.")
FEES = "The fees for gen ai is 20000 rs and that for machine learning is 12000 rs"
TODAY = date(2026, 11, 11)


def cand(doc_id, content, title=None, path=""):
    return Candidate(doc_id=doc_id, title=title or doc_id, path=path, category="", tags=[], version=1, checksum="x", content=content)


# ================================================================================================================== A/B. discovery, multi-intent
def test_a_request_with_two_fee_changes_is_two_intents_not_an_old_to_new_pair():
    parts = split_intents(FEES)
    assert parts == ["The fees for gen ai is 20000 rs", "fees for machine learning is 12000 rs"]
    f1, f2 = parse_rules(parts[0]), parse_rules(parts[1])
    assert (f1.facts[0].old_value, f1.facts[0].new_value, f1.entity_terms) == (None, "20000 rs", ["gen", "ai"])
    assert (f2.facts[0].old_value, f2.facts[0].new_value, f2.entity_terms) == (None, "12000 rs", ["machine", "learning"])
    assert parse_rules(FEES).facts[0].old_value is None                 # even unsplit: two amounts after "is" are two NEW values (it used to read 20000 -> 12000)


def test_each_intent_discovers_its_own_document_without_any_old_value():
    s = Settings(kb_ai_discovery="v2")
    found = {}
    for part in split_intents(FEES):
        cands, _ = D.rank([cand("faq", FAQ, "faq", "course/faq"), cand("courses", COURSES, "courses", "Data-Science/courses")], s, parse_rules(part), groups={})
        found[part] = [c.doc_id for c in cands]
    assert found == {"The fees for gen ai is 20000 rs": ["faq"], "fees for machine learning is 12000 rs": ["courses"]}


def test_a_strong_semantic_match_qualifies_a_document_on_its_own():
    s = Settings(kb_ai_discovery="v2")
    spec = parse_rules("the placement assistance is now available for every batch")
    doc = cand("p", "Students get help with interviews and job referrals after the course.")
    assert not D.rank([cand("p", doc.content)], s, spec, groups={}, sem={"p": 0.2})[0]
    cands, _ = D.rank([doc], s, spec, groups={}, sem={"p": 0.83})
    assert [c.doc_id for c in cands] == ["p"] and "semantically close" in cands[0].signals["why"]


def test_half_of_the_subject_words_is_enough_to_send_a_document_to_the_model():
    s = Settings(kb_ai_discovery="v2")
    spec = parse_rules("The fees for gen ai and machine learning are now 15000 rs")
    cands, _ = D.rank([cand("faq", FAQ), cand("courses", COURSES)], s, spec, groups={})
    assert {c.doc_id for c in cands} == {"faq", "courses"}


def test_two_intents_through_the_application_both_documents_get_a_proposal(api, monkeypatch):  # noqa: F811
    chat = Model(sub(("25000 rupees", "20000 rupees"), ("9999 rupees", "12000 rupees")))
    use(monkeypatch, chat)
    seed(api, faq=("faq", FAQ), courses=("courses", COURSES))
    req = analysed(api, FEES)
    items = {i["doc_id"]: i for i in req["items"] if (i["ai"] or {}).get("edits")}
    assert set(items) == {"faq", "courses"}
    assert "20000" in items["faq"]["proposed_content"] and "25000" not in items["faq"]["proposed_content"]
    assert "12000" in items["courses"]["proposed_content"] and "9999" not in items["courses"]["proposed_content"]
    intents = [e for e in events(req, "intent", "discovered")]
    assert [e["documents"] for e in intents] == [["faq"], ["courses"]]
    done = release(api, req)
    assert done["status"] == "published" and "20000" in A.live_text("faq") and "12000" in A.live_text("courses")


def test_one_intent_that_finds_nothing_does_not_hide_the_other(api, monkeypatch):  # noqa: F811
    use(monkeypatch, Model(sub(("25000 rupees", "20000 rupees"))))
    seed(api, faq=("faq", FAQ))
    req = analysed(api, "The fees for gen ai is 20000 rs and that for quantum computing is 5000 rs")
    assert [i["doc_id"] for i in req["items"] if (i["ai"] or {}).get("edits")] == ["faq"]
    disc = events(req, "intent", "discovered")
    assert len(disc) == 2 and disc[0]["documents"] == ["faq"] and disc[1]["documents"] == []


def test_a_mixed_request_value_plus_addition_keeps_the_subject_for_the_pronoun(api, monkeypatch):  # noqa: F811
    chat = Model(sub(("3 years", "5 years"), ("He do have a M.Tech in Data Science.", "He do have a M.Tech in Data Science. He also holds an MBA.")))
    use(monkeypatch, chat)
    seed(api, courses=("courses", COURSES))
    req = analysed(api, "change the work experience of santhosh sir from 3 years to 5 years and add that he holds an MBA")
    it = A.item(req, "courses")
    assert "5 years" in it["proposed_content"] and "MBA" in it["proposed_content"]
    assert [e["documents"] for e in events(req, "intent", "discovered")] == [["courses"], ["courses"]]


# ============================================================================================================== C/D. editing and verification
def _sec(doc, at):
    (sec,) = C.sections_for(doc, [doc.index(at)], C.Budget.from_settings(Settings()))
    return sec


def test_an_unrelated_passage_is_examined_but_its_rewrite_is_not_accepted():
    web = "# Web Development\n\n## Eligibility\n\n- 12th pass\n- Classes are conducted offline\n"
    spec = parse_rules("Change online classes to hybrid classes")
    assert (spec.facts[0].old_value, spec.facts[0].new_value) == ("online classes", "hybrid classes")
    o = E.revise(_sec(web, "- Classes"), web.replace("conducted offline", "conducted hybrid"), E.Rules(spec.summary, "Web", ("hybrid classes",), ("online classes",), TODAY))
    assert o.state == "invalid" and "does not contain the value being replaced" in o.why


def test_a_negation_instead_of_a_removal_is_flagged_and_the_checklist_verifier_fails_it():
    one = "# DS\n\nMachine Learning faculty is Santhosh sir. He do have a M.Tech in Data Science.\n"
    ans = one.replace("He do have a M.Tech in Data Science.", "He has only a B.Tech in AI. He do not have a M.Tech in Data Science.")
    o = E.revise(_sec(one, "Machine"), ans, E.Rules("Santhosh sir actually has only a B.Tech in AI", "DS", (), (), TODAY))
    assert o.state == "revised" and any("negation" in f for h in o.hunks for f in h.flags)
    verdict = E.parse_semantic("REQUESTED: only B.Tech in AI\nMISSING: none\nCONTRADICTING: He do not have a M.Tech in Data Science\nUNRELATED: none\nVERDICT: PASS")
    assert verdict[0] == "fail" and "contradicting" in verdict[1]


def test_an_incomplete_correction_is_retried_with_the_verifiers_reason(api, monkeypatch):  # noqa: F811
    from langchain_core.messages import AIMessage
    kept = sub(("Previously he worked in IT.", "Previously he worked in a mechanical company as a support officer."),
               ("He do have a M.Tech in Data Science.", "He do have a B.Tech in AI and a M.Tech in Data Science."))
    fixed = sub(("Previously he worked in IT.", "Previously he worked in a mechanical company as a support officer."), ("He do have a M.Tech in Data Science.", "He has a B.Tech in AI."))
    check = Model(lambda x: x)
    check.invoke = lambda msgs: AIMessage(
        "REQUESTED: only B.Tech in AI; mechanical company\nMISSING: none\nCONTRADICTING: " + ("M.Tech kept" if "M.Tech" in msgs[0].content.split("<revised>")[-1] else "none")
        + "\nUNRELATED: none\nVERDICT: PASS")                                                                  # the checker says PASS either way: the checklist decides
    chat = Model(kept, fixed)
    use(monkeypatch, chat, check)
    seed(api, courses=("courses", COURSES))
    it = A.item(analysed(api, "Santhosh sir actually has only a B.Tech in AI and worked in a mechanical company as a support officer."), "courses")
    assert len(chat.calls) == 2 and "contradicting: M.Tech kept" in chat.calls[1]
    assert "M.Tech" not in it["proposed_content"] and "B.Tech in AI" in it["proposed_content"]


def test_an_unchanged_answer_to_a_removal_gets_one_second_look(api, monkeypatch):  # noqa: F811
    chat = Model(lambda x: x, sub((" He do have a M.Tech in Data Science.", "")))
    use(monkeypatch, chat)
    seed(api, courses=("courses", COURSES))
    req = analysed(api, "Remove the education details of santhosh sir")
    it = A.item(req, "courses")
    assert len(chat.calls) == 2 and "returned the section unchanged" in chat.calls[1] and "M.Tech" not in it["proposed_content"]
    assert events(req, "retry", "asking_again")


def test_the_second_look_cannot_turn_an_unrelated_document_into_a_proposal(api, monkeypatch):  # noqa: F811
    web = "# Web Development\n\n## Eligibility\n\n- 12th pass\n- Classes are conducted offline\n"
    chat = Model(lambda x: x, sub(("conducted offline", "conducted hybrid")))
    use(monkeypatch, chat)
    seed(api, web=("Web Development", web))
    req = analysed(api, "Change online classes to hybrid classes")
    assert not [i for i in req["items"] if (i["ai"] or {}).get("edits")]
    assert "offline" in A.live_text("web")


# ================================================================================================ add-on (2026-11-11 04:52): the two new real failures
FEES2 = "change the gen ai course fees to 20000 rupees and machine learning course fees to 12000 rupees"
DS2 = ("Machine Learning faculty is Santhosh sir.\nHe has a work experience of 3 years.\nPreviously he worked in IT.\nHe do have a M.Tech in Data Science.\n"
       "Fee for Machine learning course is 9999 rupees.\n")
COURSE2 = "Faculty of gen ai is vinu sir.\nHe does have Bsc in Data science.\nFees of gen ai course is 25000 rupees.\n"


def test_case_A_each_fee_is_its_own_intent_with_its_own_new_value_and_no_borrowed_old_value():
    parts = split_intents(FEES2)
    assert parts == ["change the gen ai course fees to 20000 rupees", "change machine learning course fees to 12000 rupees"]
    a, b = (parse_rules(p) for p in parts)
    assert (a.facts[0].old_value, a.facts[0].new_value) == (None, "20000 rupees") and (b.facts[0].old_value, b.facts[0].new_value) == (None, "12000 rupees")
    assert parse_rules(FEES2).facts[0].old_value is None                        # even unsplit, two "to <amount>" are two new values, never 20000 -> 12000


def test_case_A_through_the_application_both_fees_are_proposed_and_separately_reviewable(api, monkeypatch):  # noqa: F811
    chat = Model(sub(("25000 rupees", "20000 rupees"), ("9999 rupees", "12000 rupees")))
    use(monkeypatch, chat)
    seed(api, course=("course", COURSE2), ds=("Data-Science", DS2))
    req = analysed(api, FEES2)
    items = {i["doc_id"]: i for i in req["items"] if (i["ai"] or {}).get("edits")}
    assert set(items) == {"course", "ds"}
    assert "Fees of gen ai course is 20000 rupees." in items["course"]["proposed_content"]
    assert "Fee for Machine learning course is 12000 rupees." in items["ds"]["proposed_content"] and "9999" not in items["ds"]["proposed_content"]
    assert not events(req, "validation", "rejected")                            # the ML proposal is never checked against 20000
    ml_prompts = [c for c in chat.calls if "Machine Learning faculty" in c]
    assert ml_prompts and all("20000" not in c.split("<section>")[0] for c in ml_prompts)    # each section is asked about its OWN intent only
    rid = req["request_id"]
    A.decide(api, rid, "course", "accepted")
    A.decide(api, rid, "ds", "discarded")                                       # independently reviewable
    assert {i["doc_id"]: i["decision"] for i in A.get(api, rid)["items"] if i["doc_id"] in ("course", "ds")} == {"course": "accepted", "ds": "discarded"}


def test_case_A_one_intent_failing_at_the_model_does_not_block_the_other(api, monkeypatch):  # noqa: F811
    good = sub(("25000 rupees", "20000 rupees"))
    chat = Model(lambda sec: good(sec) if "gen ai" in sec else "half")         # the ML section gets an unusable answer
    chat_invoke = chat.invoke
    chat.invoke = lambda msgs: chat_invoke(msgs) if "gen ai" in msgs[0].content.split("<section>")[-1] else __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage("```text\nhalf")
    use(monkeypatch, chat)
    seed(api, course=("course", COURSE2), ds=("Data-Science", DS2))
    req = analysed(api, FEES2)
    with_edits = [i["doc_id"] for i in req["items"] if (i["ai"] or {}).get("edits")]
    assert "course" in with_edits
    ds = A.item(req, "ds")
    assert ds is not None                                                        # the ML document is still listed (the rules' own proposal or a stated failure)


class _WrongSubjectParse:
    """A language-model parse that names the NEW value as the subject (what Qwen did for case B)."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, k):
        return getattr(self.inner, k)

    def parse(self, description):
        from app.knowledge.impact.spec import ChangeSpec, Fact
        return ChangeSpec(summary=description, facts=[Fact(attribute="work experience", old_value=None, new_value="Mechanical industry")], entity_terms=["mechanical", "industry"])


def test_case_B_subject_words_of_the_description_survive_a_bad_model_parse():
    from app.knowledge.impact.analyse import Input, build_spec
    spec = build_spec(Input(description="Change the work experience of santhosh sir to Mechanical industry.", answers={}, reference=None),
                      _WrongSubjectParse(None))
    assert "santhosh" in spec.entity_terms and "mechanical" not in spec.entity_terms and "industry" not in spec.entity_terms


def test_case_B_a_semantic_score_of_0_59_with_zero_subject_words_reaches_the_model():
    s = Settings(kb_ai_discovery="v2")
    spec = parse_rules("the industry of the ML trainer is now Mechanical").model_copy(update={"entity_terms": ["trainer"]})
    ds = cand("ds", DS2, "Data-Science", "Data-Science")
    cands, _ = D.rank([ds], s, spec, groups={}, sem={"ds": 0.59})
    assert [c.doc_id for c in cands] == ["ds"] and ds.signals["lexical"] == 0 and "semantically close" in ds.signals["why"]
    assert not D.rank([cand("ds", DS2)], Settings(kb_ai_discovery="v2", kb_ai_semantic_admit=0.6), spec, groups={}, sem={"ds": 0.59})[0]     # configurable


def test_case_B_through_the_application_the_section_reaches_the_model_and_a_correction_is_reviewable(api, monkeypatch):  # noqa: F811
    chat = Model(sub(("Previously he worked in IT.", "Previously he worked in the Mechanical industry.")))
    use(monkeypatch, chat)
    seed(api, ds=("Data-Science", DS2), course=("course", COURSE2))
    req = analysed(api, "Change the work experience of santhosh sir to Mechanical industry.")
    it = A.item(req, "ds")
    assert any("Machine Learning faculty" in c for c in chat.calls)            # the relevant section was sent
    assert it["ai"]["edits"] and it["classification"] == "potential" and "Mechanical industry" in it["proposed_content"]
    assert any("explicitly corrects" in f for e in it["ai"]["edits"] for f in e.get("flags", []))     # a past statement changed: flagged for the reviewer
