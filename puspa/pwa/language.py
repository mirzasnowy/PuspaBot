"""Per-user display language for the PWA (COW-18).

One row per user in Postgres (table ``user_language``): which of the
shipped locales the user picked in Settings (``"id"`` Indonesian or
``"en"`` English). The row holds the locale code only — the strings
themselves live in the catalog files (``puspa/i18n/locales/``), never
in the database. The default is Indonesian: a user who never opened
the switch reads Indonesian, and so does every anonymous request.

Follows the same rules as :mod:`puspa.telemetry.settings`: the row
holds the choice only, and the schema comes from a numbered
migration (``0040_user_language``).
"""

from __future__ import annotations

from dataclasses import dataclass

from puspa.accounts.ports import _connect, resolve_dsn
from puspa.i18n import DEFAULT_LOCALE, available_locales
from puspa.migrations import apply_migrations

__all__ = [
    "LanguageSettings",
    "create_schema",
    "get_language",
    "get_settings",
    "save_language",
    "save_settings",
]

#: Locale codes the Settings switch offers, in display order:
#: Indonesian first (the default), then English.
SUPPORTED_LOCALES = (DEFAULT_LOCALE, "en")


def _normalize_locale(locale: object) -> str:
    """A locale code as stored: lowercase, stripped, must be shipped.

    Raises ``ValueError`` naming the problem for anything else, so a
    typo can never store a locale the catalogs do not have.
    """
    code = locale.strip().lower() if isinstance(locale, str) else ""
    if code not in available_locales():
        raise ValueError(f"unknown locale {locale!r} (known: {', '.join(available_locales())})")
    return code


@dataclass(frozen=True)
class LanguageSettings:
    """One user's stored language choice.

    ``locale`` is one of :data:`SUPPORTED_LOCALES`; the default is
    Indonesian (``"id"``).
    """

    locale: str = DEFAULT_LOCALE

    def to_public_dict(self) -> dict:
        """The settings as the Settings screen sees them."""
        return {"locale": self.locale, "available_locales": list(SUPPORTED_LOCALES)}


def create_schema(dsn=None) -> None:
    """Apply the ``user_language`` table (migration 0040_user_language)."""
    apply_migrations(resolve_dsn(dsn), target="0040")


def save_settings(user_id, settings: LanguageSettings | dict | str, dsn=None) -> LanguageSettings:
    """Persist a user's language choice.

    Accepts a :class:`LanguageSettings`, a plain dict with a
    ``locale`` key, or a bare locale string. Anything that is not a
    shipped locale is refused with ``ValueError``. Returns the
    stored settings.
    """
    if isinstance(settings, LanguageSettings):
        locale = settings.locale
    elif isinstance(settings, dict):
        locale = settings.get("locale", DEFAULT_LOCALE)
    elif isinstance(settings, str):
        locale = settings
    else:
        raise ValueError("settings must be a LanguageSettings, a dict, or a string")
    return save_language(user_id, locale, dsn=dsn)


def save_language(user_id, locale: str, dsn=None) -> LanguageSettings:
    """Persist one user's locale code; returns the stored settings."""
    code = _normalize_locale(locale)
    create_schema(dsn)

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_language (user_id, locale) VALUES (%s, %s)"
                " ON CONFLICT (user_id) DO UPDATE SET"
                " locale = EXCLUDED.locale, updated_at = now()",
                (user_id, code),
            )
        conn.commit()
    return LanguageSettings(locale=code)


def get_settings(user_id, dsn=None) -> LanguageSettings:
    """Load a user's stored language settings; the defaults when none is stored."""
    return LanguageSettings(locale=get_language(user_id, dsn=dsn))


def get_language(user_id, dsn=None) -> str:
    """Load a user's locale code; Indonesian when nothing is stored.

    Never raises for a missing row: a user who never opened the
    switch reads Indonesian. A stored code the catalogs no longer
    ship (a locale file removed after the choice was saved) also
    reads Indonesian instead of crashing the request.
    """
    create_schema(dsn)

    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT locale FROM user_language WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    if row is None:
        return DEFAULT_LOCALE
    stored = row[0].strip().lower() if isinstance(row[0], str) else ""
    return stored if stored in available_locales() else DEFAULT_LOCALE
