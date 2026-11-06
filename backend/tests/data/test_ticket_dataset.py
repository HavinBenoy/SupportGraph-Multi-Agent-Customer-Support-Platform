"""The labelled tickets must stay consistent with the seeded data and the docs, so evals fail for real reasons."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from app.db.seed_data import build_seed

ROOT = Path(__file__).resolve().parents[3]
PATH = ROOT / "evals" / "datasets" / "tickets_qspiders_v1.jsonl"
DOCS = ROOT / "tenants" / "qspiders_sample" / "docs"
TICKETS = [json.loads(line) for line in PATH.read_text(encoding="utf-8").splitlines()]

ROUTES = {"knowledge", "action", "escalate", "clarify", "write", "converse"}  # converse (M2.1): greetings and thanks
CATEGORIES = {"courses", "eligibility", "fees", "admissions", "placement", "attendance", "assessment", "schedule",
              "privacy", "other"}
FIELDS = {"id", "tenant_id", "student_id", "persona", "channel", "language", "message", "category", "priority",
          "sentiment", "expected_route", "expected_tools", "expected_write_action", "needs_human", "must_abstain",
          "expected_facts", "expected_docs", "must_not_contain", "tags", "notes"}
KNOWN_TOOLS = {"get_my_batches", "get_my_schedule", "get_my_attendance", "list_my_assessments"}
BATCH_ID = re.compile(r"\b[A-Z]{2,4}-\d{3}\b")


def test_schema_and_enums():
    for t in TICKETS:
        assert set(t) == FIELDS, t["id"]
        assert t["expected_route"] in ROUTES and t["category"] in CATEGORIES, t["id"]
        assert t["persona"] in {"prospect", "student"}
        assert t["priority"] in {"low", "medium", "high", "urgent"}
        assert t["sentiment"] in {"positive", "neutral", "negative", "angry"}
        assert t["expected_write_action"] in {None, "request_makeup_class", "update_contact_phone", "raise_support_ticket"}
        assert set(t["expected_tools"]) <= KNOWN_TOOLS, t["id"]
        assert t["message"].strip()


def test_ids_unique_and_count():
    ids = [t["id"] for t in TICKETS]
    assert len(ids) == len(set(ids)) and len(TICKETS) >= 40


def test_personas_match_who_they_are():
    students = {s[0] for s in build_seed()[0].students}
    for t in TICKETS:
        assert t["tenant_id"] == "qspiders_sample"
        if t["persona"] == "prospect":
            assert t["student_id"] == "anonymous", t["id"]  # a prospect is not signed in and has no records
        else:
            assert t["student_id"] in students, t["id"]


def test_every_batch_reference_matches_the_seed():
    seed = build_seed(True)
    mine = {e.batch_id for e in seed[0].enrollments if e.status != "withdrawn"}
    owned = {(e.student_id, e.batch_id) for e in seed[0].enrollments if e.status != "withdrawn"}
    other_only = {b.id for b in seed[1].batches} - {b.id for b in seed[0].batches}
    names = {s[1].lower(): s[0] for s in seed[0].students}
    for t in TICKETS:
        for batch in BATCH_ID.findall(t["message"]):
            if "cross_tenant" in t["tags"]:
                assert batch in other_only, t["id"]
            elif "cross_student" in t["tags"] or t["persona"] == "prospect":
                assert batch in mine, f"{t['id']}: {batch} should be a real demo batch"
            else:
                assert (t["student_id"], batch) in owned, f"{t['id']}: {batch} is not {t['student_id']}'s batch"
        if "cross_student" in t["tags"]:
            named = [sid for n, sid in names.items() if n in t["message"].lower()]
            assert named and t["student_id"] not in named, t["id"]


def test_coverage_of_routes_and_adversarial_cases():
    routes = Counter(t["expected_route"] for t in TICKETS)
    assert all(routes[r] >= 3 for r in ROUTES - {"converse"}), routes   # converse (M2.1) is covered by tests/graph/test_m21_conversation.py
    tags = Counter(tag for t in TICKETS for tag in t["tags"])
    for needed in ("injection", "cross_student", "cross_tenant", "pii_fishing", "hallucination_probe", "boundary",
                   "legal_threat", "safety_incident", "non_english", "internal_probe", "complaint"):
        assert tags[needed] >= 1, f"no ticket tagged {needed}"
    assert sum("adversarial" in t["tags"] for t in TICKETS) >= 6
    assert {t["persona"] for t in TICKETS} == {"prospect", "student"}


def test_label_consistency_rules():
    for t in TICKETS:
        if t["expected_route"] == "escalate":
            assert t["needs_human"], t["id"]
        if t["must_abstain"]:
            assert t["expected_route"] == "knowledge" and not t["expected_facts"] and not t["expected_docs"], t["id"]
        if t["expected_route"] == "knowledge":
            assert not t["expected_tools"], t["id"]
        if t["expected_route"] == "action":
            assert t["expected_tools"] and t["persona"] == "student", t["id"]  # records need a signed-in student
        if t["expected_facts"] and t["expected_route"] == "knowledge":
            assert t["expected_docs"], f"{t['id']}: an answerable question must say which document holds the answer"
        if "injection" in t["tags"]:
            assert t["expected_write_action"] is None, t["id"]  # an injected instruction must never yield a write


@pytest.mark.parametrize("fact_ticket", [t for t in TICKETS if t["expected_route"] == "knowledge" and t["expected_facts"]],
                         ids=lambda t: t["id"])
def test_every_knowledge_fact_is_stated_in_the_expected_documents(fact_ticket):
    """If a label expects '55,000' the expected document must say '55,000', otherwise the eval would punish a correct bot."""
    for doc in fact_ticket["expected_docs"]:
        assert (DOCS / f"{doc}.md").is_file(), f"{fact_ticket['id']}: unknown doc {doc}"
    corpus = " ".join((DOCS / f"{d}.md").read_text() for d in fact_ticket["expected_docs"]).lower()
    for fact in fact_ticket["expected_facts"]:
        assert fact.lower() in corpus, f"{fact_ticket['id']}: '{fact}' not found in {fact_ticket['expected_docs']}"


@pytest.mark.parametrize("t", [t for t in TICKETS if t["must_abstain"]], ids=lambda t: t["id"])
def test_must_abstain_questions_are_really_not_in_the_documents(t):
    """A must-abstain label is only fair if the documents genuinely lack the answer: check the distinctive nouns."""
    corpus = " ".join(p.read_text() for p in DOCS.glob("*.md")).lower()
    distinctive = {"java": "java", "bangalore": "bangalore", "hyderabad": "hyderabad", "devops": "devops",
                   "scholarship": "scholarship", "trainer for batch": "trainer for batch", "pass mark": "pass mark", "gst": "gst"}
    hits = [k for k in distinctive if k in t["message"].lower()]
    assert hits, f"{t['id']}: add its distinctive term to this test"
    assert all(distinctive[k] not in corpus for k in hits), f"{t['id']}: the docs mention {hits}; it is answerable"
