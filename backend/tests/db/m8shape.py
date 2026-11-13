"""Shape a database built from today's models back to a pre-M8 revision (< 0014): the M8 tables and `calls` columns did not exist yet."""

from sqlalchemy import text

M8_TABLES = ("telephony_audit", "phone_pins", "account_phones")
M8_CALL_COLUMNS = ("channel", "caller_status", "caller_role", "caller_masked", "caller_hash", "provider", "provider_call_ref", "verified_at")


def drop_m8(c) -> None:
    for t in M8_TABLES:
        c.execute(text(f"DROP TABLE {t}"))
    for col in M8_CALL_COLUMNS:
        c.execute(text(f"ALTER TABLE calls DROP COLUMN {col}"))
