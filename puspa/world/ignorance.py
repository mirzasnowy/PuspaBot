"""Ignorance map (Kosmos K6).

A record of where the world model is blind: areas (topics) it has
encountered but cannot answer, either because it has no evidence
at all or because the evidence it holds is too thin to trust.

An area is one belief domain plus one topic:

- ``price`` — a product at a store, topic ``"product@store"``
  (the price-belief key, ``puspa.world.beliefs._price_key``).
- ``bill`` — one bill, topic the bill id.
- ``schedule`` — how long one task kind takes, topic the task kind.
- ``capability`` — whether the agent can do one task kind, topic
  the task kind.
- ``contact`` — how one contact responds, topic the contact id.
- ``habit`` — the recurring pattern of one task kind, topic the
  task kind.
- ``topic`` — a free topic with no belief behind it (a question
  the system hit and could not answer from the world model, e.g.
  "whether Ana likes oat milk"). Free topics stay on the map
  until a caller resolves them explicitly.

When is an area unknown? The rule is deliberately the same test
the forecasts use, plus a confidence floor:

- No belief for the topic, or a belief that holds no usable
  value (a price belief with no price yet, a habit belief with
  no repeated pattern, ...), means ``no_evidence``. The usable
  value test mirrors each ``WorldModel._forecast_*`` "known"
  test, so the map and the forecast never disagree about
  whether the model can answer.
- A belief with a usable value but confidence below
  ``UNKNOWN_CONFIDENCE`` means ``low_confidence``. The floor
  is the fresh-belief default (``DEFAULT_CONFIDENCE``): a fresh
  belief starts exactly at the floor, so one observation clears
  a ``no_evidence`` area, and only scored misses (confidence
  -0.2 each in ``WorldModel._apply_observation``) can push a
  belief below it. Correct predictions raise confidence back,
  so evidence heals a ``low_confidence`` area too.

The map is derived, not stored:

- The ``low_confidence`` half is computed from the live belief
  dicts on every read: a belief the model holds and can use, but
  no longer trusts (confidence below the floor after scored
  misses), shows up without anyone registering it. That is the
  only derived half, on purpose: a belief with no usable value
  yet is "not yet formed", not a gap the system hit — every task
  result grows a young habit belief with no repeated pattern
  yet, and listing all of those would flood the map with every
  task kind the user ever did.
- The ``no_evidence`` half is registered: a caller that hits a
  topic with no answer records it with ``register_unknown``
  (the planner does this when a plan needs a price nobody has
  seen). Reading the map re-checks every registered area
  against the current beliefs, and an area whose evidence has
  arrived is dropped from the record — the map never shows a
  gap the model has already closed.
- Nothing here is persisted. The derived half rebuilds from
  the belief state, which ``puspa.world.store`` already
  persists; the registered half rebuilds the next time a
  caller hits the same gap (the planner re-registers on every
  plan that needs the missing price). A free topic, which has
  no belief to re-derive from, is dropped on restart and must
  be re-registered by whoever hit it — that is the honest
  limit of an in-memory map, not a silent loss: the gap is
  re-discovered, not forgotten as known.

The read path is ``IgnoranceMixin`` (same seam as
``CausalExpectationMixin`` in ``puspa.world.causal`` and
``AgentModelMixin`` in ``puspa.world.agents``): it lives here,
not in ``puspa.world.model``, because that file's size
baseline forbids growth. ``WorldModel`` inherits it, so the
read path stays ``WorldModel.ignorance_map``.

Pure Python: no database, no network, no LLM call.
"""

from __future__ import annotations

from dataclasses import dataclass

from .beliefs import DEFAULT_CONFIDENCE

__all__ = [
    "IGNORANCE_DOMAINS",
    "IGNORANCE_REASONS",
    "UNKNOWN_CONFIDENCE",
    "IgnoranceArea",
    "IgnoranceMixin",
    "area_key",
    "parse_area_key",
    "probe_action_for",
]

#: Confidence below this means the model does not trust what it
#: holds. Equal to the fresh-belief default on purpose (see the
#: module docstring): one observation clears a gap, only scored
#: misses can push a belief under the floor.
UNKNOWN_CONFIDENCE = DEFAULT_CONFIDENCE

#: Domains the map covers: the six belief domains plus free topics.
IGNORANCE_DOMAINS = (
    "price",
    "bill",
    "schedule",
    "capability",
    "contact",
    "habit",
    "topic",
)

#: Reasons an area is on the map.
IGNORANCE_REASONS = ("no_evidence", "low_confidence")

#: Belief-dict attribute per belief domain ("topic" has no beliefs).
_BELIEF_ATTRS = {
    "price": "_price_beliefs",
    "bill": "_bill_beliefs",
    "schedule": "_schedule_beliefs",
    "capability": "_capability_beliefs",
    "contact": "_contact_beliefs",
    "habit": "_habit_beliefs",
}

#: Observations a habit belief needs before its pattern counts.
#: Mirrors ``puspa.world.model.HABIT_MIN_OBSERVATIONS``; the
#: constant lives there, and importing the model here would be
#: circular, so the value is repeated with this note.
HABIT_MIN_OBSERVATIONS = 3


def area_key(domain, topic) -> str:
    """Canonical key for one area: ``"domain:topic"``.

    A price topic must be ``"product@store"`` (the price-belief
    key); every other domain takes its topic as given. Raises
    ``ValueError`` for an unknown domain or an empty topic.
    """
    domain = str(domain)
    topic = str(topic)
    if domain not in IGNORANCE_DOMAINS:
        raise ValueError(f"unknown ignorance domain: {domain!r}")
    if not topic:
        raise ValueError("topic must be a non-empty string")
    if domain == "price" and "@" not in topic:
        raise ValueError(f"a price topic must be 'product@store', got {topic!r}")
    return f"{domain}:{topic}"


def parse_area_key(key: str) -> tuple[str, str]:
    """Split an area key back into ``(domain, topic)``.

    Raises ``ValueError`` for a key that is not
    ``"domain:topic"`` with a known domain.
    """
    raw = str(key)
    if ":" not in raw:
        raise ValueError(f"ignorance area key must be 'domain:topic', got {raw!r}")
    domain, topic = raw.split(":", 1)
    if domain not in IGNORANCE_DOMAINS or not topic:
        raise ValueError(f"ignorance area key must be 'domain:topic', got {raw!r}")
    if domain == "price" and "@" not in topic:
        raise ValueError(f"a price topic must be 'product@store', got {raw!r}")
    return domain, topic


def probe_action_for(domain, topic) -> dict | None:
    """The smallest world action that would supply evidence.

    One observation of the returned action is the evidence that
    clears the area (a price check for a price, a bill check
    for a bill, an estimate for a schedule, an attempt for a
    capability, a reply check for a contact, a pattern check
    for a habit). A free topic has no world action that could
    answer it — it needs a question to a person — so this
    returns None for ``"topic"``. Raises ``ValueError`` for an
    unknown domain or a malformed price topic.
    """
    domain = str(domain)
    topic = str(topic)
    if domain == "price":
        product_id, sep, store = topic.partition("@")
        if not sep or not product_id or not store:
            raise ValueError(f"a price topic must be 'product@store', got {topic!r}")
        return {"type": "check_price", "product_id": product_id, "store": store}
    if domain == "bill":
        return {"type": "check_bill", "bill_id": topic}
    if domain == "schedule":
        return {"type": "estimate_task", "task_kind": topic}
    if domain == "capability":
        return {"type": "attempt_task", "task_kind": topic}
    if domain == "contact":
        return {"type": "contact_reply", "contact_id": topic}
    if domain == "habit":
        return {"type": "habit_pattern", "task_kind": topic}
    if domain == "topic":
        return None
    raise ValueError(f"unknown ignorance domain: {domain!r}")


def _belief_has_evidence(domain: str, belief: object | None) -> bool:
    """True when a belief holds a value the forecast can use.

    This is the same "known" test each ``WorldModel._forecast_*``
    applies (a price belief with no price yet, a habit belief
    with no repeated pattern, and so on all count as no
    evidence), so the map and the forecast never disagree about
    whether the model can answer a topic.
    """
    if belief is None:
        return False
    if domain == "price":
        return getattr(belief, "last_price", None) is not None
    if domain == "bill":
        return getattr(belief, "amount", None) is not None
    if domain == "schedule":
        return getattr(belief, "mean_duration", None) is not None
    if domain == "capability":
        return getattr(belief, "success_rate", None) is not None
    if domain == "contact":
        return getattr(belief, "reply_rate", None) is not None
    if domain == "habit":
        observations = getattr(belief, "observations", 0) or 0
        return observations >= HABIT_MIN_OBSERVATIONS and bool(
            getattr(belief, "has_repeated_pattern", False)
        )
    return False


@dataclass(frozen=True)
class IgnoranceArea:
    """One gap in the model: a domain + topic it cannot answer.

    ``key`` is ``"domain:topic"`` (see :func:`area_key`).
    ``reason`` is ``"no_evidence"`` (no belief, or a belief with
    no usable value) or ``"low_confidence"`` (a usable value the
    model no longer trusts). ``confidence`` is the belief's
    confidence when a belief exists, else None; ``observations``
    is its observation count, else 0. ``first_seen_day`` /
    ``last_seen_day`` are model days: when a caller first hit
    the gap and when it last hit it. Derived areas (found by
    scanning the beliefs, never registered) carry None for both
    days: nobody hit them, the model noticed them itself.
    """

    key: str
    domain: str
    topic: str
    reason: str
    confidence: float | None = None
    observations: int = 0
    first_seen_day: int | None = None
    last_seen_day: int | None = None

    def __post_init__(self) -> None:
        if self.domain not in IGNORANCE_DOMAINS:
            raise ValueError(f"unknown ignorance domain: {self.domain!r}")
        if self.reason not in IGNORANCE_REASONS:
            raise ValueError(f"unknown ignorance reason: {self.reason!r}")
        if self.key != area_key(self.domain, self.topic):
            raise ValueError(
                f"key {self.key!r} does not match domain {self.domain!r} and topic {self.topic!r}"
            )
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within 0..1, got {self.confidence}")
        if self.observations < 0:
            raise ValueError(f"observations must not be negative, got {self.observations}")

    def to_dict(self) -> dict:
        """JSON-safe dict of this area."""
        return {
            "key": self.key,
            "domain": self.domain,
            "topic": self.topic,
            "reason": self.reason,
            "confidence": self.confidence,
            "observations": self.observations,
            "first_seen_day": self.first_seen_day,
            "last_seen_day": self.last_seen_day,
        }


class IgnoranceMixin:
    """Ignorance read path for a model that owns belief dicts.

    The registered half of the map lives in
    ``self._ignorance_registered`` (created here in ``__init__``:
    ``WorldModel.__init__`` must call ``super().__init__()`` so
    this runs). The belief dicts themselves are read through
    ``getattr`` with an empty default, so the mixin also works
    on a host that has not built them yet — such a host simply
    has no derived areas. It lives here, not in
    ``puspa.world.model``, because that file's size baseline
    forbids growth. The planner is the real caller: goal
    ``"find_cheapest_price"`` in ``puspa.world.planner``
    registers the missing prices and returns a probe step
    instead of guessing a store.
    """

    #: Registered gaps: topics a caller hit without evidence.
    _ignorance_registered: dict[str, IgnoranceArea]

    def __init__(self) -> None:
        """Create the registered-gap store. Runs via super().__init__()."""
        self._ignorance_registered = {}
        super().__init__()

    # -- belief lookup -------------------------------------------------
    def _belief_for(self, domain: str, topic: str) -> object | None:
        """The belief for one area, or None when there is none."""
        attr = _BELIEF_ATTRS.get(domain)
        if attr is None:
            return None
        beliefs = getattr(self, attr, {}) or {}
        return beliefs.get(topic)

    def _belief_numbers(self, belief: object | None) -> tuple[float | None, int]:
        """(confidence, observations) of a belief, or (None, 0)."""
        if belief is None:
            return None, 0
        confidence = getattr(belief, "confidence", None)
        observations = getattr(belief, "observations", 0) or 0
        return (float(confidence) if confidence is not None else None, int(observations))

    def _area_for(
        self, domain: str, topic: str, *, first_seen_day: int | None, last_seen_day: int | None
    ) -> IgnoranceArea | None:
        """The area for one topic, or None when the model can answer it.

        ``None`` means the belief holds a usable value with
        confidence at or above the floor: the gap is closed.
        Otherwise the area carries the matching reason and the
        belief's current numbers.
        """
        belief = self._belief_for(domain, topic)
        confidence, observations = self._belief_numbers(belief)
        if _belief_has_evidence(domain, belief) and (
            confidence is None or confidence >= UNKNOWN_CONFIDENCE
        ):
            return None
        reason = "low_confidence" if _belief_has_evidence(domain, belief) else "no_evidence"
        return IgnoranceArea(
            key=area_key(domain, topic),
            domain=domain,
            topic=topic,
            reason=reason,
            confidence=confidence,
            observations=observations,
            first_seen_day=first_seen_day,
            last_seen_day=last_seen_day,
        )

    # -- writes --------------------------------------------------------
    def register_unknown(self, domain, topic, *, day: int | None = None) -> IgnoranceArea | None:
        """Record that the system hit a topic it cannot answer.

        Returns the recorded area, or None when the model can
        already answer the topic (a belief with a usable value
        and confidence at or above the floor) — registering a
        known topic records nothing. Re-registering a gap keeps
        its first-seen day and refreshes its last-seen day and
        its numbers from the current belief, so the record
        tracks the gap instead of duplicating it.
        """
        key = area_key(domain, topic)
        domain = str(domain)
        topic = str(topic)
        moment = int(getattr(self, "day", 0) if day is None else day)
        existing = self._ignorance_registered.get(key)
        area = self._area_for(
            domain,
            topic,
            first_seen_day=existing.first_seen_day if existing is not None else moment,
            last_seen_day=moment,
        )
        if area is None:
            return None
        self._ignorance_registered[key] = area
        return area

    def resolve_unknown(self, domain, topic) -> bool:
        """Drop a registered free-topic area once its answer arrives.

        Only the ``"topic"`` domain resolves this way: a free
        topic has no belief behind it, so only the caller that
        asked the question can say it is answered. A belief
        domain never resolves by declaration — its area leaves
        the map only when evidence arrives (a belief with a
        usable value and confidence at or above the floor), so
        resolving one returns False and changes nothing.
        Returns True when an area was dropped.
        """
        key = area_key(domain, topic)
        if str(domain) != "topic":
            return False
        return self._ignorance_registered.pop(key, None) is not None

    # -- reads ---------------------------------------------------------
    def ignorance_map(self, *, domain: str | None = None) -> list[IgnoranceArea]:
        """Every gap the model currently has, sorted by key.

        Registered areas are re-checked against the live
        beliefs on every read: an area whose evidence has
        arrived is dropped from the record (the read
        reconciles, the way ``CausalLedger.expect`` sweeps
        expired hypotheses), and an area whose belief has
        since been proven wrong is re-reported as
        ``low_confidence`` with its current numbers. The
        derived half scans all six belief dicts for beliefs
        that hold a usable value below the confidence floor,
        so a belief the model stopped trusting shows up even
        when nobody registered it. A belief with no usable
        value yet is not derived: ``no_evidence`` areas come
        only from registration (see the module docstring), so
        the map never floods with young habit beliefs.
        ``domain`` filters to one domain when given.
        """
        if domain is not None and str(domain) not in IGNORANCE_DOMAINS:
            raise ValueError(f"unknown ignorance domain: {domain!r}")
        areas: dict[str, IgnoranceArea] = {}
        closed: list[str] = []
        for key, recorded in self._ignorance_registered.items():
            area = self._area_for(
                recorded.domain,
                recorded.topic,
                first_seen_day=recorded.first_seen_day,
                last_seen_day=recorded.last_seen_day,
            )
            if area is None:
                closed.append(key)
            else:
                areas[key] = area
        for key in closed:
            del self._ignorance_registered[key]
        for belief_domain, attr in _BELIEF_ATTRS.items():
            beliefs = getattr(self, attr, {}) or {}
            for topic in beliefs:
                key = area_key(belief_domain, topic)
                if key in areas:
                    continue
                area = self._area_for(
                    belief_domain,
                    topic,
                    first_seen_day=None,
                    last_seen_day=None,
                )
                # Derived half is low-confidence only: a belief
                # with no usable value yet is "not yet formed",
                # not a gap the system hit (see the module
                # docstring), so a derived no_evidence area is
                # never listed.
                if area is not None and area.reason == "low_confidence":
                    areas[key] = area
        result = [area for area in areas.values() if domain is None or area.domain == domain]
        result.sort(key=lambda area: area.key)
        return result

    def is_unknown(self, domain, topic) -> bool:
        """True when the area is on the ignorance map right now."""
        key = area_key(domain, topic)
        return any(area.key == key for area in self.ignorance_map())

    def probe_for(self, domain, topic) -> dict | None:
        """The smallest probe for one gap, or None.

        Returns ``{"action": ..., "area": key}`` when the area
        is unknown and a world action can supply the missing
        evidence. Returns None when the area is already known
        (nothing to probe) or when no world action can supply
        it (a free topic needs a question to a person, not an
        action). The caller runs the probe action, and the
        observation that comes back clears the area from the
        map.
        """
        key = area_key(domain, topic)
        if not self.is_unknown(domain, topic):
            return None
        action = probe_action_for(domain, topic)
        if action is None:
            return None
        return {"action": action, "area": key}
