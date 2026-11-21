"""Emergency stop for desktop hands (V2-35).

One flag that halts the hands immediately, mid-task. The user
sets it from the CLI (``puspa computer stop``), the PWA stop
button, or the ``computer_stop`` MCP tool, and clears it the
same way; a running ``run_task`` stops at the next step
boundary at the latest, and an in-flight single action is
refused once the flag is set.

The flag is a small JSON file under the user's Puspa state dir
(``~/.local/share/puspa/computer/stop.json``), so a stop set in
one process halts a task running in another: every action
re-reads the file before it runs instead of trusting a cached
value. That is also why the stop works with no database and no
desktop session: the flag must flip even when everything else
is down.

This is the computer-scoped stop, not the global agent kill
switch (:class:`puspa.ledger.kill_switch.KillSwitch`): that
one stops the whole agent and is flipped by the operator over
a database connection; this one stops only the desktop hands
and is flipped by the user from the PWA or CLI. The two are
independent on purpose: stopping the hands must never need
the ledger to be up, and must never stop unrelated work.

A file that cannot be read (missing, corrupt) reads as
inactive, except a corrupt file reads as *active*: a stop the
user asked for must never silently clear itself because the
file got mangled. ``clear`` overwrites the file, so a wedged
flag is always recoverable.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["ComputerStop", "ComputerStopped", "default_stop_path"]

#: File name inside the computer state dir.
STOP_FILENAME = "stop.json"


class ComputerStopped(Exception):
    """A computer action was refused because the user stopped
    the hands. Raised instead of running, never after."""


def default_stop_path() -> Path:
    """The stop file, unless ``PUSPA_COMPUTER_STATE_DIR``
    points somewhere else (tests use that to stay hermetic)."""
    state_dir = os.environ.get("PUSPA_COMPUTER_STATE_DIR")
    if state_dir:
        return Path(state_dir) / STOP_FILENAME
    return Path.home() / ".local" / "share" / "puspa" / "computer" / STOP_FILENAME


class ComputerStop:
    """The computer emergency stop flag, backed by one file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_stop_path()

    # -- reads ------------------------------------------------------
    def state(self) -> dict:
        """The flag as a plain dict: active, actor, reason, and
        when it was last flipped (ISO-8601, or None)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"active": False, "actor": "", "reason": "", "updated_at": None}
        except (OSError, ValueError):
            # Fail closed: a flag we cannot read must never read
            # as "off". The user clears it explicitly.
            return {
                "active": True,
                "actor": "",
                "reason": "stop file is unreadable",
                "updated_at": None,
            }
        if not isinstance(data, dict):
            return {
                "active": True,
                "actor": "",
                "reason": "stop file is unreadable",
                "updated_at": None,
            }
        return {
            "active": bool(data.get("active", False)),
            "actor": str(data.get("actor", "") or ""),
            "reason": str(data.get("reason", "") or ""),
            "updated_at": data.get("updated_at"),
        }

    def is_active(self) -> bool:
        """True when the hands are stopped right now."""
        return bool(self.state()["active"])

    def check(self) -> None:
        """Raise :class:`ComputerStopped` when the flag is set."""
        current = self.state()
        if not current["active"]:
            return
        detail = ""
        if current["reason"]:
            detail = f": {current['reason']}"
        elif current["actor"]:
            detail = f" (stopped by {current['actor']})"
        raise ComputerStopped(f"computer hands are stopped by the user{detail}")

    # -- writes -----------------------------------------------------
    def request(self, *, actor: str = "user", reason: str = "") -> dict:
        """Set the flag. Returns the new state."""
        return self._save(True, actor=actor, reason=reason)

    def clear(self, *, actor: str = "user", reason: str = "") -> dict:
        """Clear the flag. Returns the new state."""
        return self._save(False, actor=actor, reason=reason)

    def _save(self, active: bool, *, actor: str, reason: str) -> dict:
        state = {
            "active": active,
            "actor": actor,
            "reason": reason,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        return state
