"""Locale catalog files (JSON) and their validation (G18).

A locale is one JSON file per language in ``puspa/i18n/locales/``:
``<language>.json``, a flat object of message key -> template string.
Today ``id.json`` (Indonesian, the default) and ``en.json``
(English) ship; see ``docs/i18n.md`` for how a language is added.

Validation rule (DuskBot pattern, kept simple and honest): every key
in the default locale must exist in every other locale file, and no
template may be empty. With only ``id`` present the check passes
trivially and guards future additions — a new key added to ``id.json``
without a translation fails loudly instead of silently serving the
wrong language.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Default locale: Indonesian. Puspa is Indonesia-first; every other
#: locale falls back to this one, never to English.
DEFAULT_LOCALE = "id"


class LocaleValidationError(ValueError):
    """A locale file is malformed, or the catalogs disagree on keys."""


def load_locale_file(path: str | Path) -> dict[str, str]:
    """Load one locale JSON file.

    The file must be a flat object of string key -> non-empty string
    template. Anything else raises :class:`LocaleValidationError`
    (fail-closed: a broken catalog must never ship half-loaded).
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocaleValidationError(f"cannot load locale file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise LocaleValidationError(f"locale file {path} must be a JSON object")
    catalog: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            raise LocaleValidationError(f"locale file {path} has a non-string key: {key!r}")
        if not isinstance(value, str) or not value.strip():
            raise LocaleValidationError(
                f"locale file {path} key {key!r} must be a non-empty string"
            )
        catalog[key] = value
    return catalog


def available_locales(directory: str | Path) -> tuple[str, ...]:
    """Locale codes with a ``<code>.json`` file in ``directory``."""
    directory = Path(directory)
    if not directory.is_dir():
        return ()
    return tuple(sorted(path.stem for path in directory.glob("*.json")))


def load_locales(directory: str | Path) -> dict[str, dict[str, str]]:
    """Load every locale file in ``directory``.

    Raises :class:`LocaleValidationError` when the default locale
    (``id``) is missing — a Puspa build without Indonesian is not a
    Puspa build.
    """
    directory = Path(directory)
    catalogs = {
        code: load_locale_file(directory / f"{code}.json") for code in available_locales(directory)
    }
    if DEFAULT_LOCALE not in catalogs:
        raise LocaleValidationError(
            f"default locale {DEFAULT_LOCALE!r} is missing from {directory}"
        )
    return catalogs


def validate_locales(
    catalogs: dict[str, dict[str, str]], *, default: str = DEFAULT_LOCALE
) -> list[str]:
    """Check the catalogs agree; return a list of problems (empty = valid).

    - The default locale must be present.
    - Every key in the default locale must exist in every other locale.
    - No locale may carry a key the default locale does not have
      (an untranslated orphan is a key that was removed or never added
      to the default — it must not silently linger).
    """
    problems: list[str] = []
    if default not in catalogs:
        return [f"default locale {default!r} is missing"]
    default_keys = set(catalogs[default])
    for code, catalog in sorted(catalogs.items()):
        if code == default:
            continue
        for key in sorted(default_keys - set(catalog)):
            problems.append(f"locale {code!r} is missing key {key!r}")
        for key in sorted(set(catalog) - default_keys):
            problems.append(f"locale {code!r} has orphan key {key!r} (not in {default!r})")
    return problems


def check_locales(catalogs: dict[str, dict[str, str]], *, default: str = DEFAULT_LOCALE) -> None:
    """Like :func:`validate_locales`, but raises on any problem."""
    problems = validate_locales(catalogs, default=default)
    if problems:
        raise LocaleValidationError("; ".join(problems))
