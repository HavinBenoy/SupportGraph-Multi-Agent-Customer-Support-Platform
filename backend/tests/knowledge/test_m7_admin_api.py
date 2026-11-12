"""M7: the administrator UI's server contract (docs/M6.md section 13) — documents table filters + pagination, version compare, review queue, audit browser with
the before_id cursor, import wizard, and how these meet the final-M6 permanent deletion. All tenant-scoped and administrator-only."""

from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge.kbhelp import BASE, FEES_V2, SECRET_TEXT, add_version, admin, create, go_live, other_admin
from tests.knowledge.kwhelp import staff
from tests.rag.conftest import embedder, ingested  # noqa: F401


def test_meta_tells_the_ui_the_actions_and_the_deletion_rules(api):  # noqa: F811
    m = api.get(f"{BASE}/meta", headers=admin(api)).json()
    assert {"document_purged", "document_legal_hold_set", "document_archived"} <= set(m["audit_actions"])
    assert m["purge_enabled"] is True and m["purge_min_archived_days"] == 0


def test_the_documents_table_filters_and_pages_on_the_server(api):  # noqa: F811
    for i in range(7):
        create(api, doc_id=f"doc-{i}", title=f"Doc {i}", category="faq" if i % 2 else "fees", content=f"# Doc {i}\n\nBody {i}.\n")
    r = api.get(f"{BASE}/documents", params={"page_size": 3, "page": 3}, headers=admin(api)).json()
    assert r["total"] == 7 and len(r["items"]) == 1 and r["page"] == 3
    r = api.get(f"{BASE}/documents", params={"category": "faq", "status": "draft"}, headers=admin(api)).json()
    assert r["total"] == 3 and all(d["category"] == "faq" and d["status"] == "draft" and d["legal_hold"] is False for d in r["items"])
    assert api.get(f"{BASE}/documents", params={"status": "bogus"}, headers=admin(api)).status_code == 422
    assert api.get(f"{BASE}/documents", headers=other_admin(api)).json()["total"] == 0               # another tenant sees none of them
    assert api.get(f"{BASE}/documents", headers=staff(api)).status_code == 403


def test_version_compare_and_audit_cursor(api):  # noqa: F811
    go_live(api)
    add_version(api, "fees-test", FEES_V2)
    c = api.get(f"{BASE}/documents/fees-test/compare", params={"from": 1, "to": 2}, headers=admin(api)).json()
    assert c["identical"] is False and c["stats"]["added_lines"] >= 1
    first = api.get(f"{BASE}/audit", params={"limit": 2}, headers=admin(api)).json()
    assert len(first["items"]) == 2 and first["next_before_id"]
    older = api.get(f"{BASE}/audit", params={"limit": 2, "before_id": first["next_before_id"]}, headers=admin(api)).json()
    assert all(e["id"] < first["next_before_id"] for e in older["items"])
    assert not {e["id"] for e in first["items"]} & {e["id"] for e in older["items"]}               # no event shown twice
    assert SECRET_TEXT not in str(first) + str(older)
    assert api.get(f"{BASE}/audit", headers=other_admin(api)).json()["items"] == []


def test_the_audit_browser_shows_the_permanent_deletion_tombstone_and_nothing_of_the_text(api):  # noqa: F811
    create(api, doc_id="draft-x", title="Draft X", content=f"# Draft X\n\n{SECRET_TEXT}\n")
    d = api.get(f"{BASE}/documents/draft-x", headers=admin(api)).json()
    assert api.post(f"{BASE}/documents/draft-x/purge", json={"confirm_doc_id": "draft-x", "confirm_title": "Draft X", "expected_row_version": d["row_version"]},
                    headers=admin(api)).json()["outcome"] == "deleted"
    ev = api.get(f"{BASE}/audit", params={"action": "document_purged"}, headers=admin(api)).json()["items"]
    assert len(ev) == 1 and ev[0]["doc_id"] == "draft-x" and ev[0]["actor"] == "admin1"
    hist = api.get(f"{BASE}/audit", params={"doc_id": "draft-x"}, headers=admin(api)).json()["items"]
    assert [e["action"] for e in hist][0] == "document_purged" and "document_created" in [e["action"] for e in hist]
    assert SECRET_TEXT not in str(hist) and "Draft X" not in str(hist)
    assert api.get(f"{BASE}/documents", params={"q": "draft"}, headers=admin(api)).json()["total"] == 0


def test_the_review_queue_and_import_list_are_admin_only(api):  # noqa: F811
    assert api.get(f"{BASE}/proposals", headers=admin(api)).json()["items"] == []
    assert api.get(f"{BASE}/proposals", params={"status": "nope"}, headers=admin(api)).status_code == 422
    for path in ("/proposals", "/feedback", "/audit", "/meta"):
        assert api.get(f"{BASE}{path}", headers=staff(api)).status_code == 403
    assert api.post(f"{BASE}/import-disk", headers=staff(api)).status_code == 403
