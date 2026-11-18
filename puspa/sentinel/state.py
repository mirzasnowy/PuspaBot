"""Sentinel state file (G12).

The DuskBot sentinel's state-file functions, rewritten for Puspa:
``read_state`` / ``read_issues`` / ``mark_counted`` /
``upsert_issues`` / ``prune_seen`` / ``write_sentinel_files``.

The state file is one JSON document::

    {
      "version": 1,
      "last_scan": "<ISO-8601 UTC or null>",
      "issues": {
        "<key>": {
          ...issue fields from the detector...,
          "status": "open" | "acknowledged" | "resolved",
          "count": <times seen across scans>,
          "first_seen": "<ISO-8601 UTC>",
          "last_seen": "<ISO-8601 UTC>",
          "resolution": "<why it resolved, when auto-resolved>"
        }
      }
    }

Rules:

- A new issue enters as ``open`` with ``count`` 1.
- An issue seen again keeps its ``first_seen`` and status, bumps
  ``count``, and moves ``last_seen`` forward.
- An acknowledged or resolved issue is never re-opened by a scan;
  the operator's decision stands until they change it.
- ``prune_seen`` drops open issues not seen for longer than the
  lookback window (the condition is gone and nobody acted on it)
  and resolved issues older than twice the window. Acknowledged
  issues are never pruned: an operator said "I know", and only
  the operator may clear that.
- Writes are atomic (temp file in the same directory +
  ``os.replace``), so a crash mid-write never leaves a torn state.
  A missing or corrupt file reads as fresh state — the scanner
  rebuilds it from the ledger on the next scan — never raises.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

__all__ = [
    "STATE_VERSION",
    "acknowledge_issue",
    "mark_counted",
    "new_state",
    "prune_seen",
    "read_issues",
    "read_state",
    "resolve_issue",
    "upsert_issues",
    "write_sentinel_files",
]

#: State-file format version.
STATE_VERSION = 1

#: Open issues unseen for longer than this multiple of the lookback
#: are pruned; resolved issues get twice that long.
PRUNE_OPEN_MULTIPLE = 1
PRUNE_RESOLVED_MULTIPLE = 2


def new_state() -> dict:
    """A fresh, empty state document."""
    return {"version": STATE_VERSION, "last_scan": None, "issues": {}}


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def read_state(path: str | Path) -> dict:
    """Read the state file; missing or corrupt reads as fresh state.

    A corrupt file is never deleted here: the next successful scan
    overwrites it atomically through ``write_sentinel_files``.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return new_state()
    except OSError:
        return new_state()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return new_state()
    if not isinstance(data, dict) or not isinstance(data.get("issues"), dict):
        return new_state()
    state = new_state()
    state["last_scan"] = data.get("last_scan")
    for key, issue in data["issues"].items():
        if isinstance(issue, dict) and isinstance(key, str):
            state["issues"][key] = dict(issue)
    return state


def read_issues(state: dict, *, status: str | None = None) -> list[dict]:
    """Issues in the state, optionally filtered by status, key order."""
    issues = state.get("issues") or {}
    found = [dict(issue) for key, issue in sorted(issues.items()) if isinstance(issue, dict)]
    if status is not None:
        found = [issue for issue in found if issue.get("status") == status]
    return found


def mark_counted(state: dict, key: str, *, now: datetime | None = None) -> dict | None:
    """Bump ``count`` and ``last_seen`` for one issue; None if unknown.

    Returns the updated issue dict, or None when the key is not in
    the state. Used by the scanner for issues seen again.
    """
    issues = state.setdefault("issues", {})
    issue = issues.get(key)
    if not isinstance(issue, dict):
        return None
    now = _as_utc(now or datetime.now(UTC))
    issue["count"] = int(issue.get("count") or 0) + 1
    issue["last_seen"] = now.isoformat()
    return dict(issue)


def upsert_issues(state: dict, issues: list[dict], *, now: datetime | None = None) -> dict:
    """Merge freshly detected issues into the state.

    Returns ``{"added": [...keys], "updated": [...keys],
    "skipped": [...keys]}``: ``added`` are new open issues,
    ``updated`` are open issues seen again (count bumped),
    ``skipped`` are acknowledged/resolved issues the scan found
    again — the operator's status stands and only ``last_seen``
    moves.
    """
    now = _as_utc(now or datetime.now(UTC))
    stored = state.setdefault("issues", {})
    outcome: dict[str, list[str]] = {"added": [], "updated": [], "skipped": []}
    for issue in issues:
        key = str(issue.get("key") or "")
        if not key:
            continue
        existing = stored.get(key)
        if existing is None:
            stored[key] = {
                **{k: v for k, v in issue.items()},
                "status": "open",
                "count": 1,
            }
            outcome["added"].append(key)
        elif existing.get("status") in ("acknowledged", "resolved"):
            existing["last_seen"] = issue.get("last_seen") or now.isoformat()
            outcome["skipped"].append(key)
        else:
            existing.update(
                {k: v for k, v in issue.items() if k not in ("first_seen", "status", "count")}
            )
            existing["count"] = int(existing.get("count") or 0) + 1
            existing["last_seen"] = issue.get("last_seen") or now.isoformat()
            outcome["updated"].append(key)
    return outcome


def acknowledge_issue(state: dict, key: str, *, now: datetime | None = None) -> bool:
    """Mark an issue acknowledged by the operator. False if unknown."""
    issue = (state.get("issues") or {}).get(key)
    if not isinstance(issue, dict):
        return False
    issue["status"] = "acknowledged"
    issue["last_seen"] = _as_utc(now or datetime.now(UTC)).isoformat()
    return True


def resolve_issue(
    state: dict, key: str, *, resolution: str = "", now: datetime | None = None
) -> bool:
    """Mark an issue resolved. False if unknown."""
    issue = (state.get("issues") or {}).get(key)
    if not isinstance(issue, dict):
        return False
    issue["status"] = "resolved"
    issue["resolution"] = resolution
    issue["last_seen"] = _as_utc(now or datetime.now(UTC)).isoformat()
    return True


def prune_seen(state: dict, *, now: datetime | None = None, lookback_days: int = 7) -> list[str]:
    """Drop stale issues from the state; return the pruned keys.

    Open issues unseen for longer than the lookback window go
    (the condition is gone and nobody acted on it); resolved
    issues get twice the window; acknowledged issues are never
    pruned — only the operator clears them.
    """
    now = _as_utc(now or datetime.now(UTC))
    stored = state.get("issues") or {}
    pruned: list[str] = []
    for key in sorted(stored):
        issue = stored[key]
        if not isinstance(issue, dict):
            pruned.append(key)
            continue
        status = issue.get("status")
        if status == "acknowledged":
            continue
        multiple = PRUNE_RESOLVED_MULTIPLE if status == "resolved" else PRUNE_OPEN_MULTIPLE
        last_seen = issue.get("last_seen")
        try:
            seen_at = datetime.fromisoformat(str(last_seen))
        except ValueError:
            pruned.append(key)
            continue
        if now - _as_utc(seen_at) > timedelta(days=lookback_days * multiple):
            pruned.append(key)
    for key in pruned:
        stored.pop(key, None)
    return pruned


def write_sentinel_files(state: dict, path: str | Path) -> dict:
    """Write the state file atomically, plus an open-issues snapshot.

    Writes ``<path>`` (the full state, temp file + ``os.replace``)
    and ``<stem>-issues.json`` next to it (the open issues only,
    for humans and dashboards that do not want the whole state).
    Returns ``{"state": <path>, "issues": <path>}`` as strings.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    issues_path = path.with_name(f"{path.stem}-issues.json")
    open_issues = read_issues(state, status="open")
    issues_payload = json.dumps(open_issues, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp_issues = issues_path.with_suffix(issues_path.suffix + ".tmp")
    tmp_issues.write_text(issues_payload, encoding="utf-8")
    os.replace(tmp_issues, issues_path)
    return {"state": str(path), "issues": str(issues_path)}
