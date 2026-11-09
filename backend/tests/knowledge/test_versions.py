"""M6 immutable versions: numbering, history, compare, restore-as-new-version, immutability guards, concurrency."""

import threading

import pytest
from sqlalchemy import select, update

from app.config import Settings
from app.db.models import KbImmutableError, KbVersion
from app.db.session import session_scope
from app.knowledge import store
from app.knowledge.errors import KbError
from tests.api.conftest import T, api  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge.kbhelp import BASE, FEES_V1, FEES_V2, FEES_V3, add_version, admin, create, process, publish
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401


def test_versions_are_numbered_in_order_and_the_history_lists_them_newest_first(api):  # noqa: F811
    create(api)
    assert add_version(api, "fees-test", FEES_V2, "three instalments")["version"]["version"] == 2
    assert add_version(api, "fees-test", FEES_V3, "four", title="Fees (new)")["version"]["version"] == 3
    h = api.get(f"{BASE}/documents/fees-test/versions", headers=admin(api)).json()
    assert [v["version"] for v in h["items"]] == [3, 2, 1] and h["total"] == 3
    assert [v["change_note"] for v in h["items"]] == ["four", "three instalments", "Initial version"]
    assert "content" not in h["items"][0]
    d = api.get(f"{BASE}/documents/fees-test/versions/3", headers=admin(api)).json()
    assert d["content"] == FEES_V3 and d["title"] == "Fees (new)" and d["created_by"] == "admin1"
    assert api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["latest_version"] == 3
    assert api.get(f"{BASE}/documents/fees-test/versions/9", headers=admin(api)).status_code == 404
    assert api.get(f"{BASE}/documents/fees-test/versions/0", headers=admin(api)).status_code in (404, 422)


def test_a_new_version_identical_to_the_latest_is_refused_and_a_note_is_required(api):  # noqa: F811
    create(api)
    assert api.post(f"{BASE}/documents/fees-test/versions", json={"content": FEES_V1, "change_note": "same"}, headers=admin(api)).status_code == 409
    assert api.post(f"{BASE}/documents/fees-test/versions", json={"content": FEES_V2, "change_note": "  "}, headers=admin(api)).status_code == 422
    assert api.post(f"{BASE}/documents/fees-test/versions", json={"content": FEES_V2}, headers=admin(api)).status_code == 422
    assert api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["latest_version"] == 1


def test_compare_reports_a_line_diff_and_changed_fields(api):  # noqa: F811
    create(api)
    add_version(api, "fees-test", FEES_V2, "n", title="Fees v2")
    c = api.get(f"{BASE}/documents/fees-test/compare?from=1&to=2", headers=admin(api)).json()
    assert c["from"]["version"] == 1 and c["to"]["version"] == 2 and c["title_changed"] is True
    joined = "\n".join(c["diff"]) if isinstance(c["diff"], list) else str(c["diff"])
    assert "-Students may pay the programme fee in two" in joined and "+Students may pay the programme fee in three" in joined
    assert api.get(f"{BASE}/documents/fees-test/compare?from=1&to=9", headers=admin(api)).status_code == 404
    assert api.get(f"{BASE}/documents/fees-test/compare?from=0&to=1", headers=admin(api)).status_code == 422
    assert api.get(f"{BASE}/documents/fees-test/compare?from=1", headers=admin(api)).status_code == 422


def test_restore_creates_a_new_version_and_keeps_every_earlier_one(api):  # noqa: F811
    h = admin(api)
    create(api)
    add_version(api, "fees-test", FEES_V2, "two")
    add_version(api, "fees-test", FEES_V3, "three")
    r = api.post(f"{BASE}/documents/fees-test/versions/1/restore", json={}, headers=h)
    assert r.status_code == 201, r.text
    v4 = r.json()["version"]
    assert v4["version"] == 4 and v4["restored_from"] == 1 and v4["status"] == "draft" and "restore" in v4["change_note"].lower()
    bodies = {n: api.get(f"{BASE}/documents/fees-test/versions/{n}", headers=h).json() for n in (1, 2, 3, 4)}
    assert [bodies[n]["content"] for n in (1, 2, 3, 4)] == [FEES_V1, FEES_V2, FEES_V3, FEES_V1]
    assert bodies[4]["checksum"] == bodies[1]["checksum"]
    assert api.post(f"{BASE}/documents/fees-test/versions/4/restore", json={}, headers=h).status_code == 409           # already the latest content
    assert api.post(f"{BASE}/documents/fees-test/versions/7/restore", json={}, headers=h).status_code == 404
    assert api.post(f"{BASE}/documents/fees-test/versions/1/restore", json={"change_note": "back to start"}, headers=h).status_code == 409   # v4 already equals v1


def test_restored_version_carries_the_original_title_and_must_be_processed_before_it_can_go_live(api):  # noqa: F811
    h = admin(api)
    create(api, title="Original")
    add_version(api, "fees-test", FEES_V2, "two", title="Renamed")
    v3 = api.post(f"{BASE}/documents/fees-test/versions/1/restore", json={}, headers=h).json()["version"]
    assert api.get(f"{BASE}/documents/fees-test/versions/{v3['version']}", headers=h).json()["title"] == "Original"
    assert publish(api, "fees-test", v3["version"], expect=409)
    process(api, "fees-test", v3["version"])
    assert publish(api, "fees-test", v3["version"])["version"] == v3["version"]


def test_stale_expected_row_version_is_a_conflict_not_a_lost_update(api):  # noqa: F811
    create(api)
    rv = api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["row_version"]
    assert api.post(f"{BASE}/documents/fees-test/versions", json={"content": FEES_V2, "change_note": "a", "expected_row_version": rv}, headers=admin(api)).status_code == 201
    r = api.post(f"{BASE}/documents/fees-test/versions", json={"content": FEES_V3, "change_note": "b", "expected_row_version": rv}, headers=admin(api))
    assert r.status_code == 409
    assert api.get(f"{BASE}/documents/fees-test", headers=admin(api)).json()["latest_version"] == 2


def test_concurrent_new_versions_get_distinct_consecutive_numbers_and_none_is_lost(api):  # noqa: F811
    create(api)
    s = Settings()
    out, errs = [], []

    def worker(i):
        try:
            with session_scope() as db:
                v = store.create_version(db, T, f"w{i}", s, "fees-test", content=f"# Fees\n\nConcurrent edit number {i} with enough words.\n", change_note=f"c{i}")
                out.append(v.version_no)
        except Exception as e:                      # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert sorted(out) == [2, 3, 4, 5, 6, 7]
    with session_scope() as db:
        nums = [r[0] for r in db.execute(select(KbVersion.version_no).where(KbVersion.doc_id == "fees-test").order_by(KbVersion.version_no)).all()]
    assert nums == [1, 2, 3, 4, 5, 6, 7]


# ---- immutability ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("col,val", [("content", "tampered"), ("checksum", "0" * 64), ("change_note", "x"), ("created_by", "mallory"), ("version_no", 99), ("title", "t")])
def test_a_saved_version_cannot_be_changed_through_the_orm(api, col, val):  # noqa: F811
    create(api)
    with pytest.raises(KbImmutableError):
        with session_scope() as db:
            v = db.get(KbVersion, (T, "fees-test", 1))
            setattr(v, col, val)
            db.flush()
    with session_scope() as db:
        assert db.get(KbVersion, (T, "fees-test", 1)).content == FEES_V1


def test_a_saved_version_cannot_be_deleted_through_the_orm(api):  # noqa: F811
    create(api)
    with pytest.raises(KbImmutableError):
        with session_scope() as db:
            db.delete(db.get(KbVersion, (T, "fees-test", 1)))
            db.flush()
    with session_scope() as db:
        assert db.get(KbVersion, (T, "fees-test", 1)) is not None


def test_no_http_route_can_edit_or_delete_a_version(api):  # noqa: F811
    create(api)
    for m in ("put", "patch", "delete"):
        r = getattr(api, m)(f"{BASE}/documents/fees-test/versions/1", headers=admin(api))
        assert r.status_code in (404, 405), (m, r.status_code)
    assert api.delete(f"{BASE}/documents/fees-test", headers=admin(api)).status_code in (404, 405)       # documents are archived, never deleted
