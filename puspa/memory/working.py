"""Working memory (Kosmos Brain v2, Phase 4, K15): the short-term store.

A mind holds a few things at once, not everything. This module is
that small store: a bounded set of slots the brain keeps alive by
rehearsal, in the spirit of Baddeley's working memory. It is the
short-term layer the router reads first each turn; what survives
here long enough moves to long-term memory, what is ignored fades.

How it works, in plain words:

- Bounded: the store holds at most ``capacity`` items (7 by
  default, Miller's 7 plus or minus 2). Adding one more evicts
  the weakest item in the store, so the strongest survive a full
  store. The capacity is a hard limit, never a suggestion.
- Attention-weighted retention: every item carries an attention
  weight from 0 to 1. Attention sets both how strong the item
  starts and how slowly it fades: a high-attention item starts
  near full strength and decays slowly, a low-attention item
  starts weak and fades fast.
- Decay: an item's retention falls over time since it was last
  touched, on a half-life curve. An item whose retention falls
  below the forget threshold is dropped the next time the store
  is read or swept, so unrehearsed items disappear on their own.
- Rehearsal: repeating an item (``rehearse``) refreshes its
  decay clock and nudges its attention up a little, so a
  rehearsed item outlives an ignored one of the same age.
- Promotion: after enough rehearsals (3 by default) the item
  is promoted to long-term memory through the path that already
  exists: ``puspa.memory.facts.record_fact`` with source
  ``"inferred"``, so it lands in the fact store as a proposal
  (never silently active) and shows up in the rebuilt MEMORY.md
  as an unconfirmed guess. The working slot is freed when the
  promotion lands. No second long-term store is built here.

What this module does NOT do: it does not persist across
restarts (the store is in-memory, like ``MemoryFactStore``;
durable sessions are a separate known gap), it does not decide
what deserves attention (the caller passes the weight), and it
does not write to the ledger or the durable document itself —
promotion goes through the fact store, and the nightly rebuild
picks the promoted fact up from there.

Determinism: the clock is injectable (a callable returning an
aware datetime, like the ``time.monotonic``-shaped clock in
``puspa.governor.budgets``), so tests move time by hand instead
of waiting on the wall clock. Every method also accepts an
explicit ``now``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import facts as facts_module

__all__ = [
    "DEFAULT_CAPACITY",
    "DEFAULT_FORGET_THRESHOLD",
    "DEFAULT_HALF_LIFE_SECONDS",
    "DEFAULT_PROMOTE_AFTER_REHEARSALS",
    "REHEARSAL_ATTENTION_BOOST",
    "WorkingItem",
    "WorkingMemory",
]

#: Default slots in the store: Miller's 7 plus or minus 2.
DEFAULT_CAPACITY = 7

#: Retention below this is forgotten (swept on the next read).
DEFAULT_FORGET_THRESHOLD = 0.05

#: Half-life, in seconds, of an item with attention 0.5. The
#: half-life scales with attention (see ``WorkingItem.half_life``),
#: so this is the middle of the range, not a fixed decay rate.
DEFAULT_HALF_LIFE_SECONDS = 300.0

#: Rehearsals before an item is promoted to long-term memory.
DEFAULT_PROMOTE_AFTER_REHEARSALS = 3

#: Attention gained per rehearsal, capped at 1.0.
REHEARSAL_ATTENTION_BOOST = 0.1


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_attention(attention: float) -> float:
    """Attention as a float in [0, 1]; anything else is a caller bug."""
    try:
        value = float(attention)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"attention must be a number in [0, 1], got {attention!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"attention must be in [0, 1], got {attention!r}")
    return value


@dataclass
class WorkingItem:
    """One thing held in working memory.

    ``key`` identifies the item (re-adding the same key refreshes
    it instead of duplicating it). ``content`` is the text held.
    ``attention`` is the weight from 0 to 1: it sets the starting
    retention and slows the decay. ``rehearsals`` counts how many
    times the item was rehearsed. ``created_at`` is when the item
    first entered the store; ``last_touched_at`` is the last add
    or rehearsal, the clock decay runs from. ``promoted`` is True
    once the item moved to long-term memory.
    """

    key: str
    content: str
    attention: float = 0.5
    rehearsals: int = 0
    created_at: datetime = field(default_factory=_now)
    last_touched_at: datetime = field(default_factory=_now)
    promoted: bool = False

    @property
    def half_life_seconds(self) -> float:
        """Decay half-life in seconds: attention stretches it.

        Attention 0 decays twice as fast as the base, attention 1
        one and a half times slower: the range is 0.5x to 1.5x the
        base half-life, so attention is worth at most a factor of
        three in lifetime.
        """
        return DEFAULT_HALF_LIFE_SECONDS * (0.5 + self.attention)

    def retention(self, now: datetime) -> float:
        """Retention at ``now``: attention times the decay factor.

        The decay factor halves every half-life since the item was
        last touched, so retention starts at ``attention`` and
        falls toward zero. A naive timestamp is read as UTC.
        """
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        touched = self.last_touched_at
        if touched.tzinfo is None:
            touched = touched.replace(tzinfo=UTC)
        age = max(0.0, (now - touched).total_seconds())
        return self.attention * (0.5 ** (age / self.half_life_seconds))

    def as_dict(self, now: datetime) -> dict[str, Any]:
        """The item as a plain dict, with its retention at ``now``."""
        return {
            "key": self.key,
            "content": self.content,
            "attention": self.attention,
            "rehearsals": self.rehearsals,
            "retention": self.retention(now),
            "created_at": self.created_at,
            "last_touched_at": self.last_touched_at,
            "promoted": self.promoted,
        }


class WorkingMemory:
    """A bounded working-memory store for one user.

    ``user_id`` owns the store; promoted facts are recorded under
    it. ``capacity`` is the maximum live items (at least 1).
    ``fact_store`` is the long-term sink promotion writes to
    (a ``MemoryFactStore`` in tests, a Postgres store in
    production); ``promoter`` overrides the sink entirely with a
    callable ``promoter(user_id, item_dict, now)`` returning a
    record dict with ``"stored": True`` when the promotion
    landed, or ``{"stored": False, ...}`` when it was refused.
    One of ``fact_store`` or ``promoter`` is required: a working
    memory with nowhere to promote to would silently drop what
    rehearsal earned. ``clock`` is a callable returning the
    current aware datetime; methods also accept an explicit
    ``now``, which wins over the clock.
    """

    def __init__(
        self,
        user_id: str,
        *,
        capacity: int = DEFAULT_CAPACITY,
        fact_store: Any = None,
        promoter: Callable[[str, dict[str, Any], datetime], dict[str, Any]] | None = None,
        clock: Callable[[], datetime] | None = None,
        promote_after: int = DEFAULT_PROMOTE_AFTER_REHEARSALS,
        forget_threshold: float = DEFAULT_FORGET_THRESHOLD,
    ) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError(f"capacity must be an integer >= 1, got {capacity!r}")
        if (
            not isinstance(promote_after, int)
            or isinstance(promote_after, bool)
            or promote_after < 1
        ):
            raise ValueError(f"promote_after must be an integer >= 1, got {promote_after!r}")
        if not 0.0 < float(forget_threshold) < 1.0:
            raise ValueError(f"forget_threshold must be in (0, 1), got {forget_threshold!r}")
        if fact_store is None and promoter is None:
            raise ValueError("working memory needs a long-term sink: pass fact_store= or promoter=")
        self.user_id = user_id
        self.capacity = capacity
        self._fact_store = fact_store
        self._promoter = promoter
        self._clock = clock or _now
        self.promote_after = promote_after
        self.forget_threshold = float(forget_threshold)
        self._items: dict[str, WorkingItem] = {}

    # -- time ------------------------------------------------------

    def _time(self, now: datetime | None) -> datetime:
        """The effective now: the explicit value, else the clock."""
        value = now if now is not None else self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    # -- adding and eviction ----------------------------------------

    def add(
        self,
        key: str,
        content: str,
        *,
        attention: float = 0.5,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Hold ``content`` under ``key``.

        Re-adding a key refreshes it in place (new content and
        attention, decay clock reset, rehearsal count kept) and
        evicts nothing. Adding a new key to a full store evicts
        the weakest live item first — the lowest retention at
        ``now``, oldest on a tie — so the strongest survive.
        Returns ``{"key", "evicted", "retention"}`` where
        ``"evicted"`` is the evicted key or None.
        """
        if not isinstance(key, str) or not key.strip():
            raise ValueError("key must be a non-empty string")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        attention = _validate_attention(attention)
        stamp = self._time(now)
        self.sweep(now=stamp)
        existing = self._items.get(key)
        if existing is not None:
            existing.content = content
            existing.attention = attention
            existing.last_touched_at = stamp
            return {"key": key, "evicted": None, "retention": existing.retention(stamp)}
        evicted = None
        if len(self._items) >= self.capacity:
            evicted = self._evict_weakest(stamp)
        self._items[key] = WorkingItem(
            key=key,
            content=content,
            attention=attention,
            created_at=stamp,
            last_touched_at=stamp,
        )
        return {
            "key": key,
            "evicted": evicted,
            "retention": self._items[key].retention(stamp),
        }

    def _evict_weakest(self, now: datetime) -> str | None:
        """Drop the weakest live item; return its key, or None."""
        if not self._items:
            return None
        weakest = min(
            self._items.values(),
            key=lambda item: (item.retention(now), item.last_touched_at, item.key),
        )
        del self._items[weakest.key]
        return weakest.key

    # -- rehearsal and promotion ------------------------------------

    def rehearse(self, key: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Rehearse the item under ``key``.

        Rehearsal refreshes the decay clock and raises attention
        by :data:`REHEARSAL_ATTENTION_BOOST` (capped at 1.0), so a
        rehearsed item outlives an ignored one. When the
        rehearsal count reaches ``promote_after``, the item is
        promoted to long-term memory and its slot is freed — but
        only when the promotion actually lands: a refused
        promotion (the trust gate blocked the content) keeps the
        item in the store and reports the refusal instead of
        dropping what rehearsal earned.

        Returns ``{"key", "rehearsals", "retention", "promoted",
        "promotion", "promotion_refused"}``. Raises ``KeyError``
        for an unknown or already-forgotten key.
        """
        stamp = self._time(now)
        item = self._items.get(key)
        if item is None:
            raise KeyError(f"no working-memory item under key {key!r}")
        if item.retention(stamp) < self.forget_threshold:
            # Decayed past forgetting: rehearsal cannot revive it.
            del self._items[key]
            raise KeyError(f"working-memory item {key!r} decayed and was forgotten")
        item.rehearsals += 1
        item.attention = min(1.0, item.attention + REHEARSAL_ATTENTION_BOOST)
        item.last_touched_at = stamp
        result: dict[str, Any] = {
            "key": key,
            "rehearsals": item.rehearsals,
            "retention": item.retention(stamp),
            "promoted": False,
            "promotion": None,
            "promotion_refused": None,
        }
        if item.rehearsals < self.promote_after:
            return result
        promotion = self._promote(item, stamp)
        if not promotion.get("stored"):
            result["promotion_refused"] = {
                "verdict": promotion.get("verdict"),
                "reasons": promotion.get("reasons"),
            }
            return result
        item.promoted = True
        del self._items[key]
        result["promoted"] = True
        result["promotion"] = promotion
        return result

    def _promote(self, item: WorkingItem, now: datetime) -> dict[str, Any]:
        """Write one item to long-term memory; return the outcome.

        The default sink records an inferred fact (a proposal,
        never silently active) with a provenance note naming the
        rehearsal count, through the same ``record_fact`` the
        rest of memory uses. A custom ``promoter`` replaces it.
        """
        detail = item.as_dict(now)
        if self._promoter is not None:
            return dict(self._promoter(self.user_id, detail, now))
        return dict(
            facts_module.record_fact(
                self.user_id,
                item.key,
                item.content,
                source="inferred",
                store=self._fact_store,
                now=now,
                source_detail=(f"promoted from working memory after {item.rehearsals} rehearsals"),
            )
        )

    # -- reading -----------------------------------------------------

    def sweep(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Drop every item whose retention fell below the threshold.

        Returns the forgotten items as plain dicts (with their
        final retention), weakest first. A sweep never touches an
        item at or above the threshold.
        """
        stamp = self._time(now)
        forgotten = [
            item for item in self._items.values() if item.retention(stamp) < self.forget_threshold
        ]
        forgotten.sort(key=lambda item: (item.retention(stamp), item.last_touched_at))
        for item in forgotten:
            del self._items[item.key]
        return [item.as_dict(stamp) for item in forgotten]

    def live_items(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Live items, strongest retention first.

        Reading sweeps first, so a returned item is always above
        the forget threshold: unrehearsed items disappear from
        the read on their own, without a separate sweep call.
        """
        stamp = self._time(now)
        self.sweep(now=stamp)
        live = sorted(
            self._items.values(),
            key=lambda item: (-item.retention(stamp), item.last_touched_at),
        )
        return [item.as_dict(stamp) for item in live]

    def snapshot_for_turn(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """The working-memory read for one turn: live items first.

        The router's seam: a turn reads this before anything
        else, so what the brain is currently holding is in
        context before intent inference runs. Same contents as
        :meth:`live_items`.
        """
        return self.live_items(now=now)

    def get(self, key: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        """One live item as a plain dict, or None when forgotten."""
        stamp = self._time(now)
        item = self._items.get(key)
        if item is None or item.retention(stamp) < self.forget_threshold:
            if item is not None:
                del self._items[key]
            return None
        return item.as_dict(stamp)

    def drop(self, key: str) -> bool:
        """Forget one item on purpose; True when it was held."""
        return self._items.pop(key, None) is not None

    def __len__(self) -> int:
        return len(self._items)
