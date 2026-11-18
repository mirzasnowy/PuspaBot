"""Server-written sentences in the user's language (G18).

DuskBot pattern, rewritten as Puspa code: the server writes its
sentences ("server-lines") in the user's language from a message-key
registry, never by concatenating fragments and never by mixing
English into the Indonesian UI. Today the only locale is Indonesian
(``locales/id.json``); other locales are deferred until the global
expansion (see ``docs/i18n.md``).

Fail-closed rules (the whole point of this module):

- Unknown key raises :class:`UnknownMessageKey` — the server must
  never serve a silently wrong sentence.
- Missing template variable raises :class:`MissingVariable`.
- Unknown locale falls back to Indonesian (the default) and the
  fallback is recorded in :func:`fallback_records` — it never
  crashes, and it never silently serves English to an Indonesian
  user. (There is no English catalog at all, so there is nothing
  English to fall back *to*.)
- Templates use named placeholders only (``{nominal}``), so a
  sentence is one whole string in the catalog, never assembled from
  parts at the call site.

The default catalog is loaded at import time, so a broken
``id.json`` fails startup loudly instead of failing the first user
who hits an error path.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from pathlib import Path

from .locales import DEFAULT_LOCALE, load_locales

LOCALES_DIR = Path(__file__).resolve().parent / "locales"


class UnknownMessageKey(KeyError):
    """``render`` was asked for a key no locale defines."""


class MissingVariable(KeyError):
    """``render`` was called without a variable its template needs."""


@dataclass(frozen=True)
class LocaleFallback:
    """One recorded fallback: an unknown locale served as Indonesian."""

    requested_locale: str
    key: str
    used_locale: str = DEFAULT_LOCALE


#: Fallbacks recorded since import (in order). Read via
#: :func:`fallback_records`; tests clear it with
#: :func:`clear_fallback_records`.
_fallbacks: list[LocaleFallback] = []

#: Loaded catalogs, keyed by locale code. Loaded once at import.
_catalogs: dict[str, dict[str, str]] = load_locales(LOCALES_DIR)


def available_locales() -> tuple[str, ...]:
    """Locale codes with a catalog file (today: ``("id",)``)."""
    return tuple(sorted(_catalogs))


def all_keys(locale: str = DEFAULT_LOCALE) -> frozenset[str]:
    """Message keys defined in ``locale`` (default: Indonesian)."""
    return frozenset(_catalogs[locale])


def fallback_records() -> tuple[LocaleFallback, ...]:
    """Fallbacks recorded so far (unknown locale -> Indonesian)."""
    return tuple(_fallbacks)


def clear_fallback_records() -> None:
    """Forget recorded fallbacks (tests only)."""
    _fallbacks.clear()


def _template(key: str, locale: str) -> str:
    """Template for ``key`` in ``locale``, falling back to Indonesian."""
    catalog = _catalogs.get(locale)
    if catalog is None:
        _fallbacks.append(LocaleFallback(requested_locale=locale, key=key))
        catalog = _catalogs[DEFAULT_LOCALE]
    template = catalog.get(key)
    if template is None:
        raise UnknownMessageKey(f"unknown server-lines key: {key!r} (locale {locale!r})")
    return template


def render(key: str, locale: str = DEFAULT_LOCALE, **variables: object) -> str:
    """Render the server sentence for ``key`` in ``locale``.

    ``variables`` fill the template's named placeholders. An unknown
    key raises :class:`UnknownMessageKey`; a missing variable raises
    :class:`MissingVariable` naming the variable. An unknown locale
    renders the Indonesian template and records the fallback.
    """
    template = _template(key, locale)
    required = {field for _, field, _, _ in string.Formatter().parse(template) if field is not None}
    missing = sorted(required - set(variables))
    if missing:
        raise MissingVariable(f"render({key!r}) is missing variable(s): {', '.join(missing)}")
    return template.format(**variables)
