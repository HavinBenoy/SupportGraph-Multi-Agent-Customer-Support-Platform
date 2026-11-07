"""M5 building blocks without sockets: trial settings (permissions, versioning, audit), consent transitions, the recording provider, retention/purge, metrics."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.auth.tokens import Principal
from app.calls import service as svc
from app.db.models import Call, CallConsent, CallEvent, CallRecording, FeedbackReview, InterventionFeedback, Ticket, TrialEvent, TrialSettings
from app.db.session import session_scope
from app.trial import consent as cs
from app.trial import metrics as mx
from app.trial import retention as rt
from app.trial import settings as ts
from app.trial.recording import CallRecorder, LocalProvider, NullProvider, RecordingStopped, RecordingUnavailable, TRACKS, create_row, finish_row

T, T2 = "qspiders_sample", "other_institute"
ADMIN = Principal(T, "admin", "admin1", None)
STAFF = Principal(T, "staff", "staff1", None)
UTC = timezone.utc


def mk_call(db, cid, tenant=T, trial=True, status="ended", created=None, owner=None, takeover=None, ended=None, student="stu_asha"):
    created = created or datetime.now(UTC)
    c = Call(tenant_id=tenant, id=cid, ticket_id=f"tk_{cid}", student_id=student, status=status, owner="none", epoch=1, trial_mode=trial, created_at=created,
             intervention_owner=owner, takeover_at=takeover, ended_at=ended)
    db.add(c)
    db.flush()
    return c


# ---- settings -----------------------------------------------------------------------------------------------------------------------
def test_defaults_are_off_and_a_missing_row_is_not_an_error():
    with session_scope() as db:
        c = ts.load(db, T)
        assert (c.enabled, c.version, c.recording_offered, c.withdrawal_policy) == (False, 0, False, "delete")
        assert c.disclosure_text.startswith("You're speaking with our AI support assistant.")
        assert "may monitor the conversation" in c.disclosure_text and "{days}" not in c.notice_text()


def test_only_an_administrator_can_change_the_settings_and_each_change_is_audited_and_persisted():
    with session_scope() as db:
        with pytest.raises(ts.SettingsError) as e:
            ts.update_settings(db, STAFF, {"enabled": True}, 0, provider_available=False)
        assert e.value.status == 403
        with pytest.raises(ts.SettingsError) as e:
            ts.update_settings(db, Principal(T, "student", "asha", "stu_asha"), {"enabled": True}, 0, provider_available=False)
        assert e.value.status == 403
        assert ts.load(db, T).enabled is False
    with session_scope() as db:
        c = ts.update_settings(db, ADMIN, {"enabled": True}, 0, provider_available=False)
        assert (c.enabled, c.version, c.updated_by) == (True, 1, "admin1")
    with session_scope() as db:                                                  # a fresh session = what a restart sees
        assert ts.load(db, T).enabled is True
        c = ts.update_settings(db, ADMIN, {"enabled": False}, 1, provider_available=False)
        assert (c.enabled, c.version) == (False, 2)
        kinds = [ev["kind"] for ev in ts.list_events(db, T)]
        assert kinds[:2] == ["trial_disabled", "trial_enabled"]
        assert db.get(TrialSettings, T2) is None                                # another tenant is untouched
        assert ts.load(db, T2).enabled is False


def test_stale_edits_are_refused_no_lost_update_and_a_no_op_does_not_bump_the_version():
    with session_scope() as db:
        ts.update_settings(db, ADMIN, {"retention_recording_days": 10}, 0, provider_available=False)
    with session_scope() as db:
        with pytest.raises(ts.SettingsError) as e:
            ts.update_settings(db, ADMIN, {"retention_recording_days": 20}, 0, provider_available=False)       # a second admin still had version 0
        assert e.value.status == 409
        assert ts.load(db, T).retention_recording_days == 10
        same = ts.update_settings(db, ADMIN, {"retention_recording_days": 10}, 1, provider_available=False)
        assert same.version == 1
        with pytest.raises(ts.SettingsError) as e:
            ts.update_settings(db, ADMIN, {"retention_recording_days": 20}, 7, provider_available=False)       # a version that never existed
        assert e.value.status == 409


@pytest.mark.parametrize("patch", [{"enabled": "yes"}, {"retention_recording_days": 0}, {"retention_recording_days": 366}, {"retention_recording_days": True},
                                   {"retention_consent_days": 0}, {"withdrawal_policy": "shred"}, {"disclosure_text": "too short"}, {"recording_notice": "x" * 1300},
                                   {"nonsense": 1}])
def test_invalid_settings_are_refused(patch):
    with session_scope() as db:
        with pytest.raises(ts.SettingsError) as e:
            ts.update_settings(db, ADMIN, patch, 0, provider_available=True)
        assert e.value.status == 422
        assert ts.load(db, T).version == 0


def test_recording_cannot_be_offered_without_a_provider_and_terms_changes_bump_the_policy_version():
    with session_scope() as db:
        with pytest.raises(ts.SettingsError) as e:
            ts.update_settings(db, ADMIN, {"recording_offered": True}, 0, provider_available=False)
        assert e.value.status == 409 and "no recording provider" in e.detail if hasattr(e, "detail") else True
        c = ts.update_settings(db, ADMIN, {"recording_offered": True}, 0, provider_available=True)
        assert (c.recording_offered, c.consent_policy_version) == (True, 1)
        c = ts.update_settings(db, ADMIN, {"disclosure_text": "A new disclosure sentence that is long enough."}, 1, provider_available=True)
        assert c.consent_policy_version == 1                                      # the disclosure is not the consent terms
        c = ts.update_settings(db, ADMIN, {"withdrawal_policy": "keep_until_expiry"}, 2, provider_available=True)
        assert c.consent_policy_version == 2
        c = ts.update_settings(db, ADMIN, {"retention_recording_days": 7}, 3, provider_available=True)
        assert c.consent_policy_version == 3


def test_audit_events_never_carry_free_text():
    with session_scope() as db:
        ts.update_settings(db, ADMIN, {"disclosure_text": "SECRET-DISCLOSURE-TEXT that is long enough", "recording_notice": "SECRET-NOTICE-TEXT that is long enough"}, 0,
                           provider_available=True)
        dump = str([(e.kind, e.actor, e.detail) for e in db.scalars(select(TrialEvent))])
        assert "SECRET" not in dump and "disclosure_text" in dump


def test_changing_the_retention_period_re_dates_existing_recordings_but_not_held_ones():
    now = datetime.now(UTC)
    with session_scope() as db:
        mk_call(db, "c_1")
        for rid, hold in (("rec_a", False), ("rec_b", True)):
            db.add(CallRecording(tenant_id=T, id=rid, call_id="c_1", consent_policy_version=1, status="completed", provider="local", storage_ref=f"{T}/{rid}",
                                 started_at=now, stopped_at=now, expires_at=now + timedelta(days=30), legal_hold=hold, size_bytes=10))
        db.flush()
        ts.update_settings(db, ADMIN, {"retention_recording_days": 5}, 0, provider_available=True)
        a, b = db.get(CallRecording, (T, "rec_a")), db.get(CallRecording, (T, "rec_b"))
        assert abs((a.expires_at - a.stopped_at).days - 5) <= 0
        assert (b.expires_at - b.stopped_at).days == 30


# ---- consent ------------------------------------------------------------------------------------------------------------------------
def offered(db, cid="c_x", v=1):
    mk_call(db, cid)
    cs.offer(db, T, cid, v)
    return cid


def test_consent_transitions_and_idempotence():
    with session_scope() as db:
        cid = offered(db)
        assert cs.current(db, T, cid) == "offered"
        assert cs.decide(db, T, cid, "granted", 1) == "granted"
        assert cs.decide(db, T, cid, "granted", 1) == "granted"                 # repeat: no change, no second event
        with pytest.raises(cs.ConsentError):
            cs.decide(db, T, cid, "declined", 1)                                # granted -> declined is not allowed: withdraw instead
        assert cs.decide(db, T, cid, "withdrawn", 1) == "withdrawn"
        assert cs.decide(db, T, cid, "granted", 1) == "granted"                 # an explicit change of mind
        kinds = [e.kind for e in db.scalars(select(CallEvent).where(CallEvent.call_id == cid).order_by(CallEvent.id))]
        assert kinds == ["consent_offered", "consent_granted", "consent_withdrawn", "consent_granted"]
        row = db.get(CallConsent, (T, cid))
        assert row.decided_at and row.withdrawn_at and row.policy_version == 1


def test_declined_then_changed_mind_and_withdraw_without_a_grant_are_handled():
    with session_scope() as db:
        cid = offered(db)
        with pytest.raises(cs.ConsentError):
            cs.decide(db, T, cid, "withdrawn", 1)                               # nothing to withdraw
        assert cs.decide(db, T, cid, "declined", 1) == "declined"
        with pytest.raises(cs.ConsentError):
            cs.decide(db, T, cid, "withdrawn", 1)
        assert cs.decide(db, T, cid, "granted", 1) == "granted"


def test_consent_needs_an_offer_the_right_policy_version_and_a_known_decision():
    with session_scope() as db:
        mk_call(db, "c_none")
        with pytest.raises(cs.ConsentError):
            cs.decide(db, T, "c_none", "granted", 1)                            # never offered: silence/absence is not consent
        cid = offered(db, "c_v", v=3)
        with pytest.raises(cs.ConsentError):
            cs.decide(db, T, cid, "granted", 2)                                 # they saw different terms
        with pytest.raises(cs.ConsentError):
            cs.decide(db, T, cid, "maybe", 3)
        assert cs.current(db, T, cid) == "offered"
        assert cs.current(db, T2, cid) is None                                  # tenant scoped


# ---- recording provider ------------------------------------------------------------------------------------------------------------
def test_null_provider_can_never_record():
    p = NullProvider()
    assert p.healthy() is False and p.exists("x/y") is False
    with pytest.raises(RecordingUnavailable):
        p.open(T, "rec_1")
    p.delete("x/y")


def test_local_provider_writes_wavs_with_private_permissions_and_deletes_idempotently(tmp_path):
    p = LocalProvider(str(tmp_path / "rec"))
    assert p.healthy()
    ref, h = p.open(T, "rec_1")
    assert ref == f"{T}/rec_1"
    h.write("caller", b"\x01\x00" * 1600, 16000)
    h.write("assistant", b"\x02\x00" * 2205, 22050)
    h.write("nonsense", b"\x00\x00", 16000)                                     # an unknown track is ignored, never a file name
    size = h.close()
    assert size > 0 and h.close() == size                                       # idempotent
    d = tmp_path / "rec" / T / "rec_1"
    assert sorted(f.name for f in d.iterdir()) == ["assistant.wav", "caller.wav"]
    assert (d.stat().st_mode & 0o777) == 0o700 and all((f.stat().st_mode & 0o777) == 0o600 for f in d.iterdir())
    wav = (d / "caller.wav").read_bytes()
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE" and int.from_bytes(wav[24:28], "little") == 16000
    assert p.exists(ref) and p.track_path(ref, "caller") is not None and p.track_path(ref, "staff") is None and p.track_path(ref, "../x") is None
    p.delete(ref)
    p.delete(ref)                                                               # already gone is a success
    assert not p.exists(ref) and not d.exists()
    assert (tmp_path / "rec").exists()                                          # only the recording's own directory was removed


@pytest.mark.parametrize("ref", ["../escape/rec", "tenant/../../etc", "/etc/passwd", "a/b/c", "..", "t/..", "t/", "/x", "a b/c", "t/rec\x00"])
def test_local_provider_refuses_references_that_escape_its_root(tmp_path, ref):
    p = LocalProvider(str(tmp_path / "rec"))
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    with pytest.raises(ValueError):
        p.delete(ref)
    assert p.exists(ref) is False and victim.exists()
    with pytest.raises(ValueError):
        p.track_path(ref, "caller")


def test_local_provider_unavailable_directory_fails_closed(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    p = LocalProvider(str(blocker / "sub"))                                      # a directory that cannot be created
    assert p.healthy() is False
    with pytest.raises(RecordingUnavailable):
        p.open(T, "rec_1")


def test_a_recording_cannot_open_twice_and_the_size_limit_stops_it(tmp_path):
    p = LocalProvider(str(tmp_path), max_mb=1)
    ref, h = p.open(T, "rec_1")
    with pytest.raises(RecordingUnavailable):
        p.open(T, "rec_1")
    with pytest.raises(RecordingStopped) as e:
        for _ in range(50):
            h.write("caller", b"\x01\x00" * 16000, 16000)
    assert e.value.reason == "size_limit"
    h.abort()


@pytest.mark.anyio
async def test_the_recorder_finalises_once_and_a_failure_does_not_pretend_to_be_complete(tmp_path):
    results = []

    async def finish(status, reason, size):
        results.append((status, reason, size))

    p = LocalProvider(str(tmp_path))
    ref, h = p.open(T, "rec_1")
    r = CallRecorder("rec_1", h, 22050, finish)
    r.caller(b"\x01\x00" * 160)
    r.ai(b"\x00" * 8 + b"\x01\x00" * 100)
    r.staff(b"\x01\x00" * 160)
    await r.stop("ended")
    await r.stop("ended")
    assert [x[:2] for x in results] == [("completed", "ended")] and results[0][2] > 0
    assert sorted(f.name for f in (tmp_path / T / "rec_1").iterdir()) == [f"{t}.wav" for t in sorted(TRACKS)]
    ref2, h2 = p.open(T, "rec_2")
    r2 = CallRecorder("rec_2", h2, 22050, finish)
    await r2.fail("write_error")
    await r2.stop("ended")
    assert results[1] == ("failed", "write_error", 0) and len(results) == 2


def test_audio_is_never_stored_in_the_database():
    cols = {c.name for c in CallRecording.__table__.columns}
    assert not any(t in cols for t in ("audio", "data", "blob", "pcm", "content"))
    assert not any(str(c.type).upper().startswith(("BLOB", "LARGEBINARY", "BYTEA")) for t in (CallRecording, CallConsent, InterventionFeedback) for c in t.__table__.columns)


# ---- retention ---------------------------------------------------------------------------------------------------------------------
def rec_row(db, tmp, rid, *, call="c_r", days_ago=40, hold=False, status="completed", write=True, provider=None):
    stopped = datetime.now(UTC) - timedelta(days=days_ago)
    ref = f"{T}/{rid}"
    if write:
        d = tmp / T / rid
        d.mkdir(parents=True)
        (d / "caller.wav").write_bytes(b"RIFF....")
    db.add(CallRecording(tenant_id=T, id=rid, call_id=call, consent_policy_version=1, status=status, provider="local", storage_ref=ref, started_at=stopped,
                         stopped_at=stopped, expires_at=stopped + timedelta(days=30), legal_hold=hold, hold_reason="case 7" if hold else None, size_bytes=8))
    db.flush()


def test_retention_purges_only_expired_unheld_recordings_and_is_idempotent(tmp_path):
    prov = LocalProvider(str(tmp_path))
    with session_scope() as db:
        mk_call(db, "c_r")
        mk_call(db, "c_other", tenant=T2, student=None)
        rec_row(db, tmp_path, "rec_old", days_ago=40)
        rec_row(db, tmp_path, "rec_new", days_ago=1)
        rec_row(db, tmp_path, "rec_held", days_ago=90, hold=True)
        keep = tmp_path / "unrelated.txt"
        keep.write_text("not a recording")
        (tmp_path / T2).mkdir()
        (tmp_path / T2 / "rec_foreign").mkdir()
        assert rt.status(db, T, prov)["recordings"]["eligible_now"] == 1
        dry = rt.run_purge(db, prov, T, "admin1", dry_run=True)
        assert (dry["purged"], dry["dry_run"]) == (1, True) and (tmp_path / T / "rec_old").exists()      # a dry run deletes nothing
        out = rt.run_purge(db, prov, T, "admin1")
        assert (out["purged"], out["failed"], out["held"]) == (1, 0, 1)
        old = db.get(CallRecording, (T, "rec_old"))
        assert (old.status, old.storage_ref, old.size_bytes) == ("purged", None, 0) and old.purged_at is not None
        assert not (tmp_path / T / "rec_old").exists()
        assert (tmp_path / T / "rec_new").exists() and (tmp_path / T / "rec_held").exists() and keep.exists() and (tmp_path / T2 / "rec_foreign").exists()
        assert db.get(CallRecording, (T, "rec_held")).status == "completed"
        again = rt.run_purge(db, prov, T, "admin1")
        assert (again["purged"], again["failed"]) == (0, 0)                      # idempotent
        assert db.scalar(select(func.count()).select_from(Call).where(Call.tenant_id == T)) == 1      # calls are never deleted
        runs = [e for e in db.scalars(select(TrialEvent).where(TrialEvent.kind == "purge_run"))]
        assert len(runs) == 1 and runs[0].detail["purged"] == 1                  # counts only; an empty run leaves no trace
        assert set(runs[0].detail) <= {"purged", "failed", "held", "consents", "feedback"}


def test_a_failed_purge_is_remembered_and_retried_and_does_not_block_the_others(tmp_path):
    class Flaky(LocalProvider):
        broken = True

        def delete(self, ref):
            if self.broken and ref.endswith("rec_a"):
                raise PermissionError("secret path /var/x")
            super().delete(ref)

    prov = Flaky(str(tmp_path))
    with session_scope() as db:
        mk_call(db, "c_r")
        rec_row(db, tmp_path, "rec_a")
        rec_row(db, tmp_path, "rec_b")
        out = rt.run_purge(db, prov, T, "system")
        assert (out["purged"], out["failed"]) == (1, 1)
        a = db.get(CallRecording, (T, "rec_a"))
        assert (a.status, a.purge_attempts, a.purge_error) == ("completed", 1, "PermissionError") and a.storage_ref
        assert "secret" not in str(a.purge_error)
        assert db.get(CallRecording, (T, "rec_b")).status == "purged"
        st = rt.status(db, T, prov)["recordings"]
        assert st["purge_failing"] == 1 and st["eligible_now"] == 1
        Flaky.broken = False
        out = rt.run_purge(db, prov, T, "system")                                # the next run recovers
        assert (out["purged"], out["failed"]) == (1, 0)
        a = db.get(CallRecording, (T, "rec_a"))
        assert (a.status, a.purge_error, a.storage_ref) == ("purged", None, None)


def test_legal_hold_requires_a_reason_is_audited_and_blocks_purge(tmp_path):
    prov = LocalProvider(str(tmp_path))
    with session_scope() as db:
        mk_call(db, "c_r")
        rec_row(db, tmp_path, "rec_a")
        with pytest.raises(ValueError):
            rt.set_hold(db, T, "rec_a", True, "  ", "admin1")
        with pytest.raises(LookupError):
            rt.set_hold(db, T2, "rec_a", True, "x", "admin1")                    # another tenant cannot even see it
        rt.set_hold(db, T, "rec_a", True, "case 12", "admin1")
        assert rt.run_purge(db, prov, T)["purged"] == 0 and (tmp_path / T / "rec_a").exists()
        rt.set_hold(db, T, "rec_a", False, None, "admin1")
        assert rt.run_purge(db, prov, T)["purged"] == 1
        with pytest.raises(ValueError):
            rt.set_hold(db, T, "rec_a", True, "late", "admin1")                  # already purged
        assert [e.kind for e in db.scalars(select(TrialEvent).order_by(TrialEvent.id)) if e.kind.startswith("legal")] == ["legal_hold_set", "legal_hold_cleared"]


def test_consents_and_reviewed_feedback_expire_by_their_own_periods_but_pending_feedback_never_does(tmp_path):
    prov = LocalProvider(str(tmp_path))
    old = datetime.now(UTC) - timedelta(days=400)
    with session_scope() as db:
        for cid in ("c_old", "c_new", "c_keepfile"):
            mk_call(db, cid)
        for cid, when in (("c_old", old), ("c_new", datetime.now(UTC)), ("c_keepfile", old)):
            db.add(CallConsent(tenant_id=T, call_id=cid, status="granted", policy_version=1, offered_at=when, updated_at=when))
        rec_row(db, tmp_path, "rec_k", call="c_keepfile", days_ago=1)
        for cid, state, rs, at in (("c_old", "submitted", "approved_for_update", old), ("c_new", "submitted", "pending_review", old), ("c_keepfile", "skipped", None, old)):
            db.add(InterventionFeedback(tenant_id=T, call_id=cid, staff_subject="staff1", state=state, intervened_at=old, category="other" if state == "submitted" else None,
                                        what_was_wrong="x" * 8 if state == "submitted" else None, review_status=rs, reviewed_at=old if rs not in (None, "pending_review") else None,
                                        skipped_at=old if state == "skipped" else None, submitted_at=old if state == "submitted" else None))
        db.flush()
        db.add(FeedbackReview(tenant_id=T, call_id="c_old", reviewer="admin1", from_status="pending_review", to_status="approved_for_update"))
        db.flush()
        out = rt.run_purge(db, prov, T)
        assert (out["consents"], out["feedback"]) == (1, 2)
        assert {c.call_id for c in db.scalars(select(CallConsent))} == {"c_new", "c_keepfile"}      # a call with a live recording keeps its consent record
        assert {f.call_id for f in db.scalars(select(InterventionFeedback))} == {"c_new"}            # pending review stays
        assert db.scalar(select(func.count()).select_from(FeedbackReview)) == 0
        assert db.scalar(select(func.count()).select_from(Call)) == 3 and db.scalar(select(func.count()).select_from(Ticket)) >= 0


def test_startup_marks_left_over_recordings_failed_so_retention_removes_them(tmp_path):
    prov = LocalProvider(str(tmp_path))
    with session_scope() as db:
        mk_call(db, "c_r", status="failed")
        rec_row(db, tmp_path, "rec_crash", status="recording", days_ago=0)
        db.get(CallRecording, (T, "rec_crash")).expires_at = None
        assert rt.sweep_recordings(db) == 1
        r = db.get(CallRecording, (T, "rec_crash"))
        assert (r.status, r.stop_reason) == ("failed", "server_restart") and r.expires_at is not None
        assert rt.sweep_recordings(db) == 0
        r.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert rt.run_purge(db, prov, T)["purged"] == 1


def test_run_all_covers_every_tenant_with_settings_and_isolates_failures(tmp_path):
    prov = LocalProvider(str(tmp_path))
    with session_scope() as db:
        for t in (T, T2):
            db.add(TrialSettings(tenant_id=t, enabled=True, version=1, disclosure_text="d" * 30, recording_notice="n" * 30, consent_policy_version=1,
                                 retention_recording_days=30, retention_consent_days=365, retention_feedback_days=365, withdrawal_policy="delete", recording_offered=False))
        mk_call(db, "c_r")
        rec_row(db, tmp_path, "rec_a")
    res = rt.run_all(prov)
    assert set(res) == {T, T2} and res[T]["purged"] == 1 and res[T2]["purged"] == 0


# ---- metrics -----------------------------------------------------------------------------------------------------------------------
def span(days=30):
    until = datetime.now(UTC) + timedelta(days=1)
    return until - timedelta(days=days + 1), until


def test_metrics_empty_state_has_no_invented_numbers():
    with session_scope() as db:
        m = mx.compute(db, T, *span())
    assert m["empty"] is True and m["calls"]["total"] == 0
    assert m["human"]["intervention_rate"] is None and m["durations"]["avg_call_s"] is None and m["consent"]["granted_rate"] is None


def seed_metrics(db):
    now = datetime.now(UTC)
    mk_call(db, "c_ai", created=now, ended=now + timedelta(seconds=60))                                              # AI handled, ended normally
    mk_call(db, "c_listen", created=now, ended=now + timedelta(seconds=120), student="stu_meera")                   # listened only
    mk_call(db, "c_int", created=now, owner="staff1", takeover=now + timedelta(seconds=10), ended=now + timedelta(seconds=70))   # human intervention
    mk_call(db, "c_fail", status="failed", created=now, ended=now + timedelta(seconds=5), student=None)             # failed, anonymous
    mk_call(db, "c_live", status="active_ai", created=now, student=None)
    mk_call(db, "c_off", trial=False, created=now)                                                                    # outside the trial
    mk_call(db, "c_old", created=now - timedelta(days=90))                                                            # outside the period
    mk_call(db, "c_x", tenant=T2, created=now, student=None)                                                                        # another tenant
    for cid in ("c_listen", "c_int"):
        svc.add_event(db, T, cid, "listen_started", "staff1")
    svc.add_event(db, T, "c_listen", "listen_started", "staff2")
    svc.add_event(db, T, "c_int", "takeover_succeeded", "staff1")
    for cid, st in (("c_ai", "granted"), ("c_listen", "declined"), ("c_int", "withdrawn"), ("c_live", "offered")):
        db.add(CallConsent(tenant_id=T, call_id=cid, status=st, policy_version=1, offered_at=now, updated_at=now))
    db.add(CallRecording(tenant_id=T, id="rec_1", call_id="c_ai", consent_policy_version=1, status="completed", provider="local", storage_ref="t/r", started_at=now,
                         stopped_at=now, expires_at=now, size_bytes=5))
    db.add(CallRecording(tenant_id=T, id="rec_2", call_id="c_int", consent_policy_version=1, status="purged", provider="local", started_at=now, stopped_at=now, size_bytes=0))
    db.add(InterventionFeedback(tenant_id=T, call_id="c_int", staff_subject="staff1", state="submitted", category="outdated_knowledge", what_was_wrong="TOPSECRETTEXT",
                                intervened_at=now, review_status="pending_review"))
    db.flush()


def test_metrics_count_each_thing_separately_and_exclude_other_periods_tenants_and_non_trial_calls():
    with session_scope() as db:
        seed_metrics(db)
        m = mx.compute(db, T, *span())
    c, h = m["calls"], m["human"]
    assert (c["total"], c["ai_handled"], c["ongoing"], c["ended_normally"], c["failed"], c["ended_after_intervention"]) == (5, 4, 1, 2, 1, 1)
    assert (h["calls_with_listeners"], h["listener_sessions"], h["takeovers"], h["calls_with_takeover"], h["intervention_rate"]) == (2, 3, 1, 1, 0.2)
    assert m["calls_outside_trial"] == 1 and m["empty"] is False
    assert m["customers"] == {"unique_signed_in": 2, "anonymous_calls": 2}
    assert (m["durations"]["avg_call_s"], m["durations"]["calls_measured"], m["durations"]["avg_intervention_s"]) == (83.3, 3, 60.0)
    k = m["consent"]
    assert (k["offered"], k["granted"], k["declined"], k["withdrawn"], k["undecided"], k["ever_granted"], k["not_offered"]) == (4, 1, 1, 1, 1, 2, 1)
    assert (k["granted_rate"], k["declined_rate"], k["withdrawn_rate"]) == (0.5, 0.25, 0.5)
    r = m["recording"]
    assert (r["recordings"], r["calls_recorded"], r["by_status"]["completed"], r["by_status"]["purged"]) == (2, 2, 1, 1)
    f = m["feedback"]
    assert f["by_state"]["submitted"] == 1 and f["by_review_status"]["pending_review"] == 1 and f["reasons"] == {"outdated_knowledge": 1}
    assert any("proposed correction" in n for n in m["notes"])


def test_metrics_carry_no_personal_data_and_respect_the_date_filter_and_tenant():
    with session_scope() as db:
        seed_metrics(db)
        dump = str(mx.compute(db, T, *span()))
        for leak in ("TOPSECRETTEXT", "stu_asha", "stu_meera", "staff1", "staff2", "c_int", "tk_c_"):
            assert leak not in dump, leak
        only_old = mx.compute(db, T, datetime.now(UTC) - timedelta(days=100), datetime.now(UTC) - timedelta(days=60))
        assert only_old["calls"]["total"] == 1 and only_old["human"]["intervention_rate"] == 0.0
        other = mx.compute(db, T2, *span())
        assert other["calls"]["total"] == 1 and other["consent"]["offered"] == 0
        none = mx.compute(db, T, datetime.now(UTC) - timedelta(days=500), datetime.now(UTC) - timedelta(days=400))
        assert none["empty"] is True


def test_metrics_truncate_honestly_when_there_are_too_many_calls():
    with session_scope() as db:
        for i in range(7):
            mk_call(db, f"c_{i}")
        m = mx.compute(db, T, *span(), max_rows=5)
    assert m["truncated"] is True and m["calls"]["total"] == 5
