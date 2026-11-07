"""M5 admin API: who may do what, validation, concurrency, persistence across a restart, tenant isolation, metrics/retention/recordings endpoints."""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.db.models import Call, CallRecording, TrialEvent
from app.db.session import session_scope
from app.graph.support import build_support_graph
from app.trial.recording import LocalProvider
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.api.test_staff_admin import other_headers
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.calls.test_calls_api import staff2  # noqa: F401
from tests.calls.test_m5_core import mk_call, rec_row
from tests.rag.conftest import embedder, ingested, settings  # noqa: F401

ADMIN_ROUTES = [("get", ""), ("put", ""), ("get", "/events"), ("get", "/metrics"), ("get", "/feedback"), ("post", "/feedback/c_x/review"), ("get", "/retention"),
                ("post", "/retention/purge"), ("get", "/recordings"), ("post", "/recordings/rec_x/hold"), ("get", "/recordings/rec_x/audio/caller")]
BODY = {"put": {"expected_version": 0, "enabled": True}, "post": {"status": "reviewed", "hold": True, "reason": "x", "dry_run": True}}


def put(api, headers, version=None, **patch):
    if version is None:
        version = api.get("/api/admin/trial", headers=headers).json()["settings"]["version"]
    return api.put("/api/admin/trial", headers=headers, json={"expected_version": version, **patch})


@pytest.fixture
def local(api, tmp_path):  # noqa: F811
    api.app.state.recording_provider = LocalProvider(str(tmp_path / "recordings"))
    return api.app.state.recording_provider


def test_every_admin_trial_route_needs_an_administrator(api):  # noqa: F811
    staff, student = auth(api, "staff1"), auth(api, "asha")
    for method, suffix in ADMIN_ROUTES:
        url = f"/api/admin/trial{suffix}"
        body = {"json": BODY[method]} if method in BODY else {}
        assert getattr(api, method)(url, **body).status_code == 401, url
        assert getattr(api, method)(url, headers=student, **body).status_code == 403, url
        assert getattr(api, method)(url, headers=staff, **body).status_code == 403, url      # staff cannot change or read trial administration
    assert api.get("/api/staff/trial").status_code == 401
    assert api.get("/api/staff/trial", headers=student).status_code == 403
    assert api.get("/api/staff/trial", headers=staff).json() == {"enabled": False, "recording_offered": False}


def test_default_state_is_off_with_no_provider_and_no_return_to_ai_anywhere(api):  # noqa: F811
    r = api.get("/api/admin/trial", headers=auth(api, "admin1")).json()
    assert (r["settings"]["enabled"], r["settings"]["version"], r["settings"]["recording_offered"]) == (False, 0, False)
    assert r["recording"]["provider"] == "none" and r["recording"]["operational"] is False and "never recorded" in r["recording"]["note"]
    assert r["settings"]["disclosure_text"].startswith("You're speaking with our AI support assistant.")
    assert "{days}" not in r["settings"]["recording_notice_rendered"]
    paths = " ".join(api.get("/api/openapi.json").json()["paths"])
    assert "/return" not in paths and "return_to_ai" not in paths.lower()


def test_enable_disable_validation_and_audit(api):  # noqa: F811
    adm = auth(api, "admin1")
    r = put(api, adm, enabled=True)
    assert r.status_code == 200 and r.json()["settings"]["enabled"] is True and r.json()["settings"]["version"] == 1 and r.json()["settings"]["updated_by"] == "admin1"
    assert api.get("/api/staff/trial", headers=auth(api, "staff1")).json()["enabled"] is True
    assert put(api, adm, enabled=False).json()["settings"]["enabled"] is False
    for bad in ({"enabled": "yes"}, {"retention_recording_days": 0}, {"withdrawal_policy": "shred"}, {"disclosure_text": "short"}, {"unknown": 1}):
        assert put(api, adm, **bad).status_code == 422, bad
    assert api.put("/api/admin/trial", headers=adm, json={"enabled": True}).status_code == 422             # expected_version is required
    assert put(api, adm, recording_offered=True).status_code == 409                                        # no provider on this server
    ev = api.get("/api/admin/trial/events", headers=adm).json()
    assert [e["kind"] for e in ev][:2] == ["trial_disabled", "trial_enabled"] and ev[0]["actor"] == "admin1"
    assert api.get("/api/admin/trial/events?limit=0", headers=adm).status_code == 422


def test_concurrent_edits_one_wins_and_the_other_is_told_to_refresh(api):  # noqa: F811
    adm = auth(api, "admin1")
    put(api, adm, enabled=True)
    gate, out = threading.Barrier(2), []

    def go(days):
        gate.wait()
        out.append(api.put("/api/admin/trial", headers=adm, json={"expected_version": 1, "retention_recording_days": days}).status_code)

    ts = [threading.Thread(target=go, args=(d,)) for d in (10, 20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == [200, 409]
    s = api.get("/api/admin/trial", headers=adm).json()["settings"]
    assert s["version"] == 2 and s["retention_recording_days"] in (10, 20)


def test_settings_survive_a_restart_and_are_isolated_per_tenant(api):  # noqa: F811
    adm = auth(api, "admin1")
    assert put(api, adm, enabled=True, retention_recording_days=12, disclosure_text="Our assistant is an AI; staff may listen in during this trial.").status_code == 200
    other = other_headers(api, "otheradmin", "admin")
    assert api.get("/api/admin/trial", headers=other).json()["settings"]["enabled"] is False              # another tenant is unaffected
    assert put(api, other, enabled=True).status_code == 200 and api.get("/api/admin/trial", headers=other).json()["settings"]["version"] == 1
    assert api.get("/api/admin/trial", headers=adm).json()["settings"]["version"] == 1
    s = settings()
    app2 = create_app(s, graph=build_support_graph(settings=s))                                             # "restart": a new process, same database
    with TestClient(app2) as c2:
        got = c2.get("/api/admin/trial", headers=auth(c2, "admin1")).json()["settings"]
        assert (got["enabled"], got["retention_recording_days"], got["version"], got["updated_by"]) == (True, 12, 1, "admin1")
        assert got["disclosure_text"].startswith("Our assistant is an AI")
        assert c2.get("/api/staff/trial", headers=auth(c2, "staff1")).json()["enabled"] is True


def test_recording_can_be_offered_only_when_a_provider_exists_and_a_terms_change_bumps_the_policy(api, local):  # noqa: F811
    adm = auth(api, "admin1")
    r = put(api, adm, enabled=True, recording_offered=True)
    assert r.status_code == 200 and r.json()["recording"] == {"provider": "local", "operational": True, "note": r.json()["recording"]["note"]}
    assert r.json()["settings"]["consent_policy_version"] == 1
    assert put(api, adm, withdrawal_policy="keep_until_expiry").json()["settings"]["consent_policy_version"] == 2
    assert api.get("/api/staff/trial", headers=auth(api, "staff1")).json() == {"enabled": True, "recording_offered": True}
    put(api, adm, enabled=False)
    assert api.get("/api/staff/trial", headers=auth(api, "staff1")).json() == {"enabled": False, "recording_offered": False}      # off means off


def test_metrics_endpoint_dates_empty_state_and_isolation(api):  # noqa: F811
    adm = auth(api, "admin1")
    m = api.get("/api/admin/trial/metrics", headers=adm).json()
    assert m["empty"] is True and m["calls"]["total"] == 0 and m["human"]["intervention_rate"] is None
    for q in ("from=2026-13-01", "from=2026-11-07&to=2026-11-01", "from=2024-01-01&to=2026-11-07", "to=nonsense"):
        assert api.get(f"/api/admin/trial/metrics?{q}", headers=adm).status_code == 422, q
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        mk_call(db, "c_m1", created=now, ended=now + timedelta(seconds=30))
        mk_call(db, "c_m2", created=now - timedelta(days=60))
    today = now.date().isoformat()
    m = api.get(f"/api/admin/trial/metrics?from={today}&to={today}", headers=adm).json()
    assert m["calls"]["total"] == 1 and m["period"]["from"].startswith(today)
    wide = api.get(f"/api/admin/trial/metrics?from={(now - timedelta(days=90)).date()}&to={today}", headers=adm).json()
    assert wide["calls"]["total"] == 2
    assert api.get("/api/admin/trial/metrics", headers=other_headers(api, "otheradmin", "admin")).json()["calls"]["total"] == 0


def test_retention_and_recordings_endpoints_hide_storage_references_and_support_hold_purge_and_download(api, local, tmp_path):  # noqa: F811
    adm = auth(api, "admin1")
    root = tmp_path / "recordings"
    with session_scope() as db:
        mk_call(db, "c_r")
        rec_row(db, root, "rec_old", days_ago=40)
        rec_row(db, root, "rec_keep", days_ago=1)
    st = api.get("/api/admin/trial/retention", headers=adm).json()
    assert st["recordings"]["eligible_now"] == 1 and st["provider"]["name"] == "local" and st["policy"]["recording_days"] == 30
    lst = api.get("/api/admin/trial/recordings", headers=adm).json()
    assert {r["id"] for r in lst} == {"rec_old", "rec_keep"} and "storage_ref" not in str(lst) and str(root) not in str(lst) and "/rec_old" not in str(lst)
    assert api.get("/api/admin/trial/recordings/rec_keep/audio/caller", headers=adm).status_code == 200
    assert api.get("/api/admin/trial/recordings/rec_keep/audio/../../x", headers=adm).status_code == 404
    assert api.get("/api/admin/trial/recordings/rec_keep/audio/staff", headers=adm).status_code == 404         # no such track was recorded
    assert api.get("/api/admin/trial/recordings/rec_keep/audio/caller", headers=other_headers(api, "otheradmin", "admin")).status_code == 404
    dl = api.get("/api/admin/trial/recordings/rec_keep/audio/caller", headers=adm)
    assert dl.headers["content-type"] == "audio/wav" and dl.headers["cache-control"] == "no-store"
    with session_scope() as db:
        assert [e.detail["recording_id"] for e in db.scalars(select(TrialEvent).where(TrialEvent.kind == "recording_accessed"))] == ["rec_keep", "rec_keep"]
    h = api.post("/api/admin/trial/recordings/rec_old/hold", headers=adm, json={"hold": True, "reason": "case 9"})
    assert h.status_code == 200 and h.json()["legal_hold"] is True and h.json()["hold_reason"] == "case 9"
    assert api.post("/api/admin/trial/recordings/rec_old/hold", headers=adm, json={"hold": True}).status_code == 409           # a reason is required
    assert api.post("/api/admin/trial/recordings/nope/hold", headers=adm, json={"hold": True, "reason": "x"}).status_code == 404
    assert api.post("/api/admin/trial/retention/purge", headers=adm, json={"dry_run": True}).json()["purged"] == 0               # held: not eligible
    api.post("/api/admin/trial/recordings/rec_old/hold", headers=adm, json={"hold": False})
    dry = api.post("/api/admin/trial/retention/purge", headers=adm, json={"dry_run": True}).json()
    assert dry["purged"] == 1 and dry["dry_run"] is True and (root / T / "rec_old").exists()
    out = api.post("/api/admin/trial/retention/purge", headers=adm).json()
    assert out["purged"] == 1 and not (root / T / "rec_old").exists() and (root / T / "rec_keep").exists()
    assert api.post("/api/admin/trial/retention/purge", headers=adm).json()["purged"] == 0                                        # idempotent
    after = {r["id"]: r for r in api.get("/api/admin/trial/recordings", headers=adm).json()}
    assert after["rec_old"]["status"] == "purged" and after["rec_old"]["tracks"] == []
    assert api.get("/api/admin/trial/recordings/rec_old/audio/caller", headers=adm).status_code == 404
    last = api.get("/api/admin/trial/retention", headers=adm).json()["last_run"]
    assert last["purged"] == 1 and last["actor"] == "admin1"
    with session_scope() as db:
        assert db.get(Call, (T, "c_r")) is not None and db.get(CallRecording, (T, "rec_old")) is not None      # the call and the tombstone stay


def test_the_admin_feedback_queue_is_tenant_scoped_and_validates_filters(api):  # noqa: F811
    adm = auth(api, "admin1")
    assert api.get("/api/admin/trial/feedback", headers=adm).json()["items"] == []
    assert api.get("/api/admin/trial/feedback?status=bogus", headers=adm).status_code == 422
    assert api.get("/api/admin/trial/feedback?status=pending_review", headers=adm).status_code == 200
    assert api.post("/api/admin/trial/feedback/c_none/review", headers=adm, json={"status": "reviewed"}).status_code == 404
    assert api.post("/api/admin/trial/feedback/c_none/review", headers=adm, json={"status": "reviewed", "extra": 1}).status_code == 422
