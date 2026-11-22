"""Indonesian-first server-lines for Puspa (G18), now with English (COW-18).

Two locales ship: Indonesian (``id``, the default) and English
(``en``). See ``docs/i18n.md`` for the locale model, the language
switch, and how a language is added.
"""

from .locales import (
    DEFAULT_LOCALE,
    LocaleValidationError,
    check_locales,
    load_locale_file,
    load_locales,
    validate_locales,
)
from .server_lines import (
    LocaleFallback,
    MissingVariable,
    UnknownMessageKey,
    all_keys,
    available_locales,
    catalog,
    clear_fallback_records,
    fallback_records,
    merged_catalog,
    render,
)

__all__ = [
    "DEFAULT_LOCALE",
    "LocaleFallback",
    "LocaleValidationError",
    "MissingVariable",
    "UnknownMessageKey",
    "all_keys",
    "available_locales",
    "catalog",
    "check_locales",
    "clear_fallback_records",
    "fallback_records",
    "load_locale_file",
    "load_locales",
    "merged_catalog",
    "render",
    "validate_locales",
]
