"""Anonymous analytics + crash reports (G12).

The DuskBot pattern, rewritten for Puspa: anonymous usage
counters and crash reports that can be switched off — and that
never leave the machine on their own.

Rules, enforced here rather than documented elsewhere:

- **Off means off.** When disabled (``enabled=False``, or env
  ``PUSPA_ANALYTICS_ENABLED`` set to ``0`` / ``false``), every
  method is a no-op: nothing is counted, nothing is stored,
  nothing is flushed. The default is enabled, the same as
  DuskBot's switchable-off analytics; the switch is the env var
  plus the runtime ``enable()`` / ``disable()`` methods.
- **Anonymous by construction.** Counters are keyed by a fixed
  event name only (``^[a-z][a-z0-9_]*$`` — anything else is
  rejected, so no user id, task id, or free text can sneak into
  a counter key). No content, no identifiers, no timestamps per
  event are ever recorded — only totals.
- **Crash reports carry no user data.** A report holds the
  exception type, its module, a fingerprint (SHA-256 of
  type + module), and the time. The exception message is never
  included: it may contain user text, file paths, or secrets.
  Optional ``context`` must be a flat dict of strings, numbers,
  and booleans; anything else is dropped.
- **No network, ever.** There is no endpoint and no sender in
  this module. ``flush()`` writes JSONL to a local directory
  (or hands the payload to an injected ``sink`` callable). If a
  remote endpoint is ever wanted, that is a separate decision
  with its own review — this module must not grow one silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["Analytics"]

#: Counter names: lowercase snake_case only, so no identifier or
#: free text can become a counter key.
_COUNTER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: How many crash reports are kept in memory before the oldest
#: is dropped.
MAX_CRASHES = 50


def _env_enabled() -> bool:
    raw = os.environ.get("PUSPA_ANALYTICS_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _clean_context(context: dict | None) -> dict:
    if not isinstance(context, dict):
        return {}
    clean: dict = {}
    for key, value in context.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, (str, int, float, bool)) and value is not None:
            clean[key] = value
    return clean


class Analytics:
    """Anonymous counters + crash reports, switchable off.

    ``sink`` (optional) receives the flushed payload dict instead
    of a file write — tests use it; production uses the default
    local JSONL file under ``directory``.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        sink=None,
        directory: str | Path | None = None,
    ) -> None:
        self._enabled = _env_enabled() if enabled is None else bool(enabled)
        self.sink = sink
        self.directory = Path(directory) if directory is not None else None
        self._counters: dict[str, int] = {}
        self._crashes: list[dict] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        """Switch analytics on at runtime."""
        self._enabled = True

    def disable(self) -> None:
        """Switch analytics off at runtime; drops buffered data."""
        self._enabled = False
        self._counters.clear()
        self._crashes.clear()

    def record_event(self, name: str, *, count: int = 1) -> None:
        """Count one anonymous event. No-op when disabled.

        ``name`` must be lowercase snake_case; anything else
        raises ``ValueError`` so identifiers can never become
        counter keys.
        """
        if not self._enabled:
            return
        if not _COUNTER_NAME.match(name):
            raise ValueError(f"analytics event name must be snake_case: {name!r}")
        if count < 1:
            raise ValueError("count must be positive")
        self._counters[name] = self._counters.get(name, 0) + count

    def crash_report(self, exc: BaseException, *, context: dict | None = None) -> dict | None:
        """Build an anonymous crash report. No-op when disabled.

        The report never includes the exception message. The
        fingerprint is the SHA-256 of ``<module>:<type>``, so the
        same crash class always fingerprints the same.
        """
        if not self._enabled:
            return None
        exc_type = type(exc)
        fingerprint = hashlib.sha256(
            f"{exc_type.__module__}:{exc_type.__qualname__}".encode()
        ).hexdigest()
        report = {
            "type": exc_type.__qualname__,
            "module": exc_type.__module__,
            "fingerprint": fingerprint,
            "occurred_at": datetime.now(UTC).isoformat(),
            "context": _clean_context(context),
        }
        self._crashes.append(report)
        if len(self._crashes) > MAX_CRASHES:
            self._crashes.pop(0)
        return dict(report)

    @property
    def counters(self) -> dict[str, int]:
        """A copy of the buffered counters."""
        return dict(self._counters)

    @property
    def crashes(self) -> list[dict]:
        """A copy of the buffered crash reports."""
        return [dict(report) for report in self._crashes]

    def flush(self) -> dict | None:
        """Write the buffered payload and clear the buffers.

        Returns the flushed payload dict, or None when disabled
        or when there is nothing to flush. The payload carries
        totals only — no identifiers, no content.
        """
        if not self._enabled:
            return None
        payload = {
            "flushed_at": datetime.now(UTC).isoformat(),
            "counters": dict(self._counters),
            "crashes": [dict(report) for report in self._crashes],
        }
        if not payload["counters"] and not payload["crashes"]:
            return None
        if self.sink is not None:
            self.sink(payload)
        elif self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / "analytics.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        self._counters.clear()
        self._crashes.clear()
        return payload
