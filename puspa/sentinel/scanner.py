"""Sentinel scanner (G12).

The periodic scan itself: read the ledger, run the five
detectors, have the independent verifier confirm each candidate
(``verifier.py``, K8 — separate from the detectors), merge the
confirmed issues into the state file, prune what is stale, and
write the state back.

Rescan rule: a scan requested sooner than
``thresholds.rescan_minutes`` after ``state["last_scan"]`` is a
no-op (reported as ``skipped``) unless ``force=True``. The
30-minute default is DuskBot's rescan interval.

Auto-resolution: an ``open`` issue whose condition the verifier
now calls ``stale`` is resolved by the scanner with
``resolution="auto: condition cleared"`` — the condition is
gone, so keeping the issue open would be noise. An
``acknowledged`` issue is never auto-resolved: the operator
said "I know", and only the operator clears that.

``run_sentinel_scan`` is the entry point the scheduler calls
(``puspa/scheduler/scheduler.py`` dispatches the
``sentinel_scan`` trigger here). It never raises: any failure
is reported as a ``{"status": "error", ...}`` dict, the same
fail-soft shape the trust-root refresh job uses. The scan reads
the ledger through ``Ledger.recent_events`` (a read-only query);
it never writes to the ledger.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from puspa.sentinel.detectors import run_detectors
from puspa.sentinel.state import (
    prune_seen,
    read_state,
    resolve_issue,
    upsert_issues,
    write_sentinel_files,
)
from puspa.sentinel.thresholds import SentinelThresholds
from puspa.sentinel.verifier import verify_issue

__all__ = ["Sentinel", "default_state_dir", "run_sentinel_scan"]

#: State directory when nothing else is configured.
DEFAULT_STATE_DIR = Path.home() / ".puspa" / "sentinel"


def default_state_dir() -> Path:
    """Sentinel home: ``PUSPA_SENTINEL_HOME`` or ``~/.puspa/sentinel``."""
    override = os.environ.get("PUSPA_SENTINEL_HOME")
    return Path(override) if override else DEFAULT_STATE_DIR


class Sentinel:
    """A periodic scanner over Puspa's own records.

    ``event_source`` is a callable ``() -> list[dict]`` returning
    ledger events (the ``puspa/ledger/ledger.py`` shape). The
    scanner calls it twice per scan at most: once for detection,
    once per candidate through the verifier — and the verifier
    call reuses the same callable, so a slow source is read once
    and cached by passing a closure over the first read.
    ``clock`` is an injectable ``() -> datetime`` for tests.
    """

    def __init__(
        self,
        *,
        event_source,
        state_path: str | Path,
        thresholds: SentinelThresholds | None = None,
        clock=None,
    ) -> None:
        self.event_source = event_source
        self.state_path = Path(state_path)
        self.thresholds = thresholds or SentinelThresholds()
        self.thresholds.validate()
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(UTC)

    def scan(self, *, force: bool = False) -> dict:
        """Run one scan; return the scan report dict.

        The report carries ``status`` (``"scanned"`` / ``"skipped"`` /
        ``"error"``), ``scanned_at``, ``issues_found`` (detector
        candidates), ``confirmed`` / ``stale`` / ``duplicate`` (the
        verifier's verdicts per key), ``added`` / ``updated`` /
        ``auto_resolved`` / ``pruned`` (state changes), and
        ``files`` (the written state paths).
        """
        now = self._now()
        state = read_state(self.state_path)
        last_scan = state.get("last_scan")
        if not force and last_scan:
            try:
                last = datetime.fromisoformat(str(last_scan))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if now - last < timedelta(minutes=self.thresholds.rescan_minutes):
                    return {
                        "status": "skipped",
                        "reason": "rescan interval not reached",
                        "scanned_at": now.isoformat(),
                        "last_scan": str(last_scan),
                        "rescan_minutes": self.thresholds.rescan_minutes,
                    }
            except ValueError:
                pass
        try:
            events = self.event_source()
        except Exception as exc:
            return {
                "status": "error",
                "reason": f"could not read events: {type(exc).__name__}",
                "scanned_at": now.isoformat(),
            }
        if not isinstance(events, list):
            return {
                "status": "error",
                "reason": "event source did not return a list",
                "scanned_at": now.isoformat(),
            }
        # One cached read for the verifier: verification re-reads
        # fresh data by contract, and within a single scan the
        # detection read IS the fresh data.
        cached = list(events)
        candidates = run_detectors(cached, thresholds=self.thresholds, now=now)
        confirmed: list[str] = []
        stale: list[str] = []
        duplicate: list[str] = []
        verifiable = [issue for issue in candidates]
        for issue in verifiable:
            verdict = verify_issue(
                issue,
                event_source=lambda: cached,
                state=state,
                thresholds=self.thresholds,
                now=now,
            )
            if verdict.verdict == "confirmed":
                confirmed.append(verdict.issue_key)
            elif verdict.verdict == "stale":
                stale.append(verdict.issue_key)
            elif verdict.verdict == "duplicate":
                duplicate.append(verdict.issue_key)
            else:
                stale.append(verdict.issue_key)
        confirmed_issues = [issue for issue in candidates if issue.get("key") in confirmed]
        merged = upsert_issues(state, confirmed_issues, now=now)
        auto_resolved: list[str] = []
        # Auto-resolve open issues the fresh scan no longer finds:
        # an open key absent from the confirmed set, whose condition
        # the verifier now calls stale, is resolved, not deleted.
        open_keys = {
            key
            for key, stored in (state.get("issues") or {}).items()
            if isinstance(stored, dict) and stored.get("status") == "open"
        }
        found_keys = {issue.get("key") for issue in candidates}
        for key in sorted(open_keys - found_keys):
            stored = state["issues"][key]
            probe = {
                "key": key,
                "detector": stored.get("detector"),
                "evidence": stored.get("evidence"),
            }
            verdict = verify_issue(
                probe,
                event_source=lambda: cached,
                state=None,
                thresholds=self.thresholds,
                now=now,
            )
            if verdict.verdict == "stale" and resolve_issue(
                state, key, resolution="auto: condition cleared", now=now
            ):
                auto_resolved.append(key)
        pruned = prune_seen(state, now=now, lookback_days=self.thresholds.lookback_days)
        state["last_scan"] = now.isoformat()
        files = write_sentinel_files(state, self.state_path)
        return {
            "status": "scanned",
            "scanned_at": now.isoformat(),
            "issues_found": len(candidates),
            "confirmed": sorted(confirmed),
            "stale": sorted(stale),
            "duplicate": sorted(duplicate),
            "added": merged["added"],
            "updated": merged["updated"],
            "auto_resolved": sorted(auto_resolved),
            "pruned": pruned,
            "files": files,
        }


def run_sentinel_scan(
    *,
    user_id: str | None = None,
    dsn: str | None = None,
    state_dir: str | Path | None = None,
    force: bool = False,
) -> dict:
    """Run one sentinel scan for the scheduler; never raises.

    Reads ledger events from the last ``lookback_days`` via
    ``Ledger.recent_events`` and writes state under
    ``state_dir`` (default ``default_state_dir()``). Returns the
    scanner report with ``"job": "sentinel_scan"`` added, or a
    ``{"job": ..., "status": "error", ...}`` dict on failure.
    ``user_id`` is accepted for the scheduler's job shape and
    recorded in the report; the sentinel scans the whole ledger
    (the ledger has no per-user partition — see
    ``puspa/ledger/ledger.py``).
    """
    from puspa.ledger import Ledger

    thresholds = SentinelThresholds()
    directory = Path(state_dir) if state_dir is not None else default_state_dir()
    state_path = directory / "sentinel-state.json"
    try:
        ledger = Ledger(dsn)
        since = datetime.now(UTC) - timedelta(days=thresholds.lookback_days)
        events = ledger.recent_events(since)
    except Exception as exc:
        return {
            "job": "sentinel_scan",
            "status": "error",
            "reason": f"could not read ledger: {type(exc).__name__}",
        }
    sentinel = Sentinel(
        event_source=lambda: events,
        state_path=state_path,
        thresholds=thresholds,
    )
    report = sentinel.scan(force=force)
    report["job"] = "sentinel_scan"
    report["user_id"] = user_id
    return report
