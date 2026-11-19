"""Nightly live smoke lane (E-04, plan section 4.4).

One probe per live dependency, run once a day from
``.github/workflows/nightly-smoke.yml`` with low-capped keys and
test accounts. Every probe returns a :class:`ProbeResult` —
status, latency, cost, and the provider's response shape — and
the run is appended to the JSONL time series in
:mod:`puspa.nightly.store`, so provider behaviour changes are
visible over time instead of collapsing to green/red.

The probes never touch the network unless the caller hands them
a transport or client: tests pass ``httpx.MockTransport`` and
prove the harness with fakes. The first live run happens in CI
(see ``docs/nightly-smoke.md``); no real key, secret, or token
is ever in this package or its tests.
"""

from __future__ import annotations

from .probes import (
    PROBE_NAMES,
    ProbeResult,
    SmokeError,
    probe_google_login,
    probe_ollama_turn,
    probe_openrouter_fallback,
    probe_openrouter_turn,
    probe_skill_discovery,
    probe_telegram_approval,
    probe_telegram_roundtrip,
    run_probes,
)
from .store import append_record, load_records, summarize, write_summary_markdown

__all__ = [
    "PROBE_NAMES",
    "ProbeResult",
    "SmokeError",
    "append_record",
    "load_records",
    "probe_google_login",
    "probe_ollama_turn",
    "probe_openrouter_fallback",
    "probe_openrouter_turn",
    "probe_skill_discovery",
    "probe_telegram_approval",
    "probe_telegram_roundtrip",
    "run_probes",
    "summarize",
    "write_summary_markdown",
]
