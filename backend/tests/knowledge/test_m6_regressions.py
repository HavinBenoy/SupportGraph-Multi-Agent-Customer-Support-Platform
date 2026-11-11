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
    assert parse_rules(FEES).facts[0].old_value == "20000"              # the old single-spec reading: 20000 -> 12000, which found nothing


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
