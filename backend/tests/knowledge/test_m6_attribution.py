"""Final M6 refinement 2.3: who edited a proposal comes from the persisted audit trail, never from the person looking at it."""

from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.institute import seed
from tests.knowledge.kbhelp import BASE, admin, admin2, other_admin
from tests.knowledge.kwhelp import staff
from tests.rag.conftest import embedder, ingested  # noqa: F401

NEW = "- Python Full Stack: Rs. 48,000/- (8 months, GST extra)"


def edited_by(api, user):
    seed(api)
    h = staff(api, user)
    req = A.analysed(api, h=h)
    rid = req["request_id"]
    A.accept_all(api, req, h=h)
    A.decide(api, rid, "brochure", "manual", h=h, replacements=[{"index": 0, "replacement": NEW}])
    A.submit(api, rid, h=h)
    return rid


def test_the_admin_sees_the_staff_member_who_edited_not_themselves(api):  # noqa: F811
    rid = edited_by(api, "staff7")
    it = A.item(A.a_get(api, rid), "brochure")
    assert (it["decision"], it["staff_edited_by"], it["last_edited_by"], it["last_edit_role"]) == ("manual", "staff7", "staff7", "author")
    assert it["admin_edited_by"] is None and it["editors"] == ["staff7"]


def test_a_different_staff_member_is_named_and_an_admin_edit_becomes_the_latest(api):  # noqa: F811
    rid = edited_by(api, "staff8")
    it = A.item(A.a_get(api, rid), "brochure")
    r = api.put(f"{BASE}/changes/{rid}/items/brochure/edit", json={"replacements": [{"index": 0, "replacement": NEW + " (from 1 Dec)"}],
                                                                    "expected_row_version": it["row_version"]}, headers=admin2(api))
    assert r.status_code == 200, r.text
    it = A.item(A.a_get(api, rid), "brochure")
    assert it["staff_edited_by"] == "staff8"                                  # the proposer's edit is still attributed to them ...
    assert (it["admin_edited_by"], it["last_edited_by"], it["last_edit_role"]) == ("admin2", "admin2", "admin")   # ... the latest edit is the administrator's
    assert it["editors"] == ["admin2", "staff8"]
    assert A.item(A.a_get(api, rid, h=admin(api)), "brochure")["last_edited_by"] == "admin2"   # the same answer whoever looks


def test_reverting_to_the_ai_text_clears_the_attribution(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    rid = req["request_id"]
    A.decide(api, rid, "brochure", "manual", replacements=[{"index": 0, "replacement": NEW}])
    out = A.decide(api, rid, "brochure", "accepted")
    assert out["last_edited_by"] is None and out["staff_edited_by"] is None


def test_attribution_never_crosses_tenants(api):  # noqa: F811
    rid = edited_by(api, "staff9")
    assert api.get(f"{BASE}/changes/{rid}", headers=other_admin(api)).status_code == 404
