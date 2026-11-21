"""Per-app allowlist for desktop hands (V2-35).

A NORMAL action (a click, typing, a scroll) inside an
allowlisted app runs without asking; outside one it still asks.
The user allowlists an app once, from the CLI (``puspa computer
allow Notes``), the PWA allowlist panel, or the
``computer_allowlist`` MCP tool, and the agent then clicks
freely inside it until the user revokes it.

The list lives in one small JSON file under the user's Puspa
state dir (``~/.local/share/puspa/computer/allowlist.json``,
the same ``~/.local/share/puspa`` root the backup and policy
modules use), so the CLI, the PWA server, and the MCP host all
read the same list. App names compare case-insensitively and
ignoring surrounding space, so "Notes", "notes", and " Notes "
are the same app; the stored spelling is the first one the
user gave.

The allowlist never covers DESTRUCTIVE actions: a clipboard
write, a drag, or closing a window always asks, even inside an
allowlisted app (see :mod:`puspa.computer.tiers`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = ["Allowlist", "default_allowlist_path"]

#: File name inside the computer state dir.
ALLOWLIST_FILENAME = "allowlist.json"


def default_allowlist_path() -> Path:
    """The allowlist file, unless ``PUSPA_COMPUTER_STATE_DIR``
    points somewhere else (tests use that to stay hermetic)."""
    state_dir = os.environ.get("PUSPA_COMPUTER_STATE_DIR")
    if state_dir:
        return Path(state_dir) / ALLOWLIST_FILENAME
    return Path.home() / ".local" / "share" / "puspa" / "computer" / ALLOWLIST_FILENAME


def _normalize(app: str) -> str:
    return app.strip().casefold()


class Allowlist:
    """The set of apps the agent may click inside without asking."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_allowlist_path()

    # -- reads ------------------------------------------------------
    def apps(self) -> list[str]:
        """Allowlisted app names, in the order they were added."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
            return []
        seen: set[str] = set()
        out: list[str] = []
        for app in data["apps"]:
            if not isinstance(app, str) or not app.strip():
                continue
            if _normalize(app) in seen:
                continue
            seen.add(_normalize(app))
            out.append(app.strip())
        return out

    def is_allowed(self, app: str | None) -> bool:
        """True when ``app`` is allowlisted. No app name (a
        visual or web click, which cannot say which app is on
        screen) is never allowlisted: it always asks."""
        if app is None or not app.strip():
            return False
        return _normalize(app) in {_normalize(name) for name in self.apps()}

    # -- writes -----------------------------------------------------
    def allow(self, app: str) -> bool:
        """Add ``app``. Returns True when it was new, False when
        it was already allowlisted. Raises ValueError on an empty
        name."""
        if not app.strip():
            raise ValueError("allowlist needs a non-empty app name")
        names = self.apps()
        if _normalize(app) in {_normalize(name) for name in names}:
            return False
        names.append(app.strip())
        self._save(names)
        return True

    def revoke(self, app: str) -> bool:
        """Remove ``app``. Returns True when it was listed, False
        when it was not."""
        names = self.apps()
        kept = [name for name in names if _normalize(name) != _normalize(app)]
        if len(kept) == len(names):
            return False
        self._save(kept)
        return True

    def _save(self, names: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"apps": names}, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
