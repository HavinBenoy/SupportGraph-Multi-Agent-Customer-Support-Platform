"""A synthetic institute knowledge base (no real data) used by the Part 2 tests and the evaluation suite. Every document is invented. Ground truth for each scenario is
written next to the scenario, never inferred from the code under test."""

from tests.api.conftest import T
from tests.knowledge.kbhelp import BASE, admin, go_live

PY_FEES = """# Python Full Stack Course Fees

## Current fee
The Python Full Stack course fee is ₹45,000 inclusive of GST.

## Instalments
Students may pay the Python Full Stack fee of ₹45,000 in two instalments; the first is due at enrolment.

## Fee history
In 2023 the Python Full Stack course fee was ₹38,000.
"""
JAVA_FEES = """# Java Full Stack Course Fees

## Current fee
The Java Full Stack course fee is ₹50,000 inclusive of GST.
"""
BROCHURE = """# Institute Brochure

## Programmes
- Python Full Stack: Rs. 45,000/- for 8 months
- Java Full Stack: Rs. 50,000/- for 8 months
"""
FAQ = """# Admissions FAQ

## Fees
Q: What is the fee for Python Full Stack?
A: The Python Full Stack fee is INR 45000.

Q: What is the fee for Java Full Stack?
A: The Java Full Stack fee is INR 50000.
"""
PY_FACULTY = """# Python Full Stack Faculty

## Lead trainer
The Python Full Stack lead trainer is Dr. Mehta.
"""
JAVA_FACULTY = """# Java Full Stack Faculty

## Lead trainer
The Java Full Stack lead trainer is Dr. Mehta.
"""
DS_WEEKEND_A = """# Data Science Weekend Batch A

## Timing
The Data Science weekend batch A meets on Saturday and Sunday, 10:00 AM - 1:00 PM.
"""
DS_WEEKEND_B = """# Data Science Weekend Batch B

## Timing
The Data Science weekend batch B meets on Saturday and Sunday, 10:00 AM - 1:00 PM.
"""
DS_WEEKDAY = """# Data Science Weekday Batch

## Timing
The Data Science weekday batch meets Monday to Friday, 7:00 PM - 9:00 PM.
"""
DS_MIXED = """# Data Science Batches

## Weekend batch
The weekend batch meets 10:00 AM - 1:00 PM.

## Weekday batch
The weekday batch meets 10:00 AM - 1:00 PM.
"""
HOSTEL = """# Hostel Rules

## Curfew
Residents must return to the hostel by 10:00 PM. The hostel deposit is ₹5,000.
"""
FEE_ARCHIVE = """# Fee Archive

## Previous fees
The Python Full Stack course fee was ₹45,000 until March 2025.
"""

DOCS = {
    "python-fees": ("Python Full Stack Course Fees", PY_FEES, "Courses/Python"),
    "java-fees": ("Java Full Stack Course Fees", JAVA_FEES, "Courses/Java"),
    "brochure": ("Institute Brochure", BROCHURE, "Marketing"),
    "admissions-faq": ("Admissions FAQ", FAQ, "Admissions"),
    "python-faculty": ("Python Full Stack Faculty", PY_FACULTY, "Courses/Python"),
    "java-faculty": ("Java Full Stack Faculty", JAVA_FACULTY, "Courses/Java"),
    "ds-weekend-a": ("Data Science Weekend Batch A", DS_WEEKEND_A, "Courses/Data Science"),
    "ds-weekend-b": ("Data Science Weekend Batch B", DS_WEEKEND_B, "Courses/Data Science"),
    "ds-weekday": ("Data Science Weekday Batch", DS_WEEKDAY, "Courses/Data Science"),
    "ds-batches": ("Data Science Batches", DS_MIXED, "Courses/Data Science"),
    "hostel-rules": ("Hostel Rules", HOSTEL, "Campus"),
    "fee-archive": ("Fee Archive", FEE_ARCHIVE, "Archive"),
}


def seed(api, only=None, h=None):
    """Create, process and publish the synthetic documents (optionally a subset), each in its folder. Returns {doc_id: original content}."""
    h = h or admin(api)
    folders: dict[str, str] = {}

    def folder(path):
        parent = None
        for i, name in enumerate(path.split("/")):
            key = "/".join(path.split("/")[: i + 1])
            if key not in folders:
                r = api.post(f"{BASE}/folders", json={"name": name, **({"parent_id": parent} if parent else {})}, headers=h)
                assert r.status_code == 201, r.text
                folders[key] = r.json()["folder_id"]
            parent = folders[key]
        return parent

    out = {}
    for doc_id, (title, content, path) in DOCS.items():
        if only is not None and doc_id not in only:
            continue
        go_live(api, doc_id=doc_id, content=content, title=title, category="courses")
        if not api.app.state.settings.kb_legacy_folders:              # final M6: no folders (the flat document workspace)
            out[doc_id] = content
            continue
        fid = folder(path)
        r = api.put(f"{BASE}/documents/{doc_id}/folder", json={"folder_id": fid}, headers=h)
        assert r.status_code == 200, r.text
        out[doc_id] = content
    return out
