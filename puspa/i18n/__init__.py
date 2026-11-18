"""Indonesian server-lines for Puspa (G18).

The only locale today is Indonesian; see ``docs/i18n.md`` for the
locale model and how a language is added later (deferred).
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
    clear_fallback_records,
    fallback_records,
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
    "check_locales",
    "clear_fallback_records",
    "fallback_records",
    "load_locale_file",
    "load_locales",
    "render",
    "validate_locales",
]
