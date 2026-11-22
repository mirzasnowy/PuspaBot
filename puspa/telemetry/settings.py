"""Per-user telemetry toggle for the PWA Settings screen (AST-09).

One row per user in Postgres (table ``telemetry_settings``):
whether this user switched anonymous usage telemetry on. The
row holds the toggle only — never an endpoint, a key, or any
usage data. The effective switch is
:func:`puspa.telemetry.config.resolve_enabled`: the
``PUSPA_TELEMETRY`` environment flag wins when it is set, else
this toggle, else off. The default is off.

Follows the same rules as :mod:`puspa.pwa.voice_settings`:
the row holds the choice only, and the schema comes from a
numbered migration (``0036_telemetry_settings``).
"""

from __future__ import annotations

from dataclasses import dataclass

from puspa.accounts.ports import _connect, resolve_dsn
from puspa.migrations import apply_migrations

__all__ = [
    "TelemetrySettings",
    "create_schema",
    "get_settings",
    "save_settings",
]


@dataclass(frozen=True)
class TelemetrySettings:
    """One user's stored telemetry choice.

    ``enabled`` is the Settings toggle: ``True`` means the user
    switched anonymous usage telemetry on. The default is
    ``False`` (off), and the environment flag can still override
    it either way — see :mod:`puspa.telemetry.config`.
    """

    enabled: bool = False

    def to_public_dict(self) -> dict:
        """The settings as the Settings screen sees them."""
        return {"enabled": self.enabled}


def create_schema(dsn=None) -> None:
    """Apply the ``telemetry_settings`` table (migration 0036_telemetry_settings)."""
    apply_migrations(resolve_dsn(dsn), target="0036")


def save_settings(
    user_id, settings: TelemetrySettings | dict | bool, dsn=None
) -> TelemetrySettings:
    """Persist a user's telemetry toggle.

    Accepts a :class:`TelemetrySettings`, a plain dict with an
    ``enabled`` key, or a bare bool. Anything that is not a real
    bool is refused with ``ValueError``: a privacy switch must
    never be set by a mistyped string. Returns the stored
    settings.
    """
    if isinstance(settings, bool):
        enabled = settings
    elif isinstance(settings, TelemetrySettings):
        enabled = settings.enabled
    elif isinstance(settings, dict):
        if "enabled" not in settings:
            raise ValueError("telemetry settings need an 'enabled' boolean")
        enabled = settings["enabled"]
    else:
        raise ValueError("settings must be a TelemetrySettings, a dict, or a bool")
    if not isinstance(enabled, bool):
        raise ValueError(f"telemetry 'enabled' must be a boolean, not {enabled!r}")
    create_schema(dsn)

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO telemetry_settings (user_id, enabled)"
                " VALUES (%s, %s)"
                " ON CONFLICT (user_id) DO UPDATE SET"
                " enabled = EXCLUDED.enabled,"
                " updated_at = now()",
                (user_id, enabled),
            )
        conn.commit()
    return get_settings(user_id, dsn=dsn)


def get_settings(user_id, dsn=None) -> TelemetrySettings:
    """Load a user's stored telemetry toggle; off when none is stored.

    Never raises for a missing row: a user who never opened the
    toggle gets telemetry off.
    """
    create_schema(dsn)

    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT enabled FROM telemetry_settings WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        return TelemetrySettings(enabled=False)
    return TelemetrySettings(enabled=bool(row[0]))
