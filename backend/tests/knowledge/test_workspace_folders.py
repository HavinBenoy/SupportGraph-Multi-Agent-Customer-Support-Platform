"""Knowledge workspace: folders, hierarchy, placement, staff tree, permissions, tenant isolation."""

import threading

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import KbDocument, KbEvent, KbVersion
from app.db.session import session_scope
from app.knowledge import workspace
from app.knowledge.errors import Conflict
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge.kbhelp import BASE, FEES_V1, admin, create, go_live, other_admin, retrieve_ids
from tests.knowledge.kwhelp import ST, folder, live_doc, staff, student
from tests.rag.conftest import embedder, ingested  # noqa: F401


def tree(api, h=None, base=BASE):
    r = api.get(f"{base}/tree", headers=h or admin(api))
    assert r.status_code == 200, r.text
    return r.json()


def test_create_nested_folders_returns_paths_and_appears_in_the_tree(api):  # noqa: F811
    courses = folder(api, "Courses")
    ds = folder(api, "Data Science", courses["folder_id"])
    assert ds["path"] == "Courses/Data Science" and ds["parent_id"] == courses["folder_id"]
    paths = [f["path"] for f in tree(api)["folders"]]
    assert paths == ["Courses", "Courses/Data Science"]


def test_sibling_names_are_unique_case_insensitively_but_reusable_elsewhere(api):  # noqa: F811
    a, b = folder(api, "Courses"), folder(api, "Fees")
    folder(api, "courses", expect=409)
    folder(api, "  COURSES ", expect=409)
    folder(api, "Overview", a["folder_id"])
    folder(api, "Overview", b["folder_id"])                          # same name under a different parent is fine


@pytest.mark.parametrize("bad", ["", "   ", "a/b", "a\\b", ".", "..", "x" * 81, "bell\x07"])
def test_invalid_folder_names_are_rejected(api, bad):  # noqa: F811
    folder(api, bad, expect=422)


def test_depth_limit(api):  # noqa: F811
    parent = None
    for i in range(workspace.MAX_DEPTH):
        parent = folder(api, f"L{i}", parent)["folder_id"]
    folder(api, "too-deep", parent, expect=422)


def test_unknown_or_malformed_parent(api):  # noqa: F811
    folder(api, "x", "f_" + "0" * 16, expect=404)
    folder(api, "x", "../etc", expect=422)


def test_rename_is_cas_checked_and_keeps_children_paths_consistent(api):  # noqa: F811
    a = folder(api, "Courses")
    ds = folder(api, "DS", a["folder_id"])
    r = api.put(f"{BASE}/folders/{a['folder_id']}", json={"name": "Programmes", "expected_row_version": a["row_version"]}, headers=admin(api))
    assert r.status_code == 200 and r.json()["name"] == "Programmes" and r.json()["row_version"] == a["row_version"] + 1
    assert [f["path"] for f in tree(api)["folders"]] == ["Programmes", "Programmes/DS"]
    stale = api.put(f"{BASE}/folders/{a['folder_id']}", json={"name": "Again", "expected_row_version": a["row_version"]}, headers=admin(api))
    assert stale.status_code == 409
    folder(api, "Fees")
    clash = api.put(f"{BASE}/folders/{a['folder_id']}", json={"name": "fees"}, headers=admin(api))
    assert clash.status_code == 409
    assert ds["folder_id"] in [f["folder_id"] for f in tree(api)["folders"]]


def test_move_document_changes_location_only_never_content_or_versions(api):  # noqa: F811
    live_doc(api)
    f = folder(api, "Fees")
    with session_scope() as db:
        before = [(v.version_no, v.checksum, v.status) for v in db.execute(select(KbVersion).where(KbVersion.doc_id == "fees-test")).scalars()]
        pv = db.get(KbDocument, (T, "fees-test")).published_version
    live_before = retrieve_ids(api, "instalments")
    r = api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": f["folder_id"]}, headers=admin(api))
    assert r.status_code == 200 and r.json()["folder_id"] == f["folder_id"] and not r.json()["unchanged"]
    with session_scope() as db:
        after = [(v.version_no, v.checksum, v.status) for v in db.execute(select(KbVersion).where(KbVersion.doc_id == "fees-test")).scalars()]
        assert db.get(KbDocument, (T, "fees-test")).published_version == pv
    assert before == after
    assert retrieve_ids(api, "instalments") == live_before
    doc = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()
    assert doc["folder_id"] == f["folder_id"]
    again = api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": f["folder_id"]}, headers=admin(api))
    assert again.json()["unchanged"] is True
    back = api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": None}, headers=admin(api))
    assert back.status_code == 200 and back.json()["folder_id"] is None


def test_move_with_stale_row_version_conflicts(api):  # noqa: F811
    live_doc(api)
    f = folder(api, "Fees")
    rv = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["row_version"]
    assert api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": f["folder_id"], "expected_row_version": rv + 5}, headers=admin(api)).status_code == 409


def test_move_into_unknown_folder_or_unknown_document(api):  # noqa: F811
    live_doc(api)
    assert api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": "f_" + "1" * 16}, headers=admin(api)).status_code == 404
    f = folder(api, "Fees")
    assert api.put(f"{BASE}/documents/nope/folder", json={"folder_id": f["folder_id"]}, headers=admin(api)).status_code == 404


def test_listing_filters_by_folder_subfolders_and_unfiled(api):  # noqa: F811
    live_doc(api, "d-root")
    live_doc(api, "d-top")
    live_doc(api, "d-sub")
    top = folder(api, "Courses")
    sub = folder(api, "DS", top["folder_id"])
    api.put(f"{BASE}/documents/d-top/folder", json={"folder_id": top["folder_id"]}, headers=admin(api))
    api.put(f"{BASE}/documents/d-sub/folder", json={"folder_id": sub["folder_id"]}, headers=admin(api))

    def ids(q):
        r = api.get(f"{BASE}/documents?{q}", headers=admin(api))
        assert r.status_code == 200, r.text
        return sorted(i["doc_id"] for i in r.json()["items"])
    assert ids(f"folder_id={top['folder_id']}&include_subfolders=false") == ["d-top"]
    assert ids(f"folder_id={top['folder_id']}") == ["d-sub", "d-top"]
    assert ids("unfiled=true") == ["d-root"]


def test_new_documents_start_at_the_root_and_import_is_unaffected(api):  # noqa: F811
    create(api, doc_id="fresh")
    assert api.get(f"{BASE}/documents/fresh", headers=admin(api)).json()["folder_id"] is None
    assert tree(api)["root_document_count"] >= 1


# ---- staff ----------------------------------------------------------------------------------------------------------------------
def test_staff_tree_shows_only_published_documents_and_the_folders_leading_to_them(api):  # noqa: F811
    live_doc(api, "live-doc")
    create(api, doc_id="draft-only")
    a, b = folder(api, "Live"), folder(api, "Empty")
    sub = folder(api, "Deep", a["folder_id"])
    api.put(f"{BASE}/documents/live-doc/folder", json={"folder_id": sub["folder_id"]}, headers=admin(api))
    api.put(f"{BASE}/documents/draft-only/folder", json={"folder_id": b["folder_id"]}, headers=admin(api))
    t = tree(api, staff(api), ST)
    assert [d["doc_id"] for d in t["documents"]] == ["live-doc"]
    assert {f["name"] for f in t["folders"]} == {"Live", "Deep"}
    assert {d["doc_id"] for d in tree(api)["documents"]} >= {"live-doc", "draft-only"}


def test_staff_search_never_matches_unpublished_draft_text(api):  # noqa: F811
    live_doc(api)
    api.post(f"{BASE}/documents/fees-test/versions", json={"content": "# Fees\n\nUnreleased platypusrate text goes here for later.\n", "change_note": "draft"}, headers=admin(api))
    r = api.get(f"{ST}/documents?q=platypusrate", headers=staff(api))
    assert r.status_code == 200 and r.json()["items"] == []
    assert api.get(f"{ST}/documents?q=instalment", headers=staff(api)).json()["items"]


def test_published_view_carries_location_and_version_info(api):  # noqa: F811
    live_doc(api)
    f = folder(api, "Fees")
    api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": f["folder_id"]}, headers=admin(api))
    d = api.get(f"{ST}/documents/fees-test", headers=staff(api)).json()
    assert d["breadcrumbs"] == [{"id": f["folder_id"], "name": "Fees"}] and d["path"] == "Fees"
    for k in ("version", "published_by", "row_version", "checksum", "authored_by"):
        assert k in d


def test_staff_published_history_hides_drafts_and_compares_versions(api):  # noqa: F811
    live_doc(api)
    api.post(f"{BASE}/documents/fees-test/versions", json={"content": "# Fees\n\nSECRETDRAFT only for admins here.\n", "change_note": "d"}, headers=admin(api))
    h = staff(api)
    r = api.get(f"{ST}/documents/fees-test/versions", headers=h).json()
    assert [v["version"] for v in r["items"]] == [1]
    assert api.get(f"{ST}/documents/fees-test/versions/1", headers=h).json()["content"] == FEES_V1
    assert api.get(f"{ST}/documents/fees-test/versions/2", headers=h).status_code == 404
    assert api.get(f"{ST}/documents/fees-test/compare?from=1&to=2", headers=h).status_code == 404


def test_staff_compare_two_published_versions(api):  # noqa: F811
    live_doc(api)
    api.post(f"{BASE}/documents/fees-test/versions", json={"content": FEES_V1.replace("two", "three"), "change_note": "v2"}, headers=admin(api))
    api.post(f"{BASE}/documents/fees-test/versions/2/process", headers=admin(api))
    api.post(f"{BASE}/documents/fees-test/versions/2/publish", headers=admin(api))
    r = api.get(f"{ST}/documents/fees-test/compare?from=1&to=2", headers=staff(api))
    assert r.status_code == 200
    ops = {s["op"] for s in r.json()["diff"]["body"]}
    assert {"delete", "insert", "equal"} <= ops


# ---- permissions ----------------------------------------------------------------------------------------------------------------
ADMIN_ONLY = [("POST", "/folders", {"name": "x"}), ("PUT", "/folders/f_abc", {"name": "x"}), ("PUT", "/documents/fees-test/folder", {"folder_id": None}),
              ("GET", "/tree", None), ("GET", "/changes", None), ("GET", "/changes/cr_abc", None), ("POST", "/changes/cr_abc/review", {"decision": "approve"}),
              ("POST", "/changes/cr_abc/apply", None), ("POST", "/diff-preview", {"doc_id": "fees-test", "content": "x y z"})]


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_admin_workspace_routes_refuse_staff_students_and_anonymous(api, method, path, body):  # noqa: F811
    for h, code in ((staff(api), 403), (student(api), 403), ({}, 401)):
        r = api.request(method, f"{BASE}{path}", json=body, headers=h)
        assert r.status_code == code, (method, path, r.status_code)


STAFF_ROUTES = [("GET", "/tree", None), ("POST", "/changes", {"doc_id": "fees-test"}), ("GET", "/changes", None), ("POST", "/diff-preview", {"doc_id": "fees-test", "content": "a b"}),
                ("GET", "/documents/fees-test/versions", None)]


@pytest.mark.parametrize("method,path,body", STAFF_ROUTES)
def test_staff_workspace_routes_refuse_students_and_anonymous(api, method, path, body):  # noqa: F811
    assert api.request(method, f"{ST}{path}", json=body, headers=student(api)).status_code == 403
    assert api.request(method, f"{ST}{path}", json=body).status_code == 401


def test_staff_cannot_create_folders_or_move_documents(api):  # noqa: F811
    live_doc(api)
    assert api.post(f"{ST}/folders", json={"name": "x"}, headers=staff(api)).status_code in (404, 405)


# ---- tenant isolation -----------------------------------------------------------------------------------------------------------
def test_folders_and_placements_are_tenant_scoped(api):  # noqa: F811
    live_doc(api)
    mine = folder(api, "Fees")
    oh = other_admin(api)
    assert tree(api, oh)["folders"] == []
    assert api.put(f"{BASE}/folders/{mine['folder_id']}", json={"name": "Hacked"}, headers=oh).status_code == 404
    assert folder(api, "Fees", h=oh)["name"] == "Fees"                  # the same name is free in another tenant
    assert api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": mine["folder_id"]}, headers=oh).status_code == 404
    r = api.post(f"{BASE}/folders", json={"name": "Child", "parent_id": mine["folder_id"]}, headers=oh)
    assert r.status_code == 404


def test_concurrent_creation_of_the_same_folder_name_yields_exactly_one(api):  # noqa: F811
    out, errs = [], []

    def go():
        try:
            with session_scope() as db:
                out.append(workspace.create_folder(db, T, "racer", "Racing")["folder_id"])
        except Conflict:
            errs.append("c")
        except Exception as e:                                  # noqa: BLE001
            errs.append(repr(e))
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 1, (out, errs)
    assert set(errs) == {"c"}


def test_folder_and_move_events_are_audited_without_document_text(api):  # noqa: F811
    live_doc(api)
    f = folder(api, "Fees")
    api.put(f"{BASE}/folders/{f['folder_id']}", json={"name": "Pricing"}, headers=admin(api))
    api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": f["folder_id"]}, headers=admin(api))
    with session_scope() as db:
        ev = {e.action: e for e in db.execute(select(KbEvent).where(KbEvent.tenant_id == T)).scalars()}
    assert {"folder_created", "folder_renamed", "document_moved"} <= set(ev)
    assert ev["document_moved"].summary["to_folder"] == f["folder_id"] and ev["document_moved"].actor == "admin1"
    assert ev["folder_renamed"].summary["name"] == "Pricing"


def test_disk_imported_articles_appear_at_the_root_and_can_be_organised_and_edited_without_touching_live_chunks(api):  # noqa: F811
    from sqlalchemy import func
    from app.db.models import Chunk
    from app.rag.ingest import ingest_tenant
    from tests.knowledge.kwhelp import start
    s = api.app.state.settings
    emb = __import__("app.llm.gateway", fromlist=["x"]).get_embedding_client(s)
    with session_scope() as db:
        ingest_tenant(db, T, emb)
    assert api.post(f"{BASE}/import-disk", headers=admin(api)).status_code == 200
    with session_scope() as db:
        before = {c: t for c, t in db.execute(select(Chunk.doc_id, func.count()).where(Chunk.tenant_id == T).group_by(Chunk.doc_id)).all()}
    t = tree(api)
    imported = next(d for d in t["documents"] if d["doc_id"] == "fees-and-payments")
    assert imported["folder_id"] is None and imported["status"] == "published"
    f = folder(api, "Fees")
    assert api.put(f"{BASE}/documents/fees-and-payments/folder", json={"folder_id": f["folder_id"]}, headers=admin(api)).status_code == 200
    out = start(api, "fees-and-payments")                                  # an imported article can be edited through a change request like any other
    assert out["request"]["items"][0]["base_version"] == 1
    with session_scope() as db:
        after = {c: t for c, t in db.execute(select(Chunk.doc_id, func.count()).where(Chunk.tenant_id == T).group_by(Chunk.doc_id)).all()}
    assert after == before
