"""Append-only change journal with undo (G7, DuskBot pattern).

Every memory change is logged as an entry carrying the ``before``
snapshot, so any change can be undone without ever deleting history:
undo appends a compensating entry and restores the previous state
through the fact store. The journal itself has no delete or edit
operation. Entries can also be persisted as JSONL (one JSON object
per line) when a ``path`` is given; the in-memory list is always the
live view.

Quarantined / blocked lines from the trust gate are journal entries
too (kind ``"quarantined"`` / ``"blocked"``), so the user can review
them with :meth:`Journal.list_quarantined`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc)


class Journal:
    """Append-only log of memory changes."""

    def __init__(self, path=None):
        self._entries = []
        self._seq = 0
        self.path = Path(path) if path is not None else None
        if self.path is not None and self.path.exists():
            self._load()

    def _load(self):
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            self._entries.append(entry)
            self._seq = max(self._seq, int(entry["seq"]))

    def _persist(self, entry):
        if self.path is None:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")

    def log(self, action, fact_id=None, before=None, after=None,
            actor="system", kind="change", note=None, origin=None):
        """Append one entry and return it.

        ``before``/``after`` are fact-record dicts (or None). ``kind``
        is ``"change"``, ``"quarantined"`` or ``"blocked"``.
        """
        self._seq += 1
        entry = {
            "seq": self._seq,
            "ts": _now().isoformat(),
            "action": action,
            "fact_id": fact_id,
            "before": before,
            "after": after,
            "actor": actor,
            "kind": kind,
            "note": note,
            "origin": origin,
        }
        self._entries.append(entry)
        self._persist(entry)
        return entry

    def entries(self):
        """All entries, oldest first. Returns a copy."""
        return list(self._entries)

    def list_quarantined(self):
        """Quarantined / blocked entries awaiting user review."""
        return [e for e in self._entries
                if e["kind"] in ("quarantined", "blocked")]

    def undo_last(self, store):
        """Undo the most recent ``"change"`` entry via ``store``.

        The store must implement ``apply_snapshot(snapshot)`` (see
        ``puspa/memory/facts.py``). Undo never deletes history: when
        the change created a fact (``before`` is None) the fact is
        expired, not removed; otherwise the ``before`` snapshot is
        restored. A compensating ``"undo"`` entry is appended.
        Returns the compensating entry, or None when there is
        nothing to undo.
        """
        for entry in reversed(self._entries):
            if entry["kind"] == "change" and entry["action"] != "undo":
                target = entry
                break
        else:
            return None
        before = target["before"]
        if before is None:
            after = target["after"]
            if after is not None:
                store.expire_snapshot(after)
            restored = None
        else:
            restored = store.apply_snapshot(before)
        undo_entry = self.log(
            "undo", fact_id=target["fact_id"], before=target["after"],
            after=restored, actor="system", kind="change",
            note=f"undo of seq {target['seq']} ({target['action']})")
        return undo_entry
