"""Signed, expiring tokens (HMAC-SHA256). Stateless: the API verifies a token with the server secret alone.

    token = base64url(json payload) + "." + base64url(hmac)

Payload: tenant, role, subject (username), student_id, exp. Anything unsigned or altered, expired, or signed with another
secret is rejected. There is no "none" algorithm and no algorithm field to attack. Revocation: disable the account (checked at
login only) or rotate `SG_AUTH_SECRET` (invalidates every token). Also issues/validates the server-made ticket ids that bind an
anonymous chat to the tenant it was created for."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass


class TokenError(ValueError):
    """The token is malformed, forged or expired."""


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    role: str            # "student" | "staff" | "admin"
    subject: str         # the account's username
    student_id: str | None = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def issue_token(secret: str, p: Principal, *, ttl_seconds: int, now: float | None = None) -> str:
    payload = {"t": p.tenant_id, "r": p.role, "s": p.subject, "st": p.student_id, "exp": int((now or time.time()) + ttl_seconds)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(secret, body)}"


def verify_token(secret: str, token: str, *, now: float | None = None) -> Principal:
    try:
        body, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(secret, body)):
            raise TokenError("bad signature")
        d = json.loads(_unb64(body))
        if d["exp"] < (now or time.time()):
            raise TokenError("expired")
        if d["r"] not in ("student", "staff", "admin"):
            raise TokenError("bad role")
        return Principal(tenant_id=d["t"], role=d["r"], subject=d["s"], student_id=d.get("st"))
    except TokenError:
        raise
    except (ValueError, KeyError, TypeError) as e:
        raise TokenError("malformed token") from e


def new_anonymous_ticket(secret: str, tenant_id: str) -> str:
    """`a-<128-bit random>.<tag>`: only the server can make one, and the tag binds it to the tenant."""
    rand = secrets.token_hex(16)
    return f"a-{rand}.{_sign(secret, f'anon:{tenant_id}:{rand}')[:16]}"


def check_anonymous_ticket(secret: str, tenant_id: str, ticket_id: str) -> bool:
    try:
        head, tag = ticket_id.split(".")
        if not head.startswith("a-"):
            return False
        return hmac.compare_digest(tag, _sign(secret, f"anon:{tenant_id}:{head[2:]}")[:16])
    except ValueError:
        return False


def new_student_ticket(secret: str, tenant_id: str, student_id: str) -> str:
    """`s-<128-bit random>.<tag>`, bound to the tenant AND the student: a client cannot invent an id, and an id minted for one
    student is useless to another (it also fails the thread lookup, which includes the student id)."""
    rand = secrets.token_hex(16)
    return f"s-{rand}.{_sign(secret, f'stu:{tenant_id}:{student_id}:{rand}')[:16]}"


def check_student_ticket(secret: str, tenant_id: str, student_id: str, ticket_id: str) -> bool:
    try:
        head, tag = ticket_id.split(".")
        if not head.startswith("s-"):
            return False
        return hmac.compare_digest(tag, _sign(secret, f"stu:{tenant_id}:{student_id}:{head[2:]}")[:16])
    except ValueError:
        return False
