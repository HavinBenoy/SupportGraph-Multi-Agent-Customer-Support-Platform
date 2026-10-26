"""The policy docs are what the knowledge agent will cite. Guard them against drifting from the code."""

import re

import yaml

from app.tenants.loader import TENANTS_DIR, load_tenant_config

DOCS = sorted((TENANTS_DIR / "acme_store" / "docs").glob("*.md"))


def _front_matter(path):
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(), re.S)
    assert m, f"{path.name} has no front matter"
    return yaml.safe_load(m.group(1))


def test_every_customer_doc_has_valid_unique_front_matter():
    assert len(DOCS) >= 6
    ids = []
    for d in DOCS:
        fm = _front_matter(d)
        assert fm["audience"] == "customer", d.name
        assert fm["doc_id"] == d.stem and fm["title"] and fm["last_reviewed"], d.name
        ids.append(fm["doc_id"])
    assert len(ids) == len(set(ids))


def test_return_window_in_docs_matches_the_tenant_config():
    """Two sources of truth for one number is how 'hallucinated policy' happens. They must agree."""
    window = load_tenant_config("acme_store").return_window_days
    returns = (TENANTS_DIR / "acme_store" / "docs" / "returns-and-refunds.md").read_text()
    assert f"{window} days of delivery" in returns
    mentioned = {int(n) for n in re.findall(r"(\d+)\s*days of delivery", returns)}
    assert mentioned == {window}


def test_internal_material_is_kept_out_of_the_customer_docs_folder():
    internal = list((TENANTS_DIR / "acme_store" / "internal").glob("*.md"))
    assert internal and all(_front_matter(p)["audience"] == "internal" for p in internal)
    for d in DOCS:  # approval thresholds must never be retrievable by a customer-facing bot
        text = d.read_text().lower()
        assert "approval" not in text and "supervisor" not in text, d.name
