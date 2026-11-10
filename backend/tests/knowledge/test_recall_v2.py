"""Part 2 recall and precision on a realistic synthetic knowledge base (tests/knowledge/qs_corpus.py), v1 (document-level discovery) against v2 (evidence scan over units).
No database, no model: the deterministic RuleImpactModel judges, exactly as it does in production before (or without) a language model."""

import pytest

from app.config import Settings
from app.knowledge.impact import discover as D
from app.knowledge.impact.aliases import build_aliases, expand, find_person
from app.knowledge.impact.evaluate import RuleImpactModel, gather, validate
from app.knowledge.impact.sections import units
from tests.knowledge import qs_corpus as Q

CFG = {"machine learning": ["ml", "machine-learning"], "data science": ["ds"]}


def run(desc, mode="v2", corpus=None):
    s = Settings(kb_ai_discovery=mode)
    m = RuleImpactModel()
    spec = m.parse(desc)
    cands, rep = D.rank(corpus or Q.candidates(), s, spec, groups=CFG)
    out = []
    for c in cands:
        ctx = gather(spec, c, s, None)
        if not ctx:
            continue
        for p in validate(spec, c, ctx, m.evaluate(spec, c, ctx), s).passages:
            out.append((c.doc_id, p))
    return spec, rep, out


def score(out):
    proposed = {(d, p["text"]): p for d, p in out if p.get("replacement") and p["kind"] in ("definite", "potential", "ambiguous")}
    definite = {(d, p["text"]): p for d, p in out if p.get("replacement") and p["kind"] == "definite"}
    found = {k for k in Q.EDITS if (k[0], k[1]) in definite}
    wrong = {k for k in definite if k in Q.KEEP or k in Q.REVIEW}
    return found, wrong, proposed


@pytest.mark.parametrize("desc", [Q.OLD_KNOWN, Q.NEW_ONLY, Q.ABBREVIATED])
def test_v2_finds_every_differently_worded_passage_and_never_edits_the_lookalikes(desc):
    _, _, out = run(desc)
    found, wrong, proposed = score(out)
    assert found == set(Q.EDITS), set(Q.EDITS) - found
    assert not wrong, wrong
    for (doc, line), repl in Q.EDITS.items():
        assert proposed[(doc, line)]["replacement"] == repl
    for k in Q.KEEP:
        assert k not in proposed or proposed[k]["kind"] not in ("definite",)


def test_v2_is_at_least_as_good_as_v1_and_strictly_better_on_recall_and_false_edits():
    res = {}
    for mode in ("v1", "v2"):
        found, wrong, _ = score(run(Q.NEW_ONLY, mode)[2])
        res[mode] = (len(found), len(wrong))
    assert res["v2"][0] == len(Q.EDITS) and res["v2"][1] == 0
    assert res["v1"][0] < res["v2"][0]
    assert res["v1"][1] >= res["v2"][1]


def test_historical_and_other_subject_lines_are_classified_not_edited():
    _, _, out = run(Q.OLD_KNOWN)
    kinds = {(d, p["text"]): p["kind"] for d, p in out}
    assert kinds[("faculty-history", "Machine Learning faculty (2024): Jyothi sir.")] == "historical"
    assert kinds[("faculty-directory", "| Statistics | Jyothi sir | 9 years |")] == "related"
    assert kinds[("jyothi-profile", "Jyothi sir also teaches Statistics for Data Science.")] == "related"


def test_a_contradicting_value_is_ambiguous_and_a_respelling_is_only_potential():
    _, _, out = run(Q.OLD_KNOWN)
    by = {(d, p["text"]): p for d, p in out}
    assert by[("placement-guide", "Machine Learning faculty is Ramesh sir.")]["kind"] == "ambiguous"
    assert by[("typo-doc", "Jyoti sir teaches machine learning on Saturdays.")]["kind"] == "potential"
    assert by[("ml-stats-line", "Jyothi sir teaches Statistics and Machine Learning.")]["kind"] in ("potential", "ambiguous")


def test_new_value_only_learns_the_old_value_from_the_documents_in_a_second_round():
    _, rep, _ = run(Q.NEW_ONLY)
    assert [r["round"] for r in rep.rounds] == [1, 2]
    assert rep.learned and rep.learned[0]["value"] == "Jyothi sir" and "Ramesh sir" in rep.learned[0]["other_values"]


def test_evidence_is_recorded_for_every_judged_passage():
    _, _, out = run(Q.OLD_KNOWN)
    assert out and all(p.get("evidence") for _, p in out if p["kind"] in ("definite", "potential", "ambiguous"))


def test_unrelated_documents_are_not_candidates():
    _, _, out = run(Q.OLD_KNOWN)
    assert "hostel" not in {d for d, _ in out} and "python-course" not in {d for d, p in out if p["kind"] == "definite"}


# ---- building blocks -------------------------------------------------------------------------------------------------------------------------
def test_person_matching_ignores_honorifics_and_flags_respellings_as_inexact():
    hits = find_person("Mr. Jyothi, Jyothi sir and Jyoti sir.", "Jyothi sir")
    assert [h.exact for h in hits] == [True, True, False]


def test_alias_expansion_makes_ml_mean_machine_learning():
    spec = RuleImpactModel().parse(Q.OLD_KNOWN)
    al = build_aliases(spec, CFG, [])
    assert "machine learning" in expand("Jyothi sir handles ML sessions", al).lower()
    assert "machine learning" in expand("the machine-learning module", al)


def test_a_table_row_is_read_with_its_header():
    u = [x for x in units(Q.DOCS["faculty-directory"][1]) if "Machine Learning" in x.text][0]
    assert u.kind == "table_row" and u.columns[:2] == ("Module", "Faculty") and u.cells[1].text == "Jyothi sir"


def test_a_question_line_gives_context_to_its_answer():
    u = [x for x in units(Q.DOCS["ml-faq"][1]) if x.text.startswith("A: Jyothi")][0]
    assert "Machine Learning" in u.lead


def test_change_phrasings_are_understood():
    m = RuleImpactModel()
    f = m.parse(Q.OLD_KNOWN).facts[0]
    assert (f.old_value, f.new_value) == ("Jyothi sir", "Santhosh sir") and f.attribute == "faculty"
    g = m.parse(Q.NEW_ONLY).facts[0]
    assert g.old_value is None and g.new_value == "Santhosh sir"
    assert m.parse(Q.ABBREVIATED).entity_terms == ["ml"]


# ---- reported from the demo: "He has a work experience of 3 years. Previously he worked in IT." ----------------------------------------------
def _one_doc(body, desc):
    from app.knowledge.impact.discover import Candidate
    c = Candidate(doc_id="ds", title="Data-Science", path="", category="courses", tags=[], version=1, checksum="x", content=body)
    s = Settings(kb_ai_discovery="v2")
    m = RuleImpactModel()
    spec = m.parse(desc)
    cands, _ = D.rank([c], s, spec, groups={})
    out = []
    for cc in cands:
        ctx = gather(spec, cc, s, None)
        out += validate(spec, cc, ctx, m.evaluate(spec, cc, ctx), s).passages if ctx else []
    return out


EXP = "change the work experience of santhosh sir from 3 years to 5 years"


def test_a_pronoun_line_belongs_to_the_person_named_on_the_line_above():
    body = "# Data-Science\n\nMachine Learning faculty is Santhosh sir\nHe has a work experience of 3 years. Previously he worked in IT.\n"
    p = [x for x in _one_doc(body, EXP) if x["kind"] == "definite"]
    assert [x["replacement"] for x in p] == ["He has a work experience of 5 years. Previously he worked in IT."]


def test_a_pronoun_line_after_a_different_person_is_not_edited():
    body = "# Data-Science\n\nPython faculty is Ramesh sir\nHe has a work experience of 3 years.\n"
    assert not [x for x in _one_doc(body, EXP) if x["replacement"]]


def test_history_in_the_same_line_only_matters_when_it_holds_the_value():
    keep = "# Data-Science\n\nSanthosh sir\nPreviously he had a work experience of 3 years.\n"
    assert not [x for x in _one_doc(keep, EXP) if x["kind"] == "definite"]
    from app.knowledge.impact.scope import is_historical
    from app.knowledge.impact.passages import Span
    line = "He has 3 years of experience. Previously he worked in IT."
    assert not is_historical("Faculty", line, None, (Span(7, 14, "3 years"),), 0)
    assert is_historical("Faculty", line, None, (Span(40, 50, "worked"),), 0)
