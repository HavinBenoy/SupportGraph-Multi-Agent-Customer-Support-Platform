"""Knowledge-governance analytics (final M6): computed from the real request workflow, tenant-scoped, admin-only, and free of document text."""

from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.kbhelp import SECRET_TEXT, admin, other_admin
from tests.knowledge.kwhelp import ST, drafted, live_doc, staff, submit
from tests.rag.conftest import embedder, ingested  # noqa: F401

URL = "/api/admin/analytics/knowledge"


def test_the_funnel_and_rates_follow_the_workflow(api):  # noqa: F811
    live_doc(api)
    live_doc(api, "second", content="# Second\n\nThe hostel fee is 3000 rupees.\n", title="Second")
    A.inline(api)
    empty = api.get(URL, headers=admin(api)).json()["knowledge"]
    assert empty["requests"] == 0 and empty["staff"]["acceptance_rate"] is None and empty["median_hours_to_publication"] is None   # n/a, never a made-up 0
    rid, _ = drafted(api)                                                       # a manual change: submitted, approved, published
    submit(api, rid)
    A.approve(api, rid)
    A.publish(api, rid)
    rid2, _ = drafted(api, "second", content="# Second\n\nThe hostel fee is 3500 rupees.\n")   # a second one: submitted and rejected
    submit(api, rid2, desc="Hostel fee")
    A.a_post(api, rid2, "review", {"decision": "reject", "note": "not yet"})
    api.post(f"{ST}/documents/second/deletion-request", json={"reason": "merged elsewhere", "expected_version": 1}, headers=staff(api))
    g = api.get(URL, params={"days": 7}, headers=admin(api)).json()
    k = g["knowledge"]
    assert k["requests"] == 3 and k["by_kind"] == {"deletion": 1, "manual": 2}
    assert [s["count"] for s in k["funnel"]] == [3, 3, 3, 1, 1]
    assert k["admin"] == {**k["admin"], "submitted": 3, "approved": 1, "rejected": 1, "approval_rate": 0.5}
    assert k["published"] == 1 and k["published_items"]["human_modified"] == 1 and k["median_hours_to_publication"] is not None
    row = next(c for c in k["contributions"] if c["staff"] == "staff1")
    assert (row["requests"], row["submitted"], row["approved"], row["published"]) == (3, 3, 1, 1)
    assert all(key in g["definitions"] for key in ("funnel", "manual_edit_rate", "median_hours_to_publication"))
    assert SECRET_TEXT not in str(g) and "hostel" not in str(g).lower()           # no document text, no descriptions


def test_admin_only_and_tenant_scoped(api):  # noqa: F811
    live_doc(api)
    A.inline(api)
    rid, _ = drafted(api)
    submit(api, rid)
    assert api.get(URL, headers=staff(api)).status_code == 403
    assert api.get(URL).status_code == 401
    assert api.get(URL, headers=other_admin(api)).json()["knowledge"]["requests"] == 0
    assert api.get(URL, params={"days": 0}, headers=admin(api)).status_code == 422


def test_support_operations_are_counted_per_conversation(api):  # noqa: F811
    a = api.get("/api/admin/analytics", params={"days": 7}, headers=admin(api)).json()["support"]
    assert set(a) >= {"conversations", "ai_resolved", "escalated", "ai_resolution_rate", "takeover_rate", "unresolved", "definitions"}
    assert a["ai_resolved"] + a["escalated"] == a["conversations"]
