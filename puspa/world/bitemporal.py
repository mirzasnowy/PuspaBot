"""Bitemporal belief state (Kosmos K3).

Every belief carries two time pairs instead of one timestamp
(``docs/kosmos-design.md`` section C point 3: "Bitemporal state:
what is true, and when the system learned it"):

- **Valid time** (``valid_from`` / ``valid_to``): when the fact
  was true in the world. A price of Rp10.000 valid from Monday
  until Thursday is one interval; the price that replaced it
  starts a new interval.
- **Known time** (``known_from`` / ``known_to``): when the system
  believed it. A fact learned on Friday about Monday is known
  from Friday, even though it was valid from Monday.

Both intervals are half-open: ``[from, to)``. A ``None`` end
means "still open": the fact is still true (valid) or still
believed (known). Every key has exactly one open version at any
moment — the current belief — and any number of closed ones.

Corrections never update a row in place. A correction appends a
new version and closes the old version's ``known_to`` at the
moment of correction; the old value stays readable forever
through the known-time query. When the fact itself stops being
true (the world changed, e.g. the price really did rise), the
caller also passes ``end_previous_valid_at`` to close the old
version's ``valid_to``. When the old belief was simply wrong
about the past (a late-arriving truth), the old ``valid_to``
stays open: the old row still says what the system believed was
true then, and the new row carries the corrected valid interval.
The two cases are different claims and must not be conflated.

As-of queries:

- ``known_as_of(key, at)``: what the system believed at ``at``.
  Asking about yesterday returns yesterday's belief, even after
  a correction today.
- ``valid_as_of(key, at)``: what is true at ``at`` according to
  current knowledge. Asking about last Monday returns the fact
  that was true then, as the system understands it now.
- ``as_of(key, valid_at=..., known_at=...)``: both at once — the
  version that was valid then *and* known then.

Times must be timezone-aware datetimes; a naive datetime is
rejected (fail-closed: a naive time silently read as the wrong
timezone would corrupt history). A new belief's valid time
defaults to its known time when the caller does not know better,
so existing callers that only know "now" keep working unchanged.

This module is pure Python (no database, no network). Durable
storage lives in ``puspa.world.store`` (table
``belief_versions``) and in ``infra/migrate_bitemporal.sql``.
There is deliberately no delete API: history is append-only,
and erasing it is a separate (K12 / data-deletion) concern.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime

__all__ = [
    "BeliefVersion",
    "BitemporalBeliefStore",
    "belief_key",
    "utcnow",
]


def utcnow() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(UTC)


def belief_key(kind: str, name: str) -> str:
    """One belief identity: ``"<kind>:<name>"``.

    Examples: ``belief_key("price", "P1@S1")``,
    ``belief_key("bill", "PLN-123")``. Both parts must be
    non-empty; the key is opaque to this module beyond that.
    """
    if not kind or not name:
        raise ValueError("belief key kind and name must both be non-empty")
    return f"{kind}:{name}"


def _require_aware(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime, got {value!r}")
    return value


def _require_jsonable(value: dict, name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a dict, got {type(value).__name__}")
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-serializable: {exc}") from exc


def _parse_time(raw: str | None, name: str) -> datetime | None:
    if raw is None:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must carry a timezone, got {raw!r}")
    return parsed


@dataclass(frozen=True)
class BeliefVersion:
    """One version of one belief: one value over two intervals.

    ``version`` counts from 1 per key. ``supersedes`` is the
    version number of the version this one replaced, or None for
    the first version of a key. ``value`` is a plain JSON-able
    dict (the belief payload, e.g. ``{"price": 10000}``).
    """

    key: str
    value: dict
    version: int
    valid_from: datetime
    valid_to: datetime | None
    known_from: datetime
    known_to: datetime | None
    supersedes: int | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("belief key must be non-empty")
        if self.version < 1:
            raise ValueError(f"version must count from 1, got {self.version}")
        _require_aware(self.valid_from, "valid_from")
        _require_aware(self.valid_to, "valid_to")
        _require_aware(self.known_from, "known_from")
        _require_aware(self.known_to, "known_to")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError(
                f"valid_to ({self.valid_to}) must be after valid_from ({self.valid_from})"
            )
        if self.known_to is not None and self.known_to <= self.known_from:
            raise ValueError(
                f"known_to ({self.known_to}) must be after known_from ({self.known_from})"
            )
        _require_jsonable(self.value, "value")

    @property
    def is_current(self) -> bool:
        """True when this version is still believed (open known time)."""
        return self.known_to is None

    def is_known_at(self, at: datetime) -> bool:
        """True when this version was the believed one at ``at``."""
        _require_aware(at, "at")
        return self.known_from <= at and (self.known_to is None or at < self.known_to)

    def is_valid_at(self, at: datetime) -> bool:
        """True when this version claims the fact was true at ``at``."""
        _require_aware(at, "at")
        return self.valid_from <= at and (self.valid_to is None or at < self.valid_to)

    def to_dict(self) -> dict:
        """JSON-safe dict (times as ISO-8601 strings)."""
        return {
            "key": self.key,
            "value": self.value,
            "version": self.version,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "known_from": self.known_from.isoformat(),
            "known_to": self.known_to.isoformat() if self.known_to else None,
            "supersedes": self.supersedes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BeliefVersion:
        """Rebuild one version; raises ValueError on bad data."""
        return cls(
            key=data["key"],
            value=data["value"],
            version=int(data["version"]),
            valid_from=_parse_time(data["valid_from"], "valid_from"),  # type: ignore[arg-type]
            valid_to=_parse_time(data.get("valid_to"), "valid_to"),
            known_from=_parse_time(data["known_from"], "known_from"),  # type: ignore[arg-type]
            known_to=_parse_time(data.get("known_to"), "known_to"),
            supersedes=data.get("supersedes"),
        )


class BitemporalBeliefStore:
    """Versioned beliefs: append-only, corrected never overwritten.

    One store holds every key's full history in memory. Invariants
    (checked on every write and on load): versions per key count
    from 1 with no gaps, known intervals are contiguous (each
    version's ``known_from`` is the previous version's
    ``known_to``), and exactly one version per key is current
    (open known time).
    """

    def __init__(self) -> None:
        self._versions: dict[str, list[BeliefVersion]] = {}

    # -- writes --------------------------------------------------------
    def record(
        self,
        key: str,
        value: dict,
        *,
        known_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> BeliefVersion:
        """Record the first version of a belief.

        ``known_at`` defaults to now (UTC). ``valid_from``
        defaults to ``known_at``: when the caller does not know
        when the fact became true, "since we learned it" is the
        honest default. Raises ValueError when the key already
        has a version (use :meth:`correct`) or the times are
        inconsistent.
        """
        if key in self._versions:
            raise ValueError(f"belief {key!r} already exists; use correct() to change it")
        known = _require_aware(known_at, "known_at") or utcnow()
        valid_start = _require_aware(valid_from, "valid_from") or known
        _require_aware(valid_to, "valid_to")
        _require_jsonable(value, "value")
        version = BeliefVersion(
            key=key,
            value=value,
            version=1,
            valid_from=valid_start,
            valid_to=valid_to,
            known_from=known,
            known_to=None,
        )
        self._versions[key] = [version]
        return version

    def correct(
        self,
        key: str,
        value: dict,
        *,
        known_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        end_previous_valid_at: datetime | None = None,
    ) -> BeliefVersion:
        """Correct a belief: append a new version, close the old one.

        The current version's ``known_to`` is always set to
        ``known_at`` (default now, UTC): from that moment on, the
        system no longer believes the old value. The old version's
        ``valid_to`` is set only when ``end_previous_valid_at``
        is given — i.e. only when the fact itself stopped being
        true then (the world changed). When it is not given, the
        old valid interval is left as recorded: the old row keeps
        saying what the system believed was true then, and the
        new row carries the corrected valid interval (a
        late-arriving truth about the past).

        The new version's ``valid_from`` defaults to
        ``known_at`` (the fact holds from when we learned the
        correction); pass an earlier ``valid_from`` for a
        correction about the past. Raises ValueError when the
        key has no version yet, when ``known_at`` predates the
        current version's ``known_from`` (history must be
        recorded in known-time order), or when the times are
        inconsistent.
        """
        versions = self._versions.get(key)
        if not versions:
            raise ValueError(f"belief {key!r} has no version yet; use record() first")
        old = versions[-1]
        if not old.is_current:
            raise ValueError(f"belief {key!r} has no current version (corrupt history)")
        known = _require_aware(known_at, "known_at") or utcnow()
        if known < old.known_from:
            raise ValueError(
                f"known_at ({known}) predates the current version's "
                f"known_from ({old.known_from}): history is recorded "
                "in known-time order"
            )
        valid_start = _require_aware(valid_from, "valid_from") or known
        _require_aware(valid_to, "valid_to")
        end_valid = _require_aware(end_previous_valid_at, "end_previous_valid_at")
        if end_valid is not None:
            if end_valid < old.valid_from:
                raise ValueError(
                    f"end_previous_valid_at ({end_valid}) predates the old "
                    f"version's valid_from ({old.valid_from})"
                )
            if old.valid_to is not None and end_valid != old.valid_to:
                raise ValueError(
                    f"old version's valid_to is already {old.valid_to}; "
                    f"cannot re-close it at {end_valid}"
                )
        _require_jsonable(value, "value")
        closed = replace(old, known_to=known, valid_to=end_valid or old.valid_to)
        versions[-1] = closed
        new = BeliefVersion(
            key=key,
            value=value,
            version=old.version + 1,
            valid_from=valid_start,
            valid_to=valid_to,
            known_from=known,
            known_to=None,
            supersedes=old.version,
        )
        versions.append(new)
        return new

    # -- reads ---------------------------------------------------------
    def current(self, key: str) -> BeliefVersion | None:
        """The version believed now, or None when the key is unknown."""
        versions = self._versions.get(key)
        if not versions:
            return None
        return versions[-1]

    def history(self, key: str) -> list[BeliefVersion]:
        """All versions of a key, oldest first (a copy)."""
        return list(self._versions.get(key, []))

    def keys(self) -> list[str]:
        """All known belief keys, sorted."""
        return sorted(self._versions)

    def __iter__(self):  # iterate keys, sorted (like ``keys()``)
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self._versions)

    def known_as_of(self, key: str, at: datetime) -> BeliefVersion | None:
        """The version believed at ``at`` (known time), or None.

        Half-open: at the exact instant a correction was learned,
        the new version already counts as believed.
        """
        _require_aware(at, "at")
        for version in self._versions.get(key, []):
            if version.is_known_at(at):
                return version
        return None

    def valid_as_of(self, key: str, at: datetime) -> BeliefVersion | None:
        """The version true at ``at`` under current knowledge.

        The latest version whose valid interval covers ``at``:
        a later correction overrides an earlier claim for the
        valid times it covers, and a world-change correction
        does not cover the old fact's interval, so the old fact
        still reads true then. (A late correction about the
        past does cover the old interval, so the corrected
        value wins there.) Returns None when no version claims
        ``at`` was true.
        """
        _require_aware(at, "at")
        best: BeliefVersion | None = None
        for version in self._versions.get(key, []):
            if version.is_valid_at(at):
                best = version  # versions are stored in order; last covering wins
        return best

    def as_of(
        self,
        key: str,
        *,
        valid_at: datetime | None = None,
        known_at: datetime | None = None,
    ) -> BeliefVersion | None:
        """The version valid at ``valid_at`` and known at ``known_at``.

        Either argument may be omitted: no ``known_at`` means
        current knowledge (same as :meth:`valid_as_of`), no
        ``valid_at`` means any valid time (same as
        :meth:`known_as_of`), neither means the current version.
        """
        _require_aware(valid_at, "valid_at")
        _require_aware(known_at, "known_at")
        if valid_at is None and known_at is None:
            return self.current(key)
        if valid_at is None:
            assert known_at is not None
            return self.known_as_of(key, known_at)
        if known_at is None:
            return self.valid_as_of(key, valid_at)
        for version in self._versions.get(key, []):
            if version.is_valid_at(valid_at) and version.is_known_at(known_at):
                return version
        return None

    # -- serialization -------------------------------------------------
    def to_dict(self) -> dict:
        """Whole store as JSON-safe data: ``{"beliefs": {key: [...]}}``."""
        return {
            "beliefs": {
                key: [version.to_dict() for version in versions]
                for key, versions in sorted(self._versions.items())
            }
        }

    @classmethod
    def from_dict(cls, data: dict) -> BitemporalBeliefStore:
        """Rebuild a store; raises ValueError on corrupt history."""
        versions: list[BeliefVersion] = []
        for key, rows in (data.get("beliefs") or {}).items():
            for row in rows:
                version = BeliefVersion.from_dict(row)
                if version.key != key:
                    raise ValueError(
                        f"version key {version.key!r} does not match its group {key!r}"
                    )
                versions.append(version)
        return cls.from_versions(versions)

    @classmethod
    def from_versions(cls, versions: list[BeliefVersion]) -> BitemporalBeliefStore:
        """Rebuild a store from version rows (e.g. from Postgres).

        Validates the per-key invariants: versions count from 1
        with no gaps, known intervals are contiguous, exactly
        one version per key is current.
        """
        store = cls()
        grouped: dict[str, list[BeliefVersion]] = {}
        for version in versions:
            grouped.setdefault(version.key, []).append(version)
        for key, rows in grouped.items():
            rows.sort(key=lambda row: row.version)
            if [row.version for row in rows] != list(range(1, len(rows) + 1)):
                raise ValueError(f"belief {key!r}: versions must count 1..n with no gaps")
            if sum(1 for row in rows if row.is_current) != 1 or not rows[-1].is_current:
                raise ValueError(f"belief {key!r}: exactly the latest version must be current")
            for previous, current in itertools.pairwise(rows):
                if previous.known_to != current.known_from:
                    raise ValueError(
                        f"belief {key!r}: known intervals must be contiguous "
                        f"(v{previous.version}.known_to != v{current.version}.known_from)"
                    )
            store._versions[key] = rows
        return store
