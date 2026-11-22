"""Redaction for shareable diagnostics bundles.

Pattern: AstrBot added message tracing plus a redacted
diagnostics export in v4.29.3 — one bundle a user can paste
into a bug report without leaking secrets. The idea is taken
from there; every line below is Puspa's own code, never copied,
and AstrBot is not a dependency.

The redaction itself reuses Puspa's single redaction point,
:mod:`puspa.credentials.redact` (API keys, tokens, Bearer /
Authorization headers, passwords, DB URLs, private-key blocks,
vault values, home paths, long high-entropy tokens). This
module adds only what a *shareable* bundle needs on top:

- email addresses: personal data, never part of a bug report;
- a recursive pass over dicts / lists / tuples / sets, so a
  secret nested inside a span attribute or a ledger event
  content is caught wherever it sits.

The marker is ``«redacted»``, the same marker the rest of
Puspa uses, so redacted text looks the same everywhere.
"""

from __future__ import annotations

import re
from typing import Any

from puspa.credentials.redact import REDACTED, redact_record, redact_text

__all__ = [
    "REDACTED",
    "redact_diagnostics_text",
    "redact_diagnostics_value",
    # Re-exported so callers of this module need only one import.
    "redact_record",
    "redact_text",
]

#: Email addresses (personal data in a bundle meant to be
#: pasted into a public bug report).
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact_diagnostics_text(text: str) -> str:
    """Redact secrets *and* email addresses in ``text``.

    Secrets first (the shared redaction point), then emails.
    The result never contains a matched secret or a matched
    email address.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    return _EMAIL_PATTERN.sub(REDACTED, redact_text(text))


def redact_diagnostics_value(value: Any) -> Any:
    """Recursively redact a JSON-like value for a diagnostics bundle.

    Strings go through :func:`redact_diagnostics_text`; dict
    keys are left alone (a key name is structure, not a secret)
    while dict values, list items, tuple items, and set members
    are redacted in place. Other values pass through unchanged.
    """
    if isinstance(value, str):
        return redact_diagnostics_text(value)
    if isinstance(value, dict):
        return {key: redact_diagnostics_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_diagnostics_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [redact_diagnostics_value(item) for item in value]
    return value
