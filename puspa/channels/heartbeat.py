"""Gateway heartbeat + polling-offset restore (G14).

The gateway records a heartbeat (the last time it was alive and
processing) and the last polling offset (the next ``getUpdates``
offset on Telegram). On start, the gateway restores the offset
from disk so it neither reprocesses a flood of old messages nor
loses messages that arrived while it was down.

State lives in one small JSON file per profile
(``<state_dir>/<profile>.json``) — no database, no new table: the
heartbeat is gateway-liveness state, not user data. Writes are
atomic (write to a temp file, then rename). A missing or corrupt
file restores to "no offset" (poll from the beginning, with the
gateway's ``drop_backlog`` option deciding whether to skip it).
The clock is injectable so tests can move time without sleeping.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Heartbeat", "HeartbeatStore"]


@dataclass
class Heartbeat:
    """One heartbeat snapshot: liveness + polling position."""

    last_beat_at: float = 0.0
    last_offset: int | None = None
    profile: str = "default"

    def as_dict(self) -> dict:
        return {
            "profile": self.profile,
            "last_beat_at": self.last_beat_at,
            "last_offset": self.last_offset,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Heartbeat:
        return cls(
            profile=str(data.get("profile", "default")),
            last_beat_at=float(data.get("last_beat_at", 0.0) or 0.0),
            last_offset=data.get("last_offset"),
        )


@dataclass
class HeartbeatStore:
    """Load/save heartbeat state for one profile (JSON file)."""

    profile: str = "default"
    state_dir: str | Path = ".puspa-channels"
    clock: Callable[[], float] = field(default=time.time, repr=False)

    @property
    def path(self) -> Path:
        return Path(self.state_dir) / f"{self.profile}.json"

    def load(self) -> Heartbeat:
        """Restore the last saved heartbeat; empty state when absent/corrupt."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Heartbeat(profile=self.profile)
        if not isinstance(data, dict):
            return Heartbeat(profile=self.profile)
        restored = Heartbeat.from_dict(data)
        restored.profile = self.profile
        return restored

    def save(self, heartbeat: Heartbeat) -> Path:
        """Atomically persist a heartbeat snapshot."""
        directory = Path(self.state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = self.path
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".heartbeat-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(heartbeat.as_dict(), handle, sort_keys=True)
            os.replace(tmp_name, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
        return target

    def beat(self, *, last_offset: int | None = None) -> Heartbeat:
        """Record a heartbeat now, keeping the previous offset unless given."""
        current = self.load()
        heartbeat = Heartbeat(
            profile=self.profile,
            last_beat_at=float(self.clock()),
            last_offset=last_offset if last_offset is not None else current.last_offset,
        )
        self.save(heartbeat)
        return heartbeat

    def age_seconds(self) -> float | None:
        """Seconds since the last recorded beat, or None when never beaten."""
        current = self.load()
        if current.last_beat_at <= 0:
            return None
        return max(0.0, float(self.clock()) - current.last_beat_at)
