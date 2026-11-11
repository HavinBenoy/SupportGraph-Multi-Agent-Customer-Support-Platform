"""Part 3 folder management: move (reparent) and safe delete, with cycles, depth, name clashes, concurrency, permissions, tenant isolation and audit."""

from sqlalchemy import select

from app.db.models import KbEvent
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge.kbhelp import BASE, admin, other_admin
from tests.knowledge.kwhelp import folder, live_doc, staff
from tests.rag.conftest import embedder, ingested  # noqa: F401


def tree(api):
    r = api.get(f"{BASE}/tree", headers=admin(api))
    assert r.status_code == 200
    return {f["folder_id"]: f for f in r.json()["folders"]}


def move(api, fid, parent, rv=None, h=None):
    return api.put(f"{BASE}/folders/{fid}/parent", json={"parent_id": parent, **({"expected_row_version": rv} if rv is not None else {})}, headers=h or admin(api))


def test_root_and_nested_folders_can_be_created_and_moved_with_their_contents(api):  # noqa: F811
    course = folder(api, "course")
    ds = folder(api, "Data-Science")                                       # a second top-level structure
    ml = folder(api, "ML", course["folder_id"])
    r = move(api, ml["folder_id"], ds["folder_id"])
    assert r.status_code == 200 and r.json()["path"] == "Data-Science/ML"
    assert move(api, ml["folder_id"], None).json()["path"] == "ML"           # back to the top level
    with session_scope() as db:
        ev = db.execute(select(KbEvent).where(KbEvent.tenant_id == T, KbEvent.action == "folder_moved")).scalars().all()
    assert len(ev) == 2


def test_cycles_depth_and_name_clashes_are_refused(api):  # noqa: F811
    a = folder(api, "A")
    b = folder(api, "B", a["folder_id"])
    assert move(api, a["folder_id"], b["folder_id"]).status_code == 422          # into its own sub-folder
    assert move(api, a["folder_id"], a["folder_id"]).status_code == 422          # into itself
    chain = [a]
    for i in range(5):
        chain.append(folder(api, f"L{i}", chain[-1]["folder_id"]))              # depth 6 under A
    c = folder(api, "C")
    folder(api, "C2", c["folder_id"])
    assert move(api, c["folder_id"], chain[-1]["folder_id"]).status_code == 422  # would be 8 deep
    folder(api, "B", c["folder_id"])
    assert move(api, b["folder_id"], c["folder_id"]).status_code == 409          # a sibling "B" already exists there


def test_stale_row_version_is_a_conflict(api):  # noqa: F811
    a, b = folder(api, "A"), folder(api, "B")
    assert move(api, a["folder_id"], b["folder_id"], rv=a["row_version"] + 5).status_code == 409


def test_only_an_empty_folder_can_be_deleted(api):  # noqa: F811
    a = folder(api, "A")
    child = folder(api, "child", a["folder_id"])
    d = api.delete(f"{BASE}/folders/{a['folder_id']}", headers=admin(api))
    assert d.status_code == 409 and "1 folder(s), 0 document(s)" in d.text
    live_doc(api)
    doc_rv = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["row_version"]
    assert api.put(f"{BASE}/documents/fees-test/folder", json={"folder_id": child["folder_id"], "expected_row_version": doc_rv}, headers=admin(api)).status_code == 200
    d2 = api.delete(f"{BASE}/folders/{child['folder_id']}", headers=admin(api))
    assert d2.status_code == 409 and "1 document(s)" in d2.text
    empty = folder(api, "empty")
    assert api.delete(f"{BASE}/folders/{empty['folder_id']}?expected_row_version=7", headers=admin(api)).status_code == 409
    ok = api.delete(f"{BASE}/folders/{empty['folder_id']}?expected_row_version=0", headers=admin(api))
    assert ok.status_code == 200 and ok.json()["deleted"] is True and empty["folder_id"] not in tree(api)
    assert api.get(f"{BASE}/documents/fees-test", headers=admin(api)).status_code == 200


def test_staff_and_other_tenants_cannot_move_or_delete(api):  # noqa: F811
    a, b = folder(api, "A"), folder(api, "B")
    assert move(api, a["folder_id"], b["folder_id"], h=staff(api)).status_code == 403
    assert api.delete(f"{BASE}/folders/{a['folder_id']}", headers=staff(api)).status_code == 403
    assert move(api, a["folder_id"], None, h=other_admin(api)).status_code == 404
    assert api.delete(f"{BASE}/folders/{a['folder_id']}", headers=other_admin(api)).status_code == 404
