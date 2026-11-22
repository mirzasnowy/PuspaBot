"""Anonymous usage telemetry sender (AST-09).

Collects aggregate usage statistics — feature-use counters and
durations — and posts them to a configured collector, but only
when telemetry is explicitly switched on. The switch is
:func:`puspa.telemetry.config.resolve_enabled`: the
``PUSPA_TELEMETRY`` environment flag wins, else the per-user
Settings toggle, else off. The default is off.

Rules, enforced here rather than documented elsewhere:

- **Off means off.** When disabled, every method is a no-op:
  nothing is counted, nothing is buffered, nothing is sent.
  ``flush()`` returns ``None`` and never touches the network.
  Tests prove this with a recording transport: zero calls
  while the default is in force.
- **Anonymous by construction.** The payload carries only
  aggregates: the Puspa version, the Python version, the
  platform string (``sys.platform``, e.g. ``"linux"``), feature
  counters, and duration totals (count + summed seconds). No
  user id, no token, no message content, no file path, no
  hostname, no IP address, and no per-event timestamp ever
  enters a payload. Event names must be lowercase snake_case,
  so no identifier or free text can become a counter key.
- **No destination, no send.** Puspa ships no telemetry
  server. With no ``PUSPA_TELEMETRY_ENDPOINT`` configured,
  ``flush()`` sends nothing even when telemetry is on.
- **Best effort, never loud.** A failed send (network error,
  non-2xx answer) is swallowed: telemetry must never break the
  app or surface user-visible errors. The buffers are cleared
  either way, so a dead collector cannot grow memory without
  bound. Only the narrow ``httpx.HTTPError`` family is caught;
  anything else propagates.

The HTTP client is injectable (an :class:`httpx.Client`), the
same seam the voice ASR uses, so tests drive the sender with
an ``httpx.MockTransport`` and no real network is ever needed.
"""

from __future__ import annotations

import math
import platform
import re
import sys

import httpx

from .config import endpoint as _endpoint_from_env
from .config import env_flag as _env_flag
from .config import resolve_enabled

__all__ = ["Telemetry"]

#: Event names: lowercase snake_case only, so no identifier or
#: free text can become a counter key.
_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: Timeout for one telemetry POST, in seconds. Telemetry is
#: background traffic; it must never hold up a request.
SEND_TIMEOUT_SECONDS = 5.0


def _puspa_version() -> str:
    """Installed package version, falling back to the pyproject version."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("puspa")
    except (ImportError, PackageNotFoundError):
        return "0.1.0"


class Telemetry:
    """Anonymous counters + durations, sent only when switched on.

    ``enabled`` (optional) fixes the switch at construction:
    ``None`` resolves it from the ``PUSPA_TELEMETRY`` flag
    (default off when unset). ``endpoint`` (optional) fixes the
    collector: ``None`` reads ``PUSPA_TELEMETRY_ENDPOINT``.
    ``http_client`` (optional) is an :class:`httpx.Client` used
    for the POST; without one a short-lived client is built per
    flush. Tests inject a client backed by
    ``httpx.MockTransport``.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        endpoint: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._enabled = resolve_enabled(env=_env_flag()) if enabled is None else bool(enabled)
        self._endpoint = _endpoint_from_env() if endpoint is None else endpoint
        self._http_client = http_client
        self._counters: dict[str, int] = {}
        self._durations: dict[str, dict[str, float | int]] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def endpoint(self) -> str | None:
        return self._endpoint

    def enable(self) -> None:
        """Switch telemetry on at runtime."""
        self._enabled = True

    def disable(self) -> None:
        """Switch telemetry off at runtime; drops buffered data."""
        self._enabled = False
        self._counters.clear()
        self._durations.clear()

    def record_event(self, name: str, *, count: int = 1) -> None:
        """Count one anonymous event. No-op when disabled.

        ``name`` must be lowercase snake_case; anything else
        raises ``ValueError`` so identifiers can never become
        counter keys.
        """
        if not self._enabled:
            return
        if not _EVENT_NAME.match(name):
            raise ValueError(f"telemetry event name must be snake_case: {name!r}")
        if count < 1:
            raise ValueError("count must be positive")
        self._counters[name] = self._counters.get(name, 0) + count

    def record_duration(self, name: str, seconds: float) -> None:
        """Add one anonymous duration sample. No-op when disabled.

        Only the count and the summed seconds are kept — no
        per-sample timestamp, so no timing side-channel can leak
        when an event happened. ``seconds`` must be finite and
        not negative.
        """
        if not self._enabled:
            return
        if not _EVENT_NAME.match(name):
            raise ValueError(f"telemetry event name must be snake_case: {name!r}")
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"duration must be a finite non-negative number, not {seconds!r}")
        bucket = self._durations.setdefault(name, {"count": 0, "total_seconds": 0.0})
        bucket["count"] = int(bucket["count"]) + 1
        bucket["total_seconds"] = float(bucket["total_seconds"]) + float(seconds)

    @property
    def counters(self) -> dict[str, int]:
        """A copy of the buffered counters."""
        return dict(self._counters)

    @property
    def durations(self) -> dict[str, dict[str, float | int]]:
        """A copy of the buffered duration aggregates."""
        return {name: dict(bucket) for name, bucket in self._durations.items()}

    def build_payload(self) -> dict | None:
        """The anonymous aggregate payload, or ``None``.

        Returns ``None`` when disabled or when nothing was
        recorded — no empty beacon is ever sent. The payload
        keys are fixed: version, runtime, counters, durations,
        and the flush time. Nothing else is ever added.
        """
        if not self._enabled:
            return None
        if not self._counters and not self._durations:
            return None
        return {
            "puspa_version": _puspa_version(),
            "python_version": platform.python_version(),
            "platform": sys.platform,
            "counters": dict(self._counters),
            "durations": {name: dict(bucket) for name, bucket in self._durations.items()},
        }

    def _post(self, endpoint: str, payload: dict) -> None:
        """POST one payload to the collector; raises on failure.

        Uses the injected client when one was given, else a
        short-lived client. The caller decides what a failure
        means (best effort: swallow and drop).
        """
        if self._http_client is not None:
            response = self._http_client.post(endpoint, json=payload, timeout=SEND_TIMEOUT_SECONDS)
        else:
            with httpx.Client(timeout=SEND_TIMEOUT_SECONDS) as client:
                response = client.post(endpoint, json=payload)
        response.raise_for_status()

    def flush(self) -> dict | None:
        """Send the buffered payload to the collector, if any.

        Returns the sent payload dict, or ``None`` when disabled,
        when nothing was recorded, when no endpoint is
        configured, or when the send failed. The buffers are
        cleared on every flush that had something to send, so a
        failed send is dropped rather than retried forever.
        """
        payload = self.build_payload()
        if payload is None:
            return None
        endpoint = self._endpoint
        if endpoint is None:
            return None
        try:
            self._post(endpoint, payload)
        except httpx.HTTPError:
            # Best effort: a dead collector never breaks the app.
            # The buffered data is dropped either way.
            self._counters.clear()
            self._durations.clear()
            return None
        self._counters.clear()
        self._durations.clear()
        return payload
