import pytest

from app.api.ratelimit import SlidingWindow
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import (
    Principal, TokenError, check_anonymous_ticket, check_student_ticket, issue_token, new_anonymous_ticket,
    new_student_ticket, verify_token,
)

S = "s" * 40


def test_password_hash_roundtrip_and_salting():
    a, b = hash_password("a-long-password"), hash_password("a-long-password")
    assert a != b and verify_password("a-long-password", a) and not verify_password("a-long-passworD", a)
    assert "a-long-password" not in a
    with pytest.raises(ValueError):
        hash_password("short")


def test_token_roundtrip_tamper_and_expiry():
    p = Principal("t1", "staff", "s1", None)
    tok = issue_token(S, p, ttl_seconds=60)
    assert verify_token(S, tok) == p
    body, sig = tok.rsplit(".", 1)
    for bad in (body + "." + sig[:-2] + "AA", "x" + tok, body):
        with pytest.raises(TokenError):
            verify_token(S, bad)
    with pytest.raises(TokenError):
        verify_token(S, issue_token(S, p, ttl_seconds=-1))


def test_ticket_ids_are_bound_to_tenant_and_person():
    g = new_anonymous_ticket(S, "t1")
    assert check_anonymous_ticket(S, "t1", g) and not check_anonymous_ticket(S, "t2", g)
    st = new_student_ticket(S, "t1", "stu_a")
    assert check_student_ticket(S, "t1", "stu_a", st)
    assert not check_student_ticket(S, "t1", "stu_b", st) and not check_student_ticket(S, "t2", "stu_a", st)
    assert not check_student_ticket(S, "t1", "stu_a", g) and not check_anonymous_ticket(S, "t1", st)
    assert not check_student_ticket(S, "t1", "stu_a", "garbage")


def test_sliding_window_limits_and_resets():
    w = SlidingWindow(2, 60)
    assert w.allow("k") and w.allow("k") and not w.allow("k") and w.allow("other")
    f = SlidingWindow(2, 60)
    f.record("u"); assert not f.blocked("u")
    f.record("u"); assert f.blocked("u")
    f.reset("u"); assert not f.blocked("u")
