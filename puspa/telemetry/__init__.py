"""Anonymous usage telemetry, off by default for self-host (AST-09).

Public surface of :mod:`puspa.telemetry`:

- :class:`puspa.telemetry.client.Telemetry` — the recorder and
  sender. Counts anonymous feature-use events and duration
  aggregates, and posts them to a configured collector only
  when telemetry is explicitly switched on.
- :func:`puspa.telemetry.config.resolve_enabled` — the
  effective switch: the ``PUSPA_TELEMETRY`` environment flag
  wins when set, else the per-user Settings toggle, else off.
- :class:`puspa.telemetry.settings.TelemetrySettings` plus
  :func:`puspa.telemetry.settings.get_settings` /
  :func:`puspa.telemetry.settings.save_settings` — the stored
  per-user toggle (Postgres, migration 0036).
"""

from __future__ import annotations

from .client import Telemetry
from .config import endpoint, env_flag, parse_endpoint, parse_env_flag, resolve_enabled
from .settings import TelemetrySettings, create_schema, get_settings, save_settings

__all__ = [
    "Telemetry",
    "TelemetrySettings",
    "create_schema",
    "endpoint",
    "env_flag",
    "get_settings",
    "parse_endpoint",
    "parse_env_flag",
    "resolve_enabled",
    "save_settings",
]
