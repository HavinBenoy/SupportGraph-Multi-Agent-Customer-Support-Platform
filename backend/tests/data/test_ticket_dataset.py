"""The labelled tickets must stay consistent with the seeded store, so evals fail for real reasons."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from app.db.seed_data import build_seed

PATH = Path(__file__).resolve().parents[3] / "evals" / "datasets" / "tickets_acme_v1.jsonl"
TICKETS = [json.loads(line) for line in PATH.read_text(encoding="utf-8").splitlines()]

ROUTES = {"knowledge", "action", "escalate", "clarify"}
CATEGORIES = {"order_status", "returns", "refund", "shipping", "billing", "account", "product_question",
              "security", "safety", "other"}
FIELDS = {"id", "tenant_id", "customer_id", "channel", "language", "message", "category", "priority", "sentiment",
          "expected_route", "expected_tools", "expected_write_action", "needs_human", "must_abstain",
          "expected_facts", "must_not_contain", "tags", "notes"}
KNOWN_TOOLS = {"get_order_status", "list_my_orders", "check_return_eligibility"}


def test_schema_and_enums():
    for t in TICKETS:
        assert set(t) == FIELDS, t["id"]
        assert t["expected_route"] in ROUTES and t["category"] in CATEGORIES, t["id"]
        assert t["priority"] in {"low", "medium", "high", "urgent"}
        assert t["sentiment"] in {"positive", "neutral", "negative", "angry"}
        assert t["expected_write_action"] in {None, "create_return", "refund"}
        assert set(t["expected_tools"]) <= KNOWN_TOOLS, t["id"]
        assert t["message"].strip()


def test_ids_unique_and_count():
    ids = [t["id"] for t in TICKETS]
    assert len(ids) == len(set(ids)) and len(TICKETS) >= 40


def test_every_customer_and_order_reference_exists_in_the_seed():
    acme = build_seed()[0]
    customers = {c[0] for c in acme.customers}
    owner = {o.id: o.customer_id for o in acme.orders}
    other = {o.id for o in build_seed(True)[1].orders}
    for t in TICKETS:
        assert t["tenant_id"] == "acme_store" and t["customer_id"] in customers, t["id"]
        for oid in re.findall(r"\b[A-Z]\d{4}\b", t["message"]):
            if "cross_tenant" in t["tags"]:
                assert oid in other and oid not in owner, t["id"]
            elif "cross_customer" in t["tags"]:
                assert oid in owner and owner[oid] != t["customer_id"], t["id"]
            else:
                assert owner.get(oid) == t["customer_id"], f"{t['id']}: {oid} is not this customer's order"


def test_coverage_of_routes_and_adversarial_cases():
    routes = Counter(t["expected_route"] for t in TICKETS)
    assert all(routes[r] >= 3 for r in ROUTES), routes
    tags = Counter(tag for t in TICKETS for tag in t["tags"])
    for needed in ("injection", "cross_customer", "cross_tenant", "pii_fishing", "hallucination_probe", "boundary",
                   "legal_threat", "safety_incident", "non_english"):
        assert tags[needed] >= 1, f"no ticket tagged {needed}"
    assert sum("adversarial" in t["tags"] for t in TICKETS) >= 6


def test_label_consistency_rules():
    for t in TICKETS:
        if t["expected_route"] == "escalate":
            assert t["needs_human"], t["id"]
        if t["must_abstain"]:
            assert t["expected_route"] == "knowledge" and not t["expected_facts"], t["id"]
        if t["expected_route"] == "knowledge":
            assert not t["expected_tools"], t["id"]
        if "injection" in t["tags"]:
            assert t["expected_write_action"] is None, t["id"]  # an injected instruction must never yield a write


@pytest.mark.parametrize("fact_ticket", [t for t in TICKETS if t["expected_route"] == "knowledge" and t["expected_facts"]],
                         ids=lambda t: t["id"])
def test_every_knowledge_fact_is_actually_stated_in_the_docs(fact_ticket):
    """If a label expects '$14.99' the docs must say '$14.99' -- otherwise the eval would punish a correct bot."""
    corpus = " ".join(p.read_text() for p in (PATH.parents[2] / "tenants" / "acme_store" / "docs").glob("*.md"))
    for fact in fact_ticket["expected_facts"]:
        assert fact.lower() in corpus.lower(), f"{fact_ticket['id']}: '{fact}' not found in policy docs"
