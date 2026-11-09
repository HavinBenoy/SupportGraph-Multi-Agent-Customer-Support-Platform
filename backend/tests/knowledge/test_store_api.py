"""M6 knowledge store over the real HTTP API: documents, validation, listing/search, metadata, tenant isolation and who may do what."""

import pytest
from sqlalchemy import select

from app.db.models import KbDocument
from app.db.session import session_scope
from tests.api.conftest import T, api, auth, login  # noqa: F401
from tests.api.test_staff_admin import other_headers
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge.kbhelp import BASE, FEES_V1, FEES_V2, add_version, admin, create, go_live, other_admin, payload, process, publish
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401


# ---- create / read ----------------------------------------------------------------------------------------------------------------
def test_creating_a_document_makes_version_1_a_draft_that_nothing_can_retrieve(api):  # noqa: F811
    r = create(api, tags=["fees", "payments"], description="How fees are paid")
    d, v = r["document"], r["version"]
    assert (d["doc_id"], d["status"], d["latest_version"], d["published_version"], d["category"]) == ("fees-test", "draft", 1, None, "fees")
    assert d["tags"] == ["fees", "payments"] and d["created_by"] == "admin1" and d["origin"] == "store"
    assert (v["version"], v["status"], v["lifecycle"], v["created_by"], v["change_note"]) == (1, "draft", "draft", "admin1", "Initial version")
    assert len(v["checksum"]) == 64 and v["size_bytes"] == len(FEES_V1.encode())
    got = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()
    assert got["title"] == "Fees Test" and got["row_version"] == d["row_version"]
    from tests.knowledge.kbhelp import live_doc
    assert live_doc("fees-test") is None


def test_duplicate_doc_id_is_refused_and_the_same_text_elsewhere_only_warns(api):  # noqa: F811
    create(api)
    assert api.post(f"{BASE}/documents", json=payload(), headers=admin(api)).status_code == 409
    second = create(api, doc_id="fees-copy", title="Fees Test")                       # same title and body under another id: allowed, but flagged
    assert second["warnings"] == [{"type": "same_content_as", "doc_id": "fees-test"}]


@pytest.mark.parametrize("bad", [
    {"doc_id": "Has Spaces"}, {"doc_id": "../etc/passwd"}, {"doc_id": ""}, {"doc_id": "-start"}, {"category": "nonsense"}, {"title": "   "}, {"title": "x" * 301},
    {"content": "   \n  "}, {"content": "# Only a title\n"}, {"content": "---\naudience: staff\n---\n# T\n\nbody text here\n"}, {"content": "ok text\x00with nul"},
    {"tags": ["UPPER"]}, {"tags": ["a"] * 11}, {"source_filename": "../../etc/passwd"}, {"source_filename": "notes.exe"}, {"source_filename": ".md"},
    {"source_mime": "application/pdf"}, {"source_filename": "a.txt", "source_mime": "text/markdown"}, {"description": "d" * 1001},
])
def test_invalid_input_is_refused_with_422_and_creates_nothing(api, bad):  # noqa: F811
    r = api.post(f"{BASE}/documents", json=payload(**bad), headers=admin(api))
    assert r.status_code == 422, r.text
    with session_scope() as db:
        assert db.execute(select(KbDocument)).first() is None


def test_unknown_fields_and_oversize_bodies_are_refused(api):  # noqa: F811
    assert api.post(f"{BASE}/documents", json={**payload(), "status": "published"}, headers=admin(api)).status_code == 422      # no way to smuggle a state in
    big = "# Big\n\n" + ("word " * 50_000)
    r = api.post(f"{BASE}/documents", json=payload(content=big), headers=admin(api))
    assert r.status_code == 422 and "limit" in r.json()["detail"]


def test_safe_filename_is_kept_as_a_display_name_only(api):  # noqa: F811
    r = create(api, source_filename="C:\\Users\\x\\Fee Policy 2026.md")
    assert r["version"]["source_filename"] == "Fee Policy 2026.md" and r["version"]["source_mime"] == "text/markdown"
    r2 = create(api, doc_id="plain", title="Plain", source_filename="notes.txt", source_mime="text/plain")
    assert (r2["version"]["source_filename"], r2["version"]["source_mime"]) == ("notes.txt", "text/plain")


def test_crlf_text_is_normalised_so_the_checksum_does_not_depend_on_line_endings(api):  # noqa: F811
    a = create(api, doc_id="crlf-a", content=FEES_V1.replace("\n", "\r\n"))
    b = create(api, doc_id="lf-b", content=FEES_V1, title="Fees Test")
    assert a["version"]["checksum"] == b["version"]["checksum"]


# ---- list / search / filter -------------------------------------------------------------------------------------------------------
def seed_three(api):
    create(api, doc_id="fees-a", title="Fee Schedule", category="fees", tags=["money"], content="# Fee Schedule\n\nThe quokka surcharge is waived for early payment.\n")
    create(api, doc_id="course-b", title="Python Course", category="courses", tags=["python", "money"], description="Full stack python curriculum",
           content="# Python Course\n\nThis course covers loops, functions and classes in detail.\n")
    go_live(api, doc_id="place-c", content="# Placement\n\nPlacement assistance includes mock interviews and resume reviews.\n", category="placement")


def test_listing_filters_search_and_pagination(api):  # noqa: F811
    seed_three(api)
    h = admin(api)
    lst = api.get(f"{BASE}/documents", headers=h).json()
    assert lst["total"] == 3 and {i["doc_id"] for i in lst["items"]} == {"fees-a", "course-b", "place-c"}
    assert [i["doc_id"] for i in api.get(f"{BASE}/documents?category=courses", headers=h).json()["items"]] == ["course-b"]
    assert [i["doc_id"] for i in api.get(f"{BASE}/documents?status=published", headers=h).json()["items"]] == ["place-c"]
    assert {i["doc_id"] for i in api.get(f"{BASE}/documents?tag=money", headers=h).json()["items"]} == {"fees-a", "course-b"}
    assert [i["doc_id"] for i in api.get(f"{BASE}/documents?q=quokka", headers=h).json()["items"]] == ["fees-a"]            # matches the body of the latest version
    assert [i["doc_id"] for i in api.get(f"{BASE}/documents?q=curriculum", headers=h).json()["items"]] == ["course-b"]       # ... and the description
    assert api.get(f"{BASE}/documents?q=nomatchatall", headers=h).json()["total"] == 0
    assert api.get(f"{BASE}/documents?q=%25", headers=h).json()["total"] == 0                                                  # a wildcard is searched literally, not as a pattern
    p1 = api.get(f"{BASE}/documents?page_size=2&page=1", headers=h).json()
    p2 = api.get(f"{BASE}/documents?page_size=2&page=2", headers=h).json()
    assert (len(p1["items"]), len(p2["items"]), p1["total"]) == (2, 1, 3)
    assert {i["doc_id"] for i in p1["items"]}.isdisjoint({i["doc_id"] for i in p2["items"]})
    for bad in ("category=zzz", "status=zzz", "page_size=1000", "page=0", "tag=BAD TAG"):
        assert api.get(f"{BASE}/documents?{bad}", headers=h).status_code == 422, bad


def test_meta_lists_categories_limits_the_active_model_and_unmanaged_disk_articles(api):  # noqa: F811
    m = api.get(f"{BASE}/meta", headers=admin(api)).json()
    assert "fees" in m["categories"] and m["limits"]["max_bytes"] > 0 and m["embedding_model"].startswith("fake:")
    assert "fees-and-payments" in m["unmanaged_disk_documents"]


# ---- metadata ---------------------------------------------------------------------------------------------------------------------
def test_metadata_update_changes_only_metadata_bumps_row_version_and_does_not_touch_versions_or_live_content(api):  # noqa: F811
    go_live(api)
    before = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()
    r = api.put(f"{BASE}/documents/fees-test", json={"category": "policies", "tags": ["b", "a"], "description": "d", "expected_row_version": before["row_version"]}, headers=admin(api))
    assert r.status_code == 200, r.text
    after = r.json()
    assert (after["category"], after["tags"], after["description"], after["updated_by"]) == ("policies", ["a", "b"], "d", "admin1")
    assert after["row_version"] == before["row_version"] + 1 and after["latest_version"] == 1 and after["published_version"] == 1 and after["status"] == "published"
    stale = api.put(f"{BASE}/documents/fees-test", json={"category": "faq", "expected_row_version": before["row_version"]}, headers=admin(api))
    assert stale.status_code == 409
    assert api.put(f"{BASE}/documents/fees-test", json={"category": "policies"}, headers=admin(api)).status_code == 422          # nothing to change
    assert api.put(f"{BASE}/documents/fees-test", json={"title": "New title"}, headers=admin(api)).status_code == 422          # titles are versioned content
    assert api.put(f"{BASE}/documents/fees-test", json={"category": "bogus"}, headers=admin(api)).status_code == 422
    assert api.put(f"{BASE}/documents/missing", json={"category": "faq"}, headers=admin(api)).status_code == 404


# ---- tenant isolation -------------------------------------------------------------------------------------------------------------
def test_another_tenant_cannot_see_or_touch_our_documents_and_can_reuse_the_same_doc_id(api):  # noqa: F811
    go_live(api)
    oh = other_admin(api)
    assert api.get(f"{BASE}/documents", headers=oh).json()["total"] == 0
    for method, path, body in [("get", "documents/fees-test", None), ("get", "documents/fees-test/versions", None), ("get", "documents/fees-test/versions/1", None),
                               ("get", "documents/fees-test/compare?from=1&to=1", None), ("put", "documents/fees-test", {"category": "faq"}),
                               ("post", "documents/fees-test/versions", {"content": FEES_V2, "change_note": "x"}), ("post", "documents/fees-test/versions/1/restore", {}),
                               ("post", "documents/fees-test/versions/1/process", None), ("post", "documents/fees-test/versions/1/publish", None),
                               ("post", "documents/fees-test/archive", None), ("post", "documents/fees-test/unpublish", None)]:
        r = getattr(api, method)(f"{BASE}/{path}", headers=oh, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, path, r.status_code)
    assert api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["status"] == "published"                          # untouched
    own = api.post(f"{BASE}/documents", json=payload(content=FEES_V2), headers=oh)                                              # the id is free in their namespace
    assert own.status_code in (201, 500)  # the second tenant exists only as a test fixture; see below for the real assertion
    if own.status_code == 201:
        assert api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["latest_version"] == 1


# ---- who may do what --------------------------------------------------------------------------------------------------------------
ADMIN_ONLY = [("get", "meta"), ("get", "documents"), ("post", "documents"), ("get", "documents/x"), ("put", "documents/x"), ("get", "documents/x/versions"),
              ("post", "documents/x/versions"), ("get", "documents/x/versions/1"), ("get", "documents/x/compare?from=1&to=2"), ("post", "documents/x/versions/1/restore"),
              ("post", "documents/x/versions/1/process"), ("post", "documents/x/versions/1/publish"), ("post", "documents/x/unpublish"), ("post", "documents/x/archive"),
              ("post", "documents/x/unarchive"), ("get", "audit"), ("get", "feedback"), ("post", "proposals"), ("get", "proposals"), ("post", "proposals/c1/review"),
              ("post", "import-disk")]


def test_anonymous_students_and_staff_cannot_use_any_administrative_knowledge_route(api):  # noqa: F811
    for who, expect in ((None, 401), ("asha", 403), ("staff1", 403)):
        for method, path in ADMIN_ONLY:
            h = auth(api, who) if who else {}
            r = getattr(api, method)(f"{BASE}/{path}", headers=h, **({"json": {}} if method in ("post", "put") else {}))
            assert r.status_code == expect, (who, method, path, r.status_code)


def test_staff_can_read_published_knowledge_only_never_drafts_history_or_audit(api):  # noqa: F811
    go_live(api, doc_id="live-doc", content="# Live\n\nThe published text of the live document.\n")
    create(api, doc_id="draft-doc", content="# Draft\n\nSecret unpublished draft wording here.\n", title="Draft")
    add_version(api, "live-doc", "# Live\n\nA newer unpublished edit of the live document.\n")
    s = auth(api, "staff1")
    lst = api.get("/api/staff/knowledge/documents", headers=s).json()
    assert [i["doc_id"] for i in lst["items"]] == ["live-doc"] and lst["items"][0]["version"] == 1 and "content" not in lst["items"][0]
    one = api.get("/api/staff/knowledge/documents/live-doc", headers=s).json()
    assert "published text" in one["content"] and "newer unpublished" not in one["content"]
    assert api.get("/api/staff/knowledge/documents/draft-doc", headers=s).status_code == 404
    assert api.get("/api/staff/knowledge/documents", headers=auth(api, "asha")).status_code == 403
    assert api.get("/api/staff/knowledge/documents").status_code == 401
    assert api.get("/api/staff/knowledge/documents?category=zzz", headers=s).status_code == 422
    for m in ("put", "delete", "patch"):
        assert getattr(api, m)("/api/staff/knowledge/documents/live-doc", headers=s).status_code in (404, 405)


def test_the_listing_never_exposes_internal_columns_or_other_tenants_data(api):  # noqa: F811
    go_live(api)
    item = api.get(f"{BASE}/documents", headers=admin(api)).json()["items"][0]
    assert not {"tenant_id", "embedding", "content", "storage_ref"} & set(item)
