"""M4 live ownership over a real VoiceSession + CallHub (fake caller sockets, fake STT/TTS/VAD): listening, takeover, return, end, and every failure
case at each ownership transition. Real microphones/speakers are NOT exercised here (see docs/M4.md for the manual browser test)."""

import asyncio
import random
import threading

import pytest

from app.calls.hub import AI, NONE, STAFF, TAG_AI, TAG_CALLER, TAG_STAFF, HubError, StaffConn
from tests.calls.rig import AI_RATE, GatedTts, Rig, events, row, staff
from tests.voice.audio import silence, tone, utterance

pytestmark = pytest.mark.anyio


def kinds(call_id):
    return [e[0] for e in events(call_id)]


def assert_invariant(rig):
    """On the REAL timeline: every AI frame was sent while the AI owned the call, every staff frame while a staff member did."""
    for src, turn, seq, owner, epoch in rig.frames:
        assert (src, owner) in {("ai", AI), ("staff", STAFF)}, (src, owner, turn, seq)


# ---- lifecycle -----------------------------------------------------------------------------------------------------------------
async def test_a_call_is_created_then_connected_with_ai_ownership_and_timestamps():
    rig = await Rig().start()
    try:
        r = row(rig.live.id)
        assert (r.status, r.owner, r.epoch, r.tenant_id, r.ticket_id, r.student_id) == ("active_ai", "ai", 1, "qspiders_sample", rig.ticket, "stu_asha")
        assert r.created_at and r.connected_at and not r.takeover_at and not r.ended_at
        assert kinds(rig.live.id) == ["created", "connected"] and (rig.live.owner, rig.live.epoch, rig.session.epoch) == (AI, 1, 1)
    finally:
        await rig.stop()


async def test_the_ai_answers_normally_when_nobody_is_involved():
    rig = await Rig().start()
    try:
        await rig.ai_turn()
        assert rig.turns and any(src == "ai" for src, *_ in rig.frames)
        assert_invariant(rig)
    finally:
        await rig.stop()


# ---- listening -----------------------------------------------------------------------------------------------------------------
async def test_a_listener_hears_the_caller_and_the_ai_and_the_call_is_unaffected():
    plain = await Rig().start()
    await plain.ai_turn()
    n_plain = len([f for f in plain.frames if f[0] == "ai"])
    await plain.stop()

    rig = await Rig().start()
    try:
        c = await rig.attach()
        await rig.ai_turn()
        audio, msgs = rig.drain_audio(c)
        tags = {a[0] for a in audio}
        assert TAG_CALLER in tags and TAG_AI in tags                       # both sides are heard
        assert len([f for f in rig.frames if f[0] == "ai"]) == n_plain      # the caller got exactly the same audio as without a listener
        assert msgs[0]["type"] == "joined" and msgs[0]["owner"] == "ai" and {"ai_audio_start", "ai_audio_end"} <= {m["type"] for m in msgs}
        assert row(rig.live.id).status == "active_ai" and rig.session.turns == 1   # no second session, same turn counter
        assert_invariant(rig)
    finally:
        await rig.stop()


async def test_listening_records_events_and_a_listener_leaving_does_not_end_the_call():
    rig = await Rig().start()
    try:
        a, b = await rig.attach("staff1"), await rig.attach("admin1", "admin")
        assert rig.live.snapshot()["listeners"] == 2 and rig.live.snapshot()["listening_staff"] == ["admin1", "staff1"]
        await rig.live.leave(a)
        await rig.live.leave(a)                                             # idempotent
        assert not rig.live.ended and row(rig.live.id).status == "active_ai" and rig.live.snapshot()["listeners"] == 1
        await rig.ai_turn()                                                 # the call carries on
        ev = events(rig.live.id)
        assert [(e[0], e[1]) for e in ev if e[0].startswith("listen")] == [("listen_started", "staff1"), ("listen_started", "admin1"), ("listen_stopped", "staff1")]
        assert all("tenant" not in e[2] for e in ev)
    finally:
        await rig.stop()


async def test_multiple_listeners_get_the_same_audio_and_the_limit_is_enforced():
    rig = await Rig(calls_max_listeners=2).start()
    try:
        a, b = await rig.attach("staff1"), await rig.attach("admin1", "admin")
        with pytest.raises(HubError) as e:
            await rig.attach("staff3")
        assert e.value.status == 429
        await rig.ai_turn()
        aa, _ = rig.drain_audio(a)
        bb, _ = rig.drain_audio(b)
        assert aa and aa == bb
    finally:
        await rig.stop()


async def test_a_slow_listener_loses_its_own_old_audio_and_never_slows_the_call():
    rig = await Rig().start()
    try:
        slow = await rig.attach(maxq=3)                                     # never drained
        await rig.ai_turn()
        assert slow.out.dropped > 0 and len(slow.out._d) <= 3               # bounded
        assert len([f for f in rig.frames if f[0] == "ai"]) > 3            # and the caller still got every frame
    finally:
        await rig.stop()


async def test_caller_audio_is_not_recognised_by_ai_while_staff_owns_the_call_but_listeners_still_hear_it():
    rig = await Rig().start()
    try:
        c = await rig.attach()
        await rig.live.takeover(staff())
        rig.drain(c)
        await rig.caller_says()
        await asyncio.sleep(0.3)
        audio, _ = rig.drain_audio(c)
        assert any(a[0] == TAG_CALLER for a in audio)                       # staff hears the caller
        assert rig.turns == [] and rig.stt.calls == [] and rig.count("transcript") == 0   # nothing recognised, nothing answered
    finally:
        await rig.stop()


# ---- takeover ------------------------------------------------------------------------------------------------------------------
async def test_takeover_transfers_ownership_atomically_and_records_events():
    rig = await Rig().start()
    try:
        snap = await rig.live.takeover(staff("staff1"))
        assert (snap["owner"], snap["owner_staff"], snap["epoch"]) == (STAFF, "staff1", 2)
        r = row(rig.live.id)
        assert (r.status, r.owner, r.owner_subject, r.epoch) == ("staff_takeover", "staff", "staff1", 2) and r.takeover_at
        assert rig.session.suspended and rig.session.epoch == 2
        assert [m for m in rig.json if m["type"] == "ownership"] == [{"type": "ownership", "owner": "staff", "epoch": 2}]
        assert kinds(rig.live.id) == ["created", "connected", "takeover_requested", "takeover_succeeded"]
    finally:
        await rig.stop()


async def test_takeover_is_idempotent_for_the_same_staff_member_and_refused_for_a_second_one():
    rig = await Rig().start()
    try:
        await rig.live.takeover(staff("staff1"))
        again = await rig.live.takeover(staff("staff1"))
        assert again["epoch"] == 2 and kinds(rig.live.id).count("takeover_succeeded") == 1 and kinds(rig.live.id).count("takeover_requested") == 1
        with pytest.raises(HubError) as e:
            await rig.live.takeover(staff("admin1", "admin"))
        assert e.value.status == 409
        ev = events(rig.live.id)
        assert ("takeover_failed", "admin1", {"reason": "already_taken", "role": "admin"}) in ev
        r = row(rig.live.id)
        assert r.owner_subject == "staff1" and r.epoch == 2                  # the loser changed nothing
    finally:
        await rig.stop()


async def test_two_staff_taking_over_at_the_same_time_exactly_one_wins():
    rig = await Rig().start()
    try:
        res = await asyncio.gather(rig.live.takeover(staff("staff1")), rig.live.takeover(staff("admin1", "admin")), return_exceptions=True)
        wins = [r for r in res if isinstance(r, dict)]
        losses = [r for r in res if isinstance(r, HubError)]
        assert len(wins) == 1 and len(losses) == 1 and losses[0].status == 409
        assert row(rig.live.id).owner_subject == wins[0]["owner_staff"] and kinds(rig.live.id).count("takeover_succeeded") == 1
    finally:
        await rig.stop()


async def test_takeover_during_ai_speech_cuts_it_and_no_stale_ai_audio_follows():
    rig = await Rig(tts=GatedTts(80, 0.01)).start()
    try:
        await rig.caller_says()
        await rig.until(lambda: len([f for f in rig.frames if f[0] == "ai"]) >= 3)
        await rig.live.takeover(staff())
        sent_at_takeover = len(rig.frames)
        await asyncio.sleep(0.4)
        assert len(rig.frames) == sent_at_takeover                           # not one more AI frame after the takeover returned
        assert {"type": "interrupted", "turn_id": rig.frames[0][1], "reason": "takeover"} in rig.json
        assert any(m["type"] == "audio_end" and m.get("interrupted") for m in rig.json)
        assert not rig.session.speaking and rig.session._speech is None
        assert_invariant(rig)
    finally:
        await rig.stop()


async def test_takeover_while_the_graph_is_running_discards_the_late_reply():
    gate, started = threading.Event(), threading.Event()

    def slow_turn(text):
        started.set()
        gate.wait(3)
        return {"reply": "A LATE ANSWER", "status": "answered", "citations": [], "escalation_id": None, "ticket_id": "t"}

    rig = await Rig(run_turn=slow_turn).start()
    try:
        await rig.caller_says()
        await asyncio.wait_for(asyncio.to_thread(started.wait, 3), 4)
        await rig.live.takeover(staff())
        gate.set()
        await asyncio.sleep(0.4)
        assert not any(m["type"] == "reply" for m in rig.json) and rig.frames == []   # never shown, never spoken
        assert rig.session.stats["discarded_ownership"] >= 1
    finally:
        gate.set()
        await rig.stop()


async def test_queued_and_pending_turns_are_dropped_at_takeover():
    gate = threading.Event()

    def slow_turn(text):
        gate.wait(3)
        return {"reply": "x", "status": "answered", "citations": [], "escalation_id": None, "ticket_id": "t"}

    rig = await Rig(run_turn=slow_turn, script=["one", "two", "three"]).start()
    try:
        for _ in range(3):
            await rig.caller_says()
            await asyncio.sleep(0.25)
        await rig.live.takeover(staff())
        gate.set()
        await asyncio.sleep(0.3)
        assert rig.session._queue.empty() and rig.session._pending is None and rig.frames == []
    finally:
        gate.set()
        await rig.stop()


# ---- staff microphone ----------------------------------------------------------------------------------------------------------
async def test_staff_audio_reaches_the_caller_only_from_the_owner_with_the_current_epoch_through_the_mic_connection():
    rig = await Rig().start()
    try:
        a, other = await rig.attach("staff1"), await rig.attach("admin1", "admin")
        assert await rig.live.mic_open(a, 1) == "not_owner"                  # nobody has taken the call yet
        await rig.live.takeover(staff("staff1"))
        assert await rig.live.mic_open(a, 1) == "stale_epoch"                # the epoch of before the takeover
        assert await rig.live.mic_open(other, 2) == "not_owner"              # another staff member cannot speak
        assert await rig.live.mic_open(StaffConn(staff("staff1"), 5), 2) == "not_attached"
        assert await rig.live.staff_audio(a, 2, b"\x01\x00" * 640) == "not_mic"   # attached and owning, but the mic was not opened
        assert await rig.live.mic_open(a, 2) == "ok" and await rig.live.mic_open(a, 2) == "ok"
        assert await rig.speak_as_staff(a, 5) == ["ok"] * 5
        assert await rig.live.staff_audio(other, 2, b"\x01\x00" * 640) == "not_mic"
        assert await rig.live.staff_audio(a, 1, b"\x01\x00" * 640) == "stale_epoch"
        assert await rig.live.staff_audio(a, 2, b"\x01\x00" * 100_000) == "too_large"
        staff_frames = [f for f in rig.frames if f[0] == "staff"]
        assert len(staff_frames) == 5 and [f[2] for f in staff_frames] == [0, 1, 2, 3, 4] and {f[1] for f in staff_frames} == {rig.live.staff_turn}
        start = next(m for m in rig.json if m["type"] == "audio_start" and m.get("staff"))
        assert start == {"type": "audio_start", "turn_id": rig.live.staff_turn, "sample_rate": 16000, "staff": True}
        assert rig.live.staff_turn > 0 and rig.session._next_id > rig.live.staff_turn
        audio, _ = rig.drain_audio(other)
        assert sum(1 for x in audio if x[0] == TAG_STAFF) == 5               # the other staff member hears the speaker
        assert_invariant(rig)
    finally:
        await rig.stop()


async def test_staff_microphone_rate_is_limited_like_the_callers():
    rig = await Rig().start()
    try:
        a = await rig.attach()
        await rig.live.takeover(staff())
        await rig.live.mic_open(a, rig.live.epoch)
        res = [await rig.live.staff_audio(a, rig.live.epoch, b"\x01\x00" * 8000) for _ in range(14)]   # 7 s of audio at once
        assert "rate" in res and res[0] == "ok"
    finally:
        await rig.stop()


# ---- return to AI --------------------------------------------------------------------------------------------------------------
async def test_return_restores_ai_ownership_same_session_and_context_without_replaying_old_output():
    rig = await Rig(script=["first question", "second question"]).start()
    try:
        await rig.ai_turn()
        first_ai_turn = rig.frames[0][1]
        n_before = len(rig.frames)
        c = await rig.attach()
        await rig.live.takeover(staff())
        await rig.live.mic_open(c, rig.live.epoch)
        await rig.speak_as_staff(c, 3)
        snap = await rig.live.return_to_ai(staff())
        assert (snap["owner"], snap["epoch"]) == (AI, 4) and not rig.session.suspended and rig.session.epoch == 4
        r = row(rig.live.id)
        assert (r.status, r.owner, r.owner_subject, r.epoch) == ("active_ai", "ai", None, 4) and r.returned_at
        assert kinds(rig.live.id)[-2:] == ["takeover_succeeded", "returned_to_ai"]
        sent_at_return = len(rig.frames)
        await asyncio.sleep(0.2)
        assert len(rig.frames) == sent_at_return                             # nothing is replayed after the return
        await rig.ai_turn()                                                  # and the AI answers the next question in the SAME session
        new = rig.frames[sent_at_return:]
        assert new and all(f[0] == "ai" and f[1] > rig.live.staff_turn > first_ai_turn for f in new)   # fresh turn ids only
        assert rig.turns == ["first question", "second question"] and rig.session.turns == 2
        assert {"type": "ownership", "owner": "ai", "epoch": 4} in rig.json
        assert any(m["type"] == "audio_end" and m.get("staff") and m["interrupted"] for m in rig.json)   # the staff "response" was closed for the caller
        assert n_before > 0
        assert_invariant(rig)
    finally:
        await rig.stop()


async def test_after_return_the_old_staff_microphone_is_rejected_and_return_is_idempotent():
    rig = await Rig().start()
    try:
        c = await rig.attach()
        await rig.live.takeover(staff())
        old_epoch = rig.live.epoch
        await rig.live.mic_open(c, old_epoch)
        await rig.live.return_to_ai(staff())
        assert await rig.live.staff_audio(c, old_epoch, b"\x01\x00" * 640) == "not_owner"
        assert await rig.live.mic_open(c, old_epoch) == "not_owner"
        snap = await rig.live.return_to_ai(staff())                          # repeated: no change, no new event
        assert snap["owner"] == AI and kinds(rig.live.id).count("returned_to_ai") == 1
        await rig.live.takeover(staff())                                     # a NEW takeover has a new epoch; the old one stays dead
        assert await rig.live.mic_open(c, old_epoch) == "stale_epoch"
        assert await rig.live.staff_audio(c, old_epoch, b"\x01\x00" * 640) == "not_mic"
    finally:
        await rig.stop()


async def test_only_the_staff_member_who_holds_the_call_or_an_admin_can_return_it():
    rig = await Rig().start()
    try:
        await rig.live.takeover(staff("staff1"))
        with pytest.raises(HubError) as e:
            await rig.live.return_to_ai(staff("staff2"))
        assert e.value.status == 403 and rig.live.owner == STAFF
        await rig.live.return_to_ai(staff("admin1", "admin"))
        assert rig.live.owner == AI
    finally:
        await rig.stop()


async def test_return_to_ai_failing_halfway_fails_the_call_safe_and_the_ai_never_resumes():
    from app.calls import service as svc

    rig = await Rig().start()
    try:
        await rig.live.takeover(staff())
        real = svc.move

        def boom(db, tenant_id, call_id, src, dst, *a, **k):
            if dst == "active_ai":
                raise RuntimeError("db down")
            return real(db, tenant_id, call_id, src, dst, *a, **k)

        svc.move = boom
        try:
            with pytest.raises(HubError) as e:
                await rig.live.return_to_ai(staff())
        finally:
            svc.move = real
        assert e.value.status == 500 and rig.live.ended and rig.live.owner == NONE
        assert row(rig.live.id).status == "failed" and row(rig.live.id).end_reason == "return_failed" and rig.caller_closed
        await rig.caller_says()
        await asyncio.sleep(0.2)
        assert rig.frames == []                                              # the AI did not speak
    finally:
        await rig.stop()


# ---- end -----------------------------------------------------------------------------------------------------------------------
async def test_end_stops_everything_persists_the_state_closes_sockets_and_refuses_further_work():
    rig = await Rig(tts=GatedTts(80, 0.01)).start()
    try:
        c = await rig.attach()
        await rig.caller_says()
        await rig.until(lambda: any(f[0] == "ai" for f in rig.frames))
        await rig.live.end("staff1", "ended_by_staff")
        n = len(rig.frames)
        await asyncio.sleep(0.3)
        assert len(rig.frames) == n and rig.live.ended and rig.caller_closed
        assert {"type": "call_end", "reason": "ended_by_staff"} in rig.json
        r = row(rig.live.id)
        assert (r.status, r.owner, r.end_reason) == ("ended", "none", "ended_by_staff") and r.ended_at
        assert kinds(rig.live.id)[-1] == "ended"
        _, msgs = rig.drain_audio(c)
        assert msgs[-1] == {"type": "call_ended", "reason": "ended_by_staff"} and c.closed
        assert rig.session._closed and rig.session._worker.done()
        assert rig.hub.get("qspiders_sample", rig.live.id) is None
        for op in (rig.live.takeover(staff()), rig.live.return_to_ai(staff()), rig.live.join(StaffConn(staff(), 5))):
            with pytest.raises(HubError) as e:
                await op
            assert e.value.status == 409
        assert await rig.live.staff_audio(c, rig.live.epoch, b"\x00\x00" * 8) == "ended" and await rig.live.mic_open(c, 1) == "ended"
        await rig.live.end("staff2", "ended_by_staff")                       # idempotent
        assert kinds(rig.live.id).count("ended") == 1
        assert_invariant(rig)
    finally:
        await rig.stop()


async def test_end_during_a_takeover_stops_staff_audio_and_cancels_pending_work():
    rig = await Rig().start()
    try:
        c = await rig.attach()
        await rig.live.takeover(staff())
        await rig.live.mic_open(c, rig.live.epoch)
        await rig.speak_as_staff(c, 2)
        await rig.live.end("staff1", "ended_by_staff")
        assert await rig.live.staff_audio(c, rig.live.epoch, b"\x01\x00" * 640) == "ended"
        assert row(rig.live.id).status == "ended" and rig.live.mic_conn is None
    finally:
        await rig.stop()


async def test_caller_disconnect_ends_the_call_tells_staff_and_records_the_participant_event():
    rig = await Rig().start()
    try:
        c = await rig.attach()
        await rig.live.takeover(staff())
        await rig.live.end("caller", "caller_disconnected", close_caller=False, participant="caller")
        r = row(rig.live.id)
        assert (r.status, r.end_reason) == ("ended", "caller_disconnected") and not rig.caller_closed
        assert kinds(rig.live.id)[-2:] == ["participant_disconnected", "ended"]
        _, msgs = rig.drain_audio(c)
        assert msgs[-1]["type"] == "call_ended"
    finally:
        await rig.stop()


async def test_a_failed_call_is_recorded_as_failed_and_cannot_resume():
    rig = await Rig().start()
    try:
        await rig.live.end("system", "error", failed=True)
        assert row(rig.live.id).status == "failed" and kinds(rig.live.id)[-1] == "failed"
        with pytest.raises(HubError):
            await rig.live.return_to_ai(staff())
    finally:
        await rig.stop()


# ---- disconnects and failures --------------------------------------------------------------------------------------------------
async def test_staff_who_disappear_during_a_takeover_hand_the_call_back_to_the_ai_after_the_grace_period():
    rig = await Rig(calls_takeover_grace_s=1).start()
    try:
        c = await rig.attach()
        await rig.live.takeover(staff())
        await rig.live.mic_open(c, rig.live.epoch)
        await rig.live.leave(c)                                              # tab closed
        assert rig.live.owner == STAFF and rig.live.mic_conn is None         # still theirs during the grace period (they may refresh)
        await rig.until(lambda: rig.live.owner == AI, 3.0)
        assert row(rig.live.id).status == "active_ai" and not rig.session.suspended
        ev = events(rig.live.id)
        assert ("participant_disconnected", "staff1", {"role": "staff", "connection": "audio"}) in ev
        assert ("returned_to_ai", "system", {"epoch": 4, "reason": "staff_unreachable", "previous_owner": "staff"}) in ev
        await rig.ai_turn()                                                  # the caller is not left in silence
        assert_invariant(rig)
    finally:
        await rig.stop()


async def test_a_staff_refresh_inside_the_grace_period_keeps_the_takeover():
    rig = await Rig(calls_takeover_grace_s=1).start()
    try:
        c = await rig.attach()
        await rig.live.takeover(staff())
        await rig.live.mic_open(c, rig.live.epoch)
        await rig.live.leave(c)
        c2 = await rig.attach()                                              # reconnect (page refresh)
        _, msgs = rig.drain_audio(c2)
        assert msgs[0]["type"] == "joined" and msgs[0]["you_own"] is True and msgs[0]["epoch"] == rig.live.epoch
        assert await rig.live.mic_open(c2, rig.live.epoch) == "ok"
        await asyncio.sleep(1.4)
        assert rig.live.owner == STAFF                                       # the grace timer was cancelled by the working microphone
        assert await rig.speak_as_staff(c2, 2) == ["ok", "ok"]
    finally:
        await rig.stop()


async def test_takeover_without_ever_opening_a_microphone_also_returns_to_the_ai():
    rig = await Rig(calls_takeover_grace_s=1).start()
    try:
        await rig.attach()
        await rig.live.takeover(staff())
        await rig.until(lambda: rig.live.owner == AI, 3.0)
    finally:
        await rig.stop()


async def test_a_second_microphone_connection_of_the_same_staff_member_replaces_the_first():
    rig = await Rig().start()
    try:
        a, b = await rig.attach(), await rig.attach()
        await rig.live.takeover(staff())
        assert await rig.live.mic_open(a, rig.live.epoch) == "ok" and await rig.live.mic_open(b, rig.live.epoch) == "ok"
        assert await rig.live.staff_audio(a, rig.live.epoch, b"\x01\x00" * 640) == "not_mic"
        assert await rig.live.staff_audio(b, rig.live.epoch, b"\x01\x00" * 640) == "ok"
    finally:
        await rig.stop()


async def test_an_ai_turn_that_raises_does_not_break_the_call_or_a_later_takeover():
    def bad(text):
        raise RuntimeError("model crashed")

    rig = await Rig(run_turn=bad).start()
    try:
        await rig.caller_says()
        await rig.until(lambda: any(m["type"] == "error" for m in rig.json))
        assert row(rig.live.id).status == "active_ai" and not rig.live.ended
        await rig.live.takeover(staff())
        assert rig.live.owner == STAFF
    finally:
        await rig.stop()


async def test_a_dead_caller_socket_does_not_crash_ownership_operations():
    rig = await Rig().start()
    try:
        rig.fail_caller_send = True
        await rig.live.takeover(staff())
        await rig.live.return_to_ai(staff())
        await rig.live.end("staff1", "ended_by_staff")
        assert row(rig.live.id).status == "ended"
    finally:
        await rig.stop()


async def test_failing_to_record_an_event_never_breaks_the_call():
    from app.calls import service as svc

    rig = await Rig().start()
    real = svc.add_event
    try:
        def bad_add(db, *a, **k):
            if a[2] in ("listen_started", "listen_stopped", "takeover_requested"):
                raise RuntimeError("events table gone")
            return real(db, *a, **k)

        svc.add_event = bad_add
        c = await rig.attach()
        await rig.live.takeover(staff())
        await rig.live.leave(c)
        assert rig.live.owner == STAFF
    finally:
        svc.add_event = real
        await rig.stop()


# ---- the invariant under random operations -------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
async def test_fuzz_the_caller_never_hears_ai_and_staff_at_once_or_stale_audio(seed):
    """Random takeovers, returns, microphones, speech, disconnects. After every operation the invariant holds and the final timeline is clean."""
    rnd = random.Random(seed)
    rig = await Rig(tts=GatedTts(30, 0.004), script=[f"question {i}" for i in range(40)], calls_takeover_grace_s=1).start()
    conns: dict[str, StaffConn] = {}
    try:
        for step in range(60):
            op = rnd.choice(["say", "say", "take_a", "take_b", "return_a", "return_b", "attach_a", "attach_b", "leave_a", "leave_b", "mic_a", "mic_b",
                             "talk_a", "talk_b", "talk_stale", "sleep"])
            who = op[-1] if op[-2:-1] == "_" else None
            name = {"a": "staff1", "b": "admin1"}.get(who or "", "")
            try:
                if op == "say":
                    await rig.caller_says()
                elif op.startswith("take"):
                    await rig.live.takeover(staff(name, "admin" if name == "admin1" else "staff"))
                elif op.startswith("return"):
                    await rig.live.return_to_ai(staff(name, "admin" if name == "admin1" else "staff"))
                elif op.startswith("attach") and who not in conns:
                    conns[who] = await rig.attach(name, "admin" if name == "admin1" else "staff", maxq=50)
                elif op.startswith("leave") and who in conns:
                    await rig.live.leave(conns.pop(who))
                elif op.startswith("mic") and who in conns:
                    await rig.live.mic_open(conns[who], rig.live.epoch)
                elif op.startswith("talk") and who in conns:
                    await rig.live.staff_audio(conns[who], rig.live.epoch, b"\x02\x00" * 640)
                elif op == "talk_stale" and conns:
                    await rig.live.staff_audio(next(iter(conns.values())), rig.live.epoch - 1, b"\x02\x00" * 640)
                else:
                    await asyncio.sleep(rnd.choice([0.005, 0.02, 0.05]))
            except HubError:
                pass
            assert rig.live.owner in (AI, STAFF, NONE)
            assert (rig.live.owner == STAFF) == (rig.live.staff is not None) or rig.live.owner == NONE
            assert not (rig.live.owner == AI and rig.session.suspended) and not (rig.live.owner == STAFF and not rig.session.suspended)
            for q in conns.values():
                rig.drain(q)
        assert_invariant(rig)
        r = row(rig.live.id)
        assert r.status in {"active_ai", "staff_takeover", "returning_to_ai"} and r.epoch >= 1
        assert (r.owner, rig.live.owner) in {("ai", AI), ("staff", STAFF), ("none", NONE)} or r.owner == "none"
    finally:
        await rig.stop()


async def test_time_under_staff_ownership_does_not_use_up_the_assistants_time_limit():
    import time

    from app.voice.session import SessionLimit

    rig = await Rig(voice_max_session_s=30).start()
    try:
        rig.session._started = time.monotonic() - 100           # the call is "100 s old": over the 30 s limit
        with pytest.raises(SessionLimit):
            rig.session._check_limits()
        rig.session._started = time.monotonic() - 100
        rig.session._susp_total = 90                            # ... but staff owned 90 s of it
        rig.session._check_limits()
        await rig.live.takeover(staff())
        rig.session._susp_at = time.monotonic() - 500           # a long takeover in progress
        rig.session._check_limits()                              # still fine while staff own the call
        await rig.live.return_to_ai(staff())
        assert rig.session._susp_at is None and rig.session._susp_total >= 500
        rig.session._check_limits()
    finally:
        await rig.stop()
