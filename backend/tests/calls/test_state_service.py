"""M4 call lifecycle: the state machine, atomic compare-and-set transitions, the call service, events, tenant isolation (SQLite; Postgres in tests/db)."""

import itertools

import pytest

from app.calls import service as svc
from app.calls.state import ALLOWED, TERMINAL, TransitionError, can, transition
from app.db.models import Call, CallEvent
from app.db.session import session_scope

T, OTHER = "qspiders_sample", "other_institute"
STATES = list(ALLOWED)


def new_call(db, tenant=T):
    return svc.create_call(db, tenant, "s-ticket.tag", "stu_asha")


def test_create_gives_unique_ids_tenant_and_ticket_association_and_a_created_event():
    with session_scope() as db:
        a, b = new_call(db), new_call(db)
        assert a.id != b.id and a.id.startswith("c_") and a.tenant_id == T and a.ticket_id == "s-ticket.tag" and a.student_id == "stu_asha"
        assert (a.status, a.owner, a.epoch, a.created_at) == ("created", "none", 0, a.created_at) and a.created_at is not None
    with session_scope() as db:
        assert [(e.kind, e.actor) for e in db.query(CallEvent).filter_by(call_id=a.id)] == [("created", "caller")]


def test_transition_table_is_exactly_the_documented_lifecycle():
    assert ALLOWED == {
        "created": {"active_ai", "ended", "failed"}, "active_ai": {"staff_takeover", "ended", "failed"},
        "staff_takeover": {"returning_to_ai", "ended", "failed"}, "returning_to_ai": {"active_ai", "ended", "failed"}, "ended": set(), "failed": set()}
    assert all(not ALLOWED[s] for s in TERMINAL)


@pytest.mark.parametrize("src,dst", list(itertools.product(STATES, STATES)))
def test_every_pair_of_states_either_moves_or_is_refused_on_the_backend(src, dst):
    with session_scope() as db:
        c = new_call(db)
        db.execute(Call.__table__.update().where(Call.id == c.id).values(status=src))   # put the row directly into `src`
        cid = c.id
    with session_scope() as db:
        if can(src, dst):
            moved = transition(db, T, cid, src, dst, subject="staff1", reason="x")
            assert moved.status == dst and moved.epoch == 1
        else:
            with pytest.raises(TransitionError):
                transition(db, T, cid, src, dst)
    with session_scope() as db:                                  # a refused move changed nothing
        assert db.get(Call, (T, cid)).status == (dst if can(src, dst) else src)


def test_compare_and_set_two_requests_from_the_same_stale_view_only_one_wins():
    with session_scope() as db:
        cid = new_call(db).id
        transition(db, T, cid, "created", "active_ai")
    with session_scope() as db1, session_scope() as db2:         # both read "active_ai"; both try to take the call
        seen1, seen2 = db1.get(Call, (T, cid)).status, db2.get(Call, (T, cid)).status
        assert seen1 == seen2 == "active_ai"
        transition(db1, T, cid, seen1, "staff_takeover", subject="staff1")
        with pytest.raises(TransitionError) as e:
            transition(db2, T, cid, seen2, "staff_takeover", subject="staff2")
        assert e.value.current == "staff_takeover"
    with session_scope() as db:
        c = db.get(Call, (T, cid))
        assert c.owner_subject == "staff1" and c.owner == "staff"


def test_every_ownership_move_bumps_the_epoch_and_stamps_its_timestamp():
    with session_scope() as db:
        cid = new_call(db).id
        c = transition(db, T, cid, "created", "active_ai")
        assert (c.epoch, c.owner, c.connected_at is not None) == (1, "ai", True)
        c = transition(db, T, cid, "active_ai", "staff_takeover", subject="staff1")
        assert (c.epoch, c.owner, c.owner_subject, c.takeover_at is not None) == (2, "staff", "staff1", True)
        c = transition(db, T, cid, "staff_takeover", "returning_to_ai")
        assert (c.epoch, c.owner, c.owner_subject, c.returned_at) == (3, "none", None, None)
        c = transition(db, T, cid, "returning_to_ai", "active_ai")
        assert (c.epoch, c.owner, c.returned_at is not None) == (4, "ai", True)
        c = transition(db, T, cid, "active_ai", "ended", reason="done")
        assert (c.epoch, c.owner, c.ended_at is not None, c.end_reason) == (5, "none", True, "done")


def test_a_terminal_call_can_never_move_again():
    with session_scope() as db:
        cid = new_call(db).id
        svc.finish(db, T, cid, reason="a")
        for dst in STATES:
            with pytest.raises(TransitionError):
                transition(db, T, cid, "ended", dst)
        again = svc.finish(db, T, cid, reason="b", failed=True)     # idempotent: no second end, no change of reason or status
        assert again.status == "ended" and again.end_reason == "a"
    with session_scope() as db:
        assert [e.kind for e in db.query(CallEvent).filter_by(call_id=cid)] == ["created", "ended"]


def test_events_are_written_with_the_move_and_a_refused_move_writes_none():
    with session_scope() as db:
        cid = new_call(db).id
        svc.move(db, T, cid, "created", "active_ai", "caller", "connected")
        with pytest.raises(TransitionError):
            svc.move(db, T, cid, "created", "active_ai", "caller", "connected")
    with session_scope() as db:
        assert [e.kind for e in db.query(CallEvent).filter_by(call_id=cid).order_by(CallEvent.id)] == ["created", "connected"]


def test_event_details_keep_only_short_scalar_allowed_keys_so_no_text_or_audio_can_be_stored():
    with session_scope() as db:
        cid = new_call(db).id
        svc.add_event(db, T, cid, "listen_started", "staff1", role="staff", transcript="my card number is 4111", audio=b"\x00\x01", reason="r" * 500,
                      listeners=2, nested={"a": 1}, text="hello")
    with session_scope() as db:
        d = db.query(CallEvent).filter_by(call_id=cid, kind="listen_started").one().detail
    assert set(d) == {"role", "reason", "listeners"} and len(d["reason"]) == svc.MAX_DETAIL_STR and d["listeners"] == 2


def test_unknown_event_kinds_are_rejected():
    with session_scope() as db:
        cid = new_call(db).id
        with pytest.raises(AssertionError):
            svc.add_event(db, T, cid, "something_else", "x")


def test_calls_of_another_tenant_are_not_found_not_listed_and_not_movable():
    with session_scope() as db:
        mine, theirs = new_call(db).id, new_call(db, OTHER).id
        with pytest.raises(svc.NotFound):
            svc.get_call(db, OTHER, mine)
        with pytest.raises(svc.NotFound):
            svc.list_events(db, T, theirs)
        assert {c.id for c in svc.list_calls(db, T)} == {mine} and {c.id for c in svc.list_calls(db, OTHER)} == {theirs}
        with pytest.raises(TransitionError):
            transition(db, OTHER, mine, "created", "active_ai")      # tenant is part of the WHERE: nothing matches
        assert db.get(Call, (T, mine)).status == "created"


def test_list_keeps_live_calls_and_recently_ended_ones_only():
    from datetime import timedelta
    with session_scope() as db:
        live, recent, old = new_call(db).id, new_call(db).id, new_call(db).id
        svc.finish(db, T, recent, reason="x")
        svc.finish(db, T, old, reason="x")
        db.execute(Call.__table__.update().where(Call.id == old).values(ended_at=svc.now() - timedelta(hours=3)))
    with session_scope() as db:
        got = {c.id for c in svc.list_calls(db, T, keep_ended_minutes=30)}
        assert got == {live, recent}
        assert {c.id for c in svc.list_calls(db, T, keep_ended_minutes=0)} == {live}


def test_startup_sweep_fails_calls_left_live_by_a_previous_process():
    with session_scope() as db:
        a, b, done = new_call(db).id, new_call(db).id, new_call(db).id
        transition(db, T, b, "created", "active_ai")
        svc.finish(db, T, done, reason="x")
    with session_scope() as db:
        assert svc.sweep_orphans(db) == 2
    with session_scope() as db:
        assert [(db.get(Call, (T, i)).status, db.get(Call, (T, i)).end_reason) for i in (a, b, done)] == [("failed", "server_restart")] * 2 + [("ended", "x")]
