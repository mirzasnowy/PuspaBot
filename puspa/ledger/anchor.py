"""Daily anchoring of hash-chain heads outside the agent (E-11, plan section 6.6).

K12 made the audit log append-only under an INSERT-only database
role, but the log still lives in one database: an attacker who owns
that database could rewrite history and recompute the chain, and
nothing outside the database would notice. Anchoring closes that
gap: once a day, the head hash of every task chain (plus the event
count behind it) is copied to an external location — an object-lock
bucket or a second VPS — as one write-once record per day. A rewrite
of the past then shows up as a mismatch between the stored anchor
and the heads recomputed from the live log.

What this module provides:

- :func:`collect_heads`: the head hash + event count of every task
  chain as of a cutoff instant, read from the ledger.
- :class:`AnchorRecord`: one day's anchor (date, heads, digest,
  optional Ed25519 signature over the digest).
- :class:`LocalAnchorStore`: a write-once store on the local
  filesystem — one JSON file per day, created with exclusive
  create, so overwriting an anchor is refused instead of silently
  replacing history. This is the stand-in for the external target:
  the live external anchor (object-lock bucket / second VPS) is
  E-02 work; any store with write-once ``put``/``get`` semantics
  drops into :func:`anchor_day` / :func:`verify_anchor` unchanged.
- :func:`anchor_day` / :func:`verify_anchor`: write one day's
  anchor and re-verify it later against the live log.

Verification is reproducible: heads are collected "as of end of
day", so later appends never change an old day's anchor, and a
recomputed anchor for the same day must match the stored one
exactly.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

__all__ = [
    "AnchorConflict",
    "AnchorError",
    "AnchorRecord",
    "AnchorStore",
    "LocalAnchorStore",
    "anchor_day",
    "collect_heads",
    "compute_digest",
    "verify_anchor",
]

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def _canonical_json(obj) -> bytes:
    """Canonical JSON: sorted keys, no spaces, UTF-8.

    Local copy of the rule in ``puspa.policy.tokens`` / ``puspa.ledger``,
    kept here so this module never imports the policy package.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


class AnchorError(Exception):
    """An anchor could not be written, read, or verified."""


class AnchorConflict(AnchorError):
    """An anchor already exists for this day with different content.

    Re-anchoring the same day with identical content is fine
    (idempotent); a different record for the same day means someone
    tried to replace history, and the write is refused.
    """


def _day_end(day: date) -> datetime:
    """End of ``day`` (exclusive): midnight UTC of the next day."""
    return datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=UTC)


def collect_heads(dsn: str | None, *, before: datetime) -> dict[str, dict[str, Any]]:
    """Head hash + event count of every task chain before ``before``.

    One row per task that has at least one event older than the
    cutoff: the latest event's hash (the chain head as of the
    cutoff), how many events the chain holds up to the cutoff, and
    the head event's recorded time. Read-only; runs on any
    connection that can SELECT the events table.
    """
    if before.tzinfo is None:
        raise AnchorError("before must be a timezone-aware datetime")
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (task_id) task_id, hash, created_at "
            "FROM events WHERE created_at < %s "
            "ORDER BY task_id, created_at DESC, id DESC",
            (before,),
        )
        heads = {row["task_id"]: dict(row) for row in cur.fetchall()}
        cur.execute(
            "SELECT task_id, COUNT(*) AS event_count FROM events "
            "WHERE created_at < %s GROUP BY task_id",
            (before,),
        )
        counts = {row["task_id"]: row["event_count"] for row in cur.fetchall()}
    out: dict[str, dict[str, Any]] = {}
    for task_id, head in heads.items():
        out[task_id] = {
            "head": head["hash"],
            "event_count": counts.get(task_id, 0),
            "head_at": head["created_at"].astimezone(UTC).isoformat(),
        }
    return out


def compute_digest(heads: dict[str, dict[str, Any]]) -> str:
    """One digest over a day's heads: sha256 of the canonical JSON
    of ``{task_id: head hash}``. Any rewritten past changes it."""
    slim = {task_id: info["head"] for task_id, info in sorted(heads.items())}
    return hashlib.sha256(_canonical_json(slim)).hexdigest()


def _signing_payload(*, day: str, digest: str, heads: dict[str, dict[str, Any]]) -> bytes:
    return _canonical_json({"date": day, "digest": digest, "heads": heads})


@dataclass(frozen=True)
class AnchorRecord:
    """One day's anchor: every chain head, one digest, one signature."""

    date: str  # YYYY-MM-DD
    heads: dict[str, dict[str, Any]]
    digest: str
    created_at: str  # ISO-8601 UTC, when the anchor was written
    signature: str = ""  # base64url Ed25519 signature over date+digest+heads
    signer: str = ""  # who/what signed (key id or operator name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "heads": self.heads,
            "digest": self.digest,
            "created_at": self.created_at,
            "signature": self.signature,
            "signer": self.signer,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnchorRecord:
        return cls(
            date=data["date"],
            heads=data["heads"],
            digest=data["digest"],
            created_at=data["created_at"],
            signature=data.get("signature", ""),
            signer=data.get("signer", ""),
        )


class AnchorStore(Protocol):
    """Write-once anchor storage: ``put`` refuses to overwrite."""

    def put(self, record: AnchorRecord) -> AnchorRecord: ...
    def get(self, day: date) -> AnchorRecord | None: ...
    def dates(self) -> list[str]: ...


class LocalAnchorStore:
    """Anchor storage on the local filesystem (the stand-in for the
    external target until the E-02 bucket / second VPS lands).

    One JSON file per day (``anchor-YYYY-MM-DD.json``) in
    ``directory``. Writes use exclusive create, so an anchor file
    can never be overwritten in place: ``put`` with identical
    content returns the stored record (re-anchoring is idempotent);
    ``put`` with different content raises :class:`AnchorConflict`.
    The directory itself must be private to the anchor writer
    (mode 0700 is set on creation when the directory is new).
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Best effort on filesystems without chmod.
        with suppress(OSError):
            os.chmod(self.directory, 0o700)

    def _path(self, day: date) -> Path:
        return self.directory / f"anchor-{day.isoformat()}.json"

    def put(self, record: AnchorRecord) -> AnchorRecord:
        """Store one anchor, write-once. See the class docstring."""
        day = date.fromisoformat(record.date)
        path = self._path(day)
        text = json.dumps(record.to_dict(), sort_keys=True, indent=2, ensure_ascii=False)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except FileExistsError:
            stored = self.get(day)
            if stored is not None and stored.to_dict() == record.to_dict():
                return stored
            raise AnchorConflict(
                f"anchor for {record.date} already exists with different content; "
                "refusing to overwrite history"
            ) from None
        return record

    def get(self, day: date) -> AnchorRecord | None:
        """The stored anchor for one day, or None when absent."""
        path = self._path(day)
        if not path.exists():
            return None
        try:
            return AnchorRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise AnchorError(f"stored anchor for {day.isoformat()} is unreadable: {exc}") from exc

    def dates(self) -> list[str]:
        """ISO dates of all stored anchors, oldest first."""
        found = []
        for path in sorted(self.directory.glob("anchor-*.json")):
            name = path.name[len("anchor-") : -len(".json")]
            try:
                date.fromisoformat(name)
            except ValueError:
                continue
            found.append(name)
        return found


def anchor_day(
    dsn: str | None,
    store: AnchorStore,
    *,
    day: date | None = None,
    signing_key=None,
    signer: str = "",
) -> AnchorRecord:
    """Anchor one day's chain heads into ``store``.

    Collects every task head as of end of ``day`` (today UTC when
    omitted), digests them, optionally signs the digest with an
    Ed25519 ``signing_key``, and stores the record write-once.
    Returns the stored record.
    """
    day = day or datetime.now(UTC).date()
    heads = collect_heads(dsn, before=_day_end(day))
    digest = compute_digest(heads)
    existing = store.get(day)
    if existing is not None and existing.digest == digest and existing.heads == heads:
        # Re-anchoring an unchanged day is a no-op: the stored
        # record (with its original timestamp) stands.
        return existing
    signature = ""
    if signing_key is not None:
        import base64

        raw_signature = signing_key.sign(
            _signing_payload(day=day.isoformat(), digest=digest, heads=heads)
        )
        signature = base64.urlsafe_b64encode(raw_signature).rstrip(b"=").decode("ascii")
    record = AnchorRecord(
        date=day.isoformat(),
        heads=heads,
        digest=digest,
        created_at=datetime.now(UTC).isoformat(),
        signature=signature,
        signer=signer,
    )
    return store.put(record)


def verify_anchor(
    dsn: str | None,
    store: AnchorStore,
    *,
    day: date,
    public_key=None,
) -> tuple[bool, list[str]]:
    """Re-verify one day's anchor against the live ledger.

    Recomputes the heads as of end of ``day`` and compares the
    digest and every per-task head and count with the stored
    record. When the stored record carries a signature and
    ``public_key`` is given, the signature is checked too.
    Returns ``(True, [])`` when everything matches, otherwise
    ``(False, [human-readable problems])``.
    """
    stored = store.get(day)
    if stored is None:
        return (False, [f"no anchor stored for {day.isoformat()}"])
    problems: list[str] = []
    heads = collect_heads(dsn, before=_day_end(day))
    digest = compute_digest(heads)
    if digest != stored.digest:
        problems.append(
            f"digest mismatch for {day.isoformat()}: stored {stored.digest[:12]}..., "
            f"recomputed {digest[:12]}..."
        )
    for task_id, info in heads.items():
        old = stored.heads.get(task_id)
        if old is None:
            problems.append(f"task {task_id!r} has events but no anchor entry")
        elif old["head"] != info["head"]:
            problems.append(
                f"task {task_id!r} head changed: anchored {old['head'][:12]}..., "
                f"live {info['head'][:12]}..."
            )
        elif old["event_count"] != info["event_count"]:
            problems.append(
                f"task {task_id!r} event count changed: anchored "
                f"{old['event_count']}, live {info['event_count']}"
            )
    for task_id in stored.heads:
        if task_id not in heads:
            problems.append(f"task {task_id!r} is anchored but has no live events")
    if stored.signature and public_key is not None:
        import base64

        from cryptography.exceptions import InvalidSignature

        try:
            raw_signature = base64.urlsafe_b64decode(
                stored.signature + "=" * (-len(stored.signature) % 4)
            )
            public_key.verify(
                raw_signature,
                _signing_payload(day=stored.date, digest=stored.digest, heads=stored.heads),
            )
        except (InvalidSignature, ValueError) as exc:
            problems.append(f"anchor signature does not verify: {exc}")
    return (not problems, problems)


def main(argv: list[str] | None = None) -> int:
    """CLI: anchor today (or --day) into --dir, or --verify it.

    Example::

        python -m puspa.ledger.anchor --dir /var/lib/puspa/anchors
        python -m puspa.ledger.anchor --dir /var/lib/puspa/anchors --verify --day 2026-11-20
    """
    import argparse

    parser = argparse.ArgumentParser(description="Anchor daily ledger chain heads.")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: PUSPA_TEST_DSN)")
    parser.add_argument("--dir", required=True, help="anchor store directory")
    parser.add_argument("--day", default=None, help="YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--verify", action="store_true", help="verify instead of anchoring")
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.day) if args.day else datetime.now(UTC).date()
    store = LocalAnchorStore(args.dir)
    if args.verify:
        ok, problems = verify_anchor(args.dsn, store, day=day)
        if ok:
            print(f"anchor {day.isoformat()} verifies against the live ledger")
            return 0
        for problem in problems:
            print(f"ANCHOR PROBLEM: {problem}")
        return 1
    record = anchor_day(args.dsn, store, day=day)
    print(f"anchored {record.date}: {len(record.heads)} task heads, digest {record.digest[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
