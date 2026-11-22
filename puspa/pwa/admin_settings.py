"""Instance-level settings for the local admin dashboard.

One tiny table, ``instance_settings`` (migration 0039): key/value
rows for settings that belong to the instance, not to one user.
Today the only key is ``instance_name`` — the display name shown
on the admin dashboard (``/admin``). The allowlist is the guard:
a new key is added here deliberately, never by a client naming
it in a request.

Follows the same rules as :mod:`puspa.telemetry.settings`: the
row holds the choice only, and the schema comes from a numbered
migration.
"""

from __future__ import annotations

from puspa.accounts.ports import _connect, resolve_dsn
from puspa.migrations import apply_migrations

__all__ = [
    "DEFAULT_INSTANCE_NAME",
    "MAX_INSTANCE_NAME_LENGTH",
    "create_schema",
    "get_instance_name",
    "save_instance_name",
]

#: The name shown on the admin dashboard before the operator picks one.
DEFAULT_INSTANCE_NAME = "Puspa"

#: Longest instance name the dashboard accepts (a display name, not a sentence).
MAX_INSTANCE_NAME_LENGTH = 80

#: The only key this module stores. See the module docstring.
_INSTANCE_NAME_KEY = "instance_name"


def create_schema(dsn=None) -> None:
    """Apply the ``instance_settings`` table (migration 0039_instance_settings)."""
    apply_migrations(resolve_dsn(dsn), target="0039")


def get_instance_name(dsn=None) -> str:
    """The instance display name; ``"Puspa"`` when never set."""
    create_schema(dsn)

    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM instance_settings WHERE key = %s", (_INSTANCE_NAME_KEY,))
        row = cur.fetchone()
    if row is None:
        return DEFAULT_INSTANCE_NAME
    return str(row[0])


def save_instance_name(name, dsn=None) -> str:
    """Persist the instance display name and return it stripped.

    A name must be a non-empty string of at most 80 characters
    after stripping; anything else is refused with ``ValueError``.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("instance name must be a non-empty string")
    cleaned = name.strip()
    if len(cleaned) > MAX_INSTANCE_NAME_LENGTH:
        raise ValueError(f"instance name must be at most {MAX_INSTANCE_NAME_LENGTH} characters")
    create_schema(dsn)

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO instance_settings (key, value) VALUES (%s, %s)"
                " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,"
                " updated_at = now()",
                (_INSTANCE_NAME_KEY, cleaned),
            )
        conn.commit()
    return cleaned
