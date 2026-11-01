"""The help documents are what the FAQ bot cites. Guard them against drifting from the code and from the truth."""

import re
from datetime import date

import pytest
import yaml

from app.rag.evaluate import internal_markers
from app.tenants.loader import TENANTS_DIR, load_tenant_config

TENANT = TENANTS_DIR / "qspiders_sample"
DOCS = sorted((TENANT / "docs").glob("*.md"))
INTERNAL = sorted((TENANT / "internal").glob("*.md"))


def _front_matter(path):
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(), re.S)
    assert m, f"{path.name} has no front matter"
    return yaml.safe_load(m.group(1))


def test_every_public_doc_has_valid_unique_front_matter():
    assert len(DOCS) >= 6
    ids = []
    for d in DOCS:
        fm = _front_matter(d)
        assert fm["audience"] == "public", d.name
        assert fm["doc_id"] == d.stem and fm["title"] and fm["last_reviewed"], d.name
        ids.append(fm["doc_id"])
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.stem)
def test_every_doc_declares_where_it_came_from_and_says_so_in_the_text(doc):
    """No invented policy may pass as real: each doc is either public-source (with URL + date) or labelled SAMPLE."""
    fm, text = _front_matter(doc), doc.read_text()
    assert fm["provenance"] in {"public", "sample"}
    if fm["provenance"] == "sample":
        assert "(SAMPLE)" in fm["title"], "a sample doc's title must say so: it is shown with every answer"
        assert "> SAMPLE CONTENT:" in text
    else:
        assert fm["source_url"].startswith("https://") and isinstance(fm["retrieved"], date)
        assert "> PUBLIC SOURCE:" in text


def test_attendance_threshold_in_docs_matches_the_tenant_config():
    """Two sources of truth for one number is how 'hallucinated policy' happens. They must agree."""
    threshold = load_tenant_config("qspiders_sample").attendance_threshold_pct
    text = (TENANT / "docs" / "student-support-attendance-and-assessments.md").read_text()
    assert {int(n) for n in re.findall(r"(\d+)%\*\*? of classes", text)} == {threshold}
    assert f"below {threshold}%" in text


def test_the_tenant_config_is_marked_as_sample_while_the_docs_are_sample():
    assert load_tenant_config("qspiders_sample").is_sample is True


def test_public_website_facts_are_limited_to_what_was_read():
    about = (TENANT / "docs" / "about-the-institute.md").read_text()
    for claimed in ("90+ training institutes", "100+ industry-relevant technologies", "Enquire Now", "Request a Call Back"):
        assert claimed in about
    assert not re.search(r"\d{10}", about), "no phone numbers: they change, and the website is the source"
    assert not re.search(r"(?i)(rs\.?|inr|₹)\s?\d", about), "the website publishes no fees; the public doc must not invent any"


def test_internal_material_is_kept_out_of_the_public_docs_folder():
    assert INTERNAL and all(_front_matter(p)["audience"] == "internal" for p in INTERNAL)
    markers = internal_markers("qspiders_sample")
    assert markers
    for d in DOCS:  # concession limits and approvers must never be retrievable by a public-facing bot
        text = d.read_text().lower()
        for banned in ("branch manager", "regional head", "5 percent", "15 percent", "academic coordinator"):
            assert banned not in text, f"{d.name} mentions internal-only '{banned}'"
        assert not any(m in re.sub(r"\s+", " ", re.sub(r"[*_`#>|-]", " ", text)) for m in markers), d.name
