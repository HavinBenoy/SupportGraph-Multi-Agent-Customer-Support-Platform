"""Password hashing with the standard library's scrypt (memory-hard, salted). No third-party crypto dependency.
Stored form: `scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>`; parameters travel with the hash so they can be raised later."""

import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1
MIN_LENGTH = 10
_DUMMY = None


def hash_password(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"password must be at least {MIN_LENGTH} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest)))
        return hmac.compare_digest(calc, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def burn_time(password: str) -> None:
    """Hash against a dummy so "no such user" costs the same as "wrong password" (no username oracle by timing)."""
    global _DUMMY
    _DUMMY = _DUMMY or hash_password("dummy-password-for-timing")
    verify_password(password, _DUMMY)
