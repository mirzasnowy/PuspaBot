"""PWA serve modes: one web codebase, two ways to serve it (G31).

Decision locked by Nero (see ``docs/modes-decision.md``): the PWA
(``apps/pwa/``, served by ``puspa.pwa.app``) plays two roles. In
``local`` mode it is the operator surface for a self-hoster running
their own bot on their own machine: the Office screen additionally
shows operator panels (pairing status, gateway status, live log).
In ``hosted`` mode it is the end-user product served from the cloud:
the operator panels are hidden, because the operator surface of a
hosted deployment lives in the (private, not-yet-built) cloud
backend, not in this repo. Never two separate frontends.

Configuration, in precedence order:

1. Explicit ``env`` argument / ``PUSPA_SERVE_MODE`` environment
   variable (``"local"`` or ``"hosted"``, case-insensitive).
2. JSON config file (``config_path`` argument / ``PUSPA_SERVE_MODE_FILE``
   environment variable) holding ``{"serve_mode": "local"}``.
3. Default: ``"local"``.

Fail-closed rules (documented in ``docs/serve-modes.md``):

- Unset means ``"local"``. The operator owns the machine, so the
  operator surface is the safe default: nothing about the hosted
  product is ever shown by accident, and no operator panel is ever
  hidden from the machine's owner by accident.
- An explicitly configured value that is not ``"local"`` or
  ``"hosted"`` is rejected: :func:`resolve_serve_mode` raises
  :class:`ServeModeError`, and ``create_app`` lets it propagate, so
  the server refuses to start rather than serve the wrong surface.
- An unknown mode never shows operator panels:
  :func:`operator_panels_visible` returns ``False`` for anything
  that is not ``"local"``.

Live state (no mocks, no constants): the operator panels read the
real objects the app owns — the pairing store
(``puspa.hosting.pairing.PairingStore``), the action gateway
(``puspa.policy.gateway.Gateway``) plus its decision log, and the
app's own event log (``app.state.event``). :func:`operator_status`
composes those into the payload served by ``GET
/api/operator/status``. In hosted mode the payload carries
``operator_panels: false`` and null panel data.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_SERVE_MODE",
    "GATEWAY_DECISION_LOG_MAX",
    "OPERATOR_LOG_MAX_LINES",
    "SERVE_MODE_ENV_VAR",
    "SERVE_MODE_FILE_ENV_VAR",
    "GatewayDecisionLog",
    "ServeMode",
    "ServeModeError",
    "describe_serve_mode",
    "gateway_status",
    "operator_panels_visible",
    "operator_status",
    "pairing_status",
    "resolve_serve_mode",
]

LOCAL = "local"
HOSTED = "hosted"

#: Unset configuration means local: the operator owns the machine.
DEFAULT_SERVE_MODE = LOCAL

SERVE_MODE_ENV_VAR = "PUSPA_SERVE_MODE"
SERVE_MODE_FILE_ENV_VAR = "PUSPA_SERVE_MODE_FILE"

#: Most recent app events kept on the operator log panel.
OPERATOR_LOG_MAX_LINES = 30

#: Most recent gateway decisions kept in memory.
GATEWAY_DECISION_LOG_MAX = 50

#: Event content keys tried (in order) for a one-line log summary.
_LOG_SUMMARY_KEYS = ("text", "purpose", "reason", "summary")


class ServeModeError(ValueError):
    """An explicitly configured serve mode is not ``local``/``hosted``."""


class ServeMode(StrEnum):
    """The two ways the one PWA codebase is served."""

    LOCAL = LOCAL
    HOSTED = HOSTED


def _coerce_mode(value: str, *, source: str) -> ServeMode:
    """Normalize one configured value, or raise on an unknown one."""
    normalized = value.strip().lower()
    if normalized == LOCAL:
        return ServeMode.LOCAL
    if normalized == HOSTED:
        return ServeMode.HOSTED
    raise ServeModeError(
        f"unknown serve mode {value!r} from {source}; expected 'local' or 'hosted'"
    )


def _read_mode_file(path: str) -> ServeMode:
    """Read ``{"serve_mode": ...}`` from a JSON file. Raises ServeModeError."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ServeModeError(f"serve mode file not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ServeModeError(f"serve mode file {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ServeModeError(f"serve mode file {path} must hold a JSON object")
    raw = data.get("serve_mode")
    if raw is None:
        raise ServeModeError(f"serve mode file {path} has no 'serve_mode' key")
    if not isinstance(raw, str):
        raise ServeModeError(f"serve mode file {path}: 'serve_mode' must be a string")
    return _coerce_mode(raw, source=f"file {path}")


def resolve_serve_mode(
    env: str | None = None,
    config_path: str | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> ServeMode:
    """Resolve the serve mode from env, then file, then the default.

    ``env`` / ``config_path`` override the environment variables when
    given (tests pass them directly). An empty or whitespace-only
    value counts as unset. An explicitly set but unknown value raises
    :class:`ServeModeError` (fail-closed); only a fully unset
    configuration falls back to the ``"local"`` default.
    """
    env_map = os.environ if environ is None else environ
    raw_env = env if env is not None else env_map.get(SERVE_MODE_ENV_VAR)
    if raw_env is not None and raw_env.strip():
        return _coerce_mode(raw_env, source=f"environment variable {SERVE_MODE_ENV_VAR}")
    raw_path = config_path if config_path is not None else env_map.get(SERVE_MODE_FILE_ENV_VAR)
    if raw_path is not None and raw_path.strip():
        return _read_mode_file(raw_path.strip())
    return ServeMode.LOCAL


def operator_panels_visible(mode: ServeMode | str) -> bool:
    """True only in local mode. Anything else (including an unknown
    mode string) hides the operator panels: fail-closed for the
    operator surface."""
    return str(mode).strip().lower() == LOCAL


def describe_serve_mode(mode: ServeMode | str) -> str:
    """One-line human description of a mode (unknown modes named as such)."""
    normalized = str(mode).strip().lower()
    if normalized == LOCAL:
        return "Local operator: this PWA serves the machine owner, with operator panels."
    if normalized == HOSTED:
        return "Hosted user product: this PWA serves end users, without operator panels."
    return f"Unknown serve mode {mode!r}: operator panels hidden."


# ---------------------------------------------------------------------------
# Gateway decision log (the gateway's audit sink)
# ---------------------------------------------------------------------------


class GatewayDecisionLog:
    """Bounded in-memory log of gateway decisions.

    The instance's :meth:`record` is wired as the gateway's ``audit``
    callable, so every decision the gateway makes lands here (and the
    Office panel's "last decision" reads it). Only the decision's
    public fields are kept — never action payloads. Oldest entries
    drop off past ``maxlen``; the log is process-local by design,
    like the pairing store.
    """

    def __init__(self, *, maxlen: int = GATEWAY_DECISION_LOG_MAX, clock=time.time) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._clock = clock

    def record(self, record: dict) -> dict:
        """Record one gateway audit record; returns the stored entry."""
        entry = {
            "allowed": bool(record.get("allowed")),
            "outcome": str(record.get("outcome", "deny")),
            "rule": record.get("rule"),
            "reason": str(record.get("reason", "")),
            "dry_run": bool(record.get("dry_run")),
            "forwarded": bool(record.get("forwarded")),
            "tool": str(record.get("tool", "")),
            "actor": str(record.get("actor", "")),
            "page_host": str(record.get("page_host", "")),
            "recorded_at": float(self._clock()),
        }
        self._entries.append(entry)
        return entry

    def last(self) -> dict | None:
        """The most recent decision, or None when none was made yet."""
        return dict(self._entries[-1]) if self._entries else None

    def recent(self, limit: int = 10) -> list[dict]:
        """The most recent decisions, newest first."""
        return [dict(entry) for entry in reversed(list(self._entries)[-limit:])]

    @property
    def count(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Live-state readers (the operator panels read these, never constants)
# ---------------------------------------------------------------------------


def pairing_status(store) -> dict:
    """Pairing state from a live ``PairingStore``.

    Counts of outstanding codes and sessions, the sessions themselves
    (device label + expiry), and locked-out addresses. Codes and
    session tokens are never exposed — only hashes are stored, and
    this snapshot does not even carry those.
    """
    return store.status_snapshot()


def gateway_status(gateway, decision_log: GatewayDecisionLog | None = None) -> dict:
    """Gateway state from a live ``Gateway`` plus its decision log.

    The active policy (deny/allow rule sources, loaded/stale flags,
    dry-run flag), how many decisions were made, and the most recent
    decision. ``last_decision`` is None until the gateway decides its
    first action — that is honest, not a placeholder: the PWA takes
    no computer actions itself, so decisions appear when an action
    flows through the gateway.
    """
    log = decision_log or GatewayDecisionLog()
    return {
        "policy": gateway.policy_summary(),
        "dry_run": bool(gateway.dry_run),
        "decisions": log.count,
        "last_decision": log.last(),
    }


def _summarize_event(event: dict) -> str:
    """One line for an app event: its type plus the most informative
    content field (message text, task purpose, or reason)."""
    content = event.get("content") or {}
    if isinstance(content, dict):
        for key in _LOG_SUMMARY_KEYS:
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return f"{event.get('type', '')}: {value.strip()[:200]}"
        status = content.get("status")
        if isinstance(status, str) and status.strip():
            return f"{event.get('type', '')}: {status.strip()[:200]}"
    return str(event.get("type", ""))


def operator_log_lines(recent_events, *, limit: int = OPERATOR_LOG_MAX_LINES) -> list[dict]:
    """The live log panel: the newest app events, oldest first, each
    as one line (type, timestamp, summary). The events are the app's
    own ``app.state.event`` entries — user messages, task events,
    approvals, and gateway decisions mirrored there by the wiring."""
    lines = []
    for event in list(recent_events)[-limit:]:
        lines.append(
            {
                "type": str(event.get("type", "")),
                "created_at": float(event.get("created_at", 0.0)),
                "summary": _summarize_event(event),
            }
        )
    return lines


def operator_status(
    mode: ServeMode | str,
    *,
    pairing_store=None,
    gateway=None,
    decision_log: GatewayDecisionLog | None = None,
    recent_events=(),
) -> dict:
    """The full ``GET /api/operator/status`` payload for a serve mode.

    Local mode: pairing + gateway + live log, all read from the live
    objects passed in (missing objects read as empty state, never as
    invented data). Hosted mode: the operator panels do not exist
    here, so the panel fields are null/empty and ``operator_panels``
    is False — the client hides the panels on that flag alone.
    """
    normalized = str(mode).strip().lower()
    if normalized != LOCAL:
        return {
            "mode": normalized if normalized in (LOCAL, HOSTED) else str(mode),
            "operator_panels": False,
            "pairing": None,
            "gateway": None,
            "log": [],
        }
    return {
        "mode": LOCAL,
        "operator_panels": True,
        "pairing": pairing_status(pairing_store) if pairing_store is not None else None,
        "gateway": gateway_status(gateway, decision_log) if gateway is not None else None,
        "log": operator_log_lines(recent_events),
    }
