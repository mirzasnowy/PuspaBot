"""Decision log (G12).

The DuskBot ``decision-log.ts`` pattern, rewritten for Puspa:
every decision is recorded — actor, source, via — into a daily
file, with 180-day retention by default.

One JSON object per line in ``decisions-YYYY-MM-DD.jsonl`` under
the log directory::

    {
      "date": "2026-11-18",
      "recorded_at": "<ISO-8601 UTC>",
      "actor": "sentinel",
      "source": "scan",
      "via": "verifier",
      "decision": "auto_resolve",
      "details": {"issue_key": "cost:task-1", "reason": "..."},
      "entry_hash": "<sha256>"
    }

``entry_hash`` is the SHA-256 of the canonical JSON of
``{actor, source, via, decision, details}`` — the timestamp is
not hashed, so the same decision always hashes the same and
can be audited by hash, the way G9's ``puspa.decider.log``
hashes its decisions (this log is the durable, file-based
counterpart of that in-memory log; it does not write to the
ledger, so it needs no new ledger event type).

Retention: ``prune`` deletes daily files older than
``retention_days``. Files are append-only; a decision is never
edited or deleted except by retention pruning.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

__all__ = ["DailyDecisionLog"]

#: Default retention, DuskBot's default: 180 days.
DEFAULT_RETENTION_DAYS = 180


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _entry_hash(*, actor: str, source: str, via: str, decision: str, details: dict) -> str:
    payload = {
        "actor": actor,
        "source": source,
        "via": via,
        "decision": decision,
        "details": details,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


class DailyDecisionLog:
    """Append-only daily decision files with retention pruning."""

    def __init__(self, directory: str | Path, *, retention_days: int = DEFAULT_RETENTION_DAYS):
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        self.directory = Path(directory)
        self.retention_days = retention_days

    def _path_for(self, day: date) -> Path:
        return self.directory / f"decisions-{day.isoformat()}.jsonl"

    def record(
        self,
        *,
        actor: str,
        source: str,
        via: str,
        decision: str,
        details: dict | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Append one decision; return the stored entry dict.

        ``actor`` is who decided (``"sentinel"``, ``"operator"``,
        a user id); ``source`` is where the decision came from
        (``"scan"``, ``"verifier"``, ``"api"``); ``via`` is the
        mechanism (``"auto"``, ``"manual"``, a backend name).
        """
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        details = dict(details or {})
        entry = {
            "date": now.date().isoformat(),
            "recorded_at": now.isoformat(),
            "actor": actor,
            "source": source,
            "via": via,
            "decision": decision,
            "details": details,
            "entry_hash": _entry_hash(
                actor=actor, source=source, via=via, decision=decision, details=details
            ),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path_for(now.date())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(entry) + "\n")
        return entry

    def read_day(self, day: date) -> list[dict]:
        """All entries for one day, in file order; [] when absent."""
        path = self._path_for(day)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        entries = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def read_range(self, start: date, end: date) -> list[dict]:
        """All entries from ``start`` to ``end`` inclusive, in order."""
        if end < start:
            raise ValueError("end must not be before start")
        entries: list[dict] = []
        day = start
        while day <= end:
            entries.extend(self.read_day(day))
            day += timedelta(days=1)
        return entries

    def prune(self, *, now: datetime | None = None) -> list[str]:
        """Delete daily files older than the retention; return names.

        Only files matching the ``decisions-YYYY-MM-DD.jsonl``
        name are touched; anything else in the directory is left
        alone.
        """
        now = now or datetime.now(UTC)
        cutoff = (now.date() - timedelta(days=self.retention_days)).isoformat()
        deleted: list[str] = []
        if not self.directory.is_dir():
            return deleted
        for path in sorted(self.directory.glob("decisions-*.jsonl")):
            stem = path.name[len("decisions-") : -len(".jsonl")]
            try:
                day = date.fromisoformat(stem)
            except ValueError:
                continue
            if day.isoformat() < cutoff:
                path.unlink()
                deleted.append(path.name)
        return deleted
