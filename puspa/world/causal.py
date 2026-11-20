"""Causal Ledger (Kosmos K4).

Hypotheses of the form "X -> Y under condition C" with a
strength, evidence, and expiry (``docs/kosmos-design.md``
section C point 4: "Causal Ledger: hypotheses 'X -> Y under
condition C' with strength, evidence, and expiry"). A
hypothesis is a belief about cause and effect, so this module
lives in ``puspa.world`` next to the other beliefs, and the
learning loop (``puspa.memory.learning_loop``) proposes and
updates hypotheses from the action -> outcome pairs it sees.

The update rule (deterministic, stated here and in
``docs/causal-ledger.md`` — no magic numbers pretending to be
statistics):

- A first observation proposes the hypothesis at
  ``INITIAL_STRENGTH`` (0.5): one observation is not proof, so
  a new hypothesis starts neutral, neither believed nor
  disbelieved.
- A consistent observation strengthens: ``strength += 0.1``,
  capped at 1.0. Each further confirmation adds the same step;
  the step is a bookkeeping choice, not a probability.
- A contradicting observation weakens twice as fast:
  ``strength -= 0.2``, floored at 0.0. The asymmetry is
  deliberate: one counterexample disproves a causal claim
  more than one success proves it.
- A hypothesis whose strength falls below ``CONTRADICTED_FLOOR``
  (0.2) becomes ``contradicted``. A hypothesis with no
  confirming observation for ``DEFAULT_TTL_DAYS`` (90 days)
  becomes ``expired``; a consistent observation refreshes the
  expiry to now + TTL. Expired and contradicted are terminal:
  observing the same triple again starts a fresh hypothesis
  (new id); the old one stays as history.
- Contradiction never creates a hypothesis: weakening a triple
  nobody proposed is meaningless, so ``observe`` with
  ``consistent=False`` on an unknown triple returns None.

Evidence links are ``{"kind", "id"}`` dicts, kind
``"ledger_event"`` (an id from the append-only ledger,
``puspa.ledger``) or ``"observation"`` (an experience id from
the learning loop). Each observation adds at most one link of
each kind; a link already present is not added twice, and the
list keeps at most the newest ``EVIDENCE_CAP`` links.

Bitemporality (K3, ``puspa.world.bitemporal``), where it
applies and where it does not:

- It applies to the claim itself: ``valid_from`` is when the
  cause-effect was first observed to hold (defaults to the
  moment the hypothesis was formed), ``valid_to`` is set when
  the claim stops holding — the contradicting observation's
  time, or the expiry time. "Which causal claims held at time
  T" is answered from this interval.
- It does not apply to the strength score: strength is a
  running summary that is updated in place, not a fact about
  the past, so there are no strength versions and no as-of
  strength query. Known time is ``created_at`` (when the
  system formed the hypothesis); there is no ``known_to``
  because hypotheses are never corrected in place — they
  change status, and the old status stays readable in the
  stored record.

This module is pure Python (no database, no network, no LLM
call). Durable storage lives in ``puspa.world.store`` (table
``causal_hypotheses``) and in
migration 0006_world. The read path is
``CausalLedger.expect``: "what do we expect if X under C?"
returns the active hypotheses for a cause, strongest first.
Expired hypotheses are never returned by the read path.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

__all__ = [
    "CausalExpectationMixin",
    "CausalHypothesis",
    "CausalLedger",
    "causal_effect_key",
    "utcnow",
]

#: Strength of a freshly proposed hypothesis: neutral, not proof.
INITIAL_STRENGTH = 0.5
#: Step added by one consistent observation (capped at 1.0).
CONFIRM_STEP = 0.1
#: Step removed by one contradicting observation (floored at 0.0).
#: Twice the confirm step on purpose: a counterexample disproves
#: a causal claim more than a success proves it.
CONTRADICT_STEP = 0.2
#: Strength below this floor means the hypothesis is contradicted.
CONTRADICTED_FLOOR = 0.2
#: Days without a confirming observation before a hypothesis expires.
DEFAULT_TTL_DAYS = 90
#: Longest an effect key may be before it is stored as a hash.
MAX_EFFECT_KEY_CHARS = 200
#: Most evidence links kept per hypothesis (newest first).
EVIDENCE_CAP = 100

HYPOTHESIS_STATUSES = ("active", "expired", "contradicted")
EVIDENCE_KINDS = ("ledger_event", "observation")


def utcnow() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(UTC)


def causal_effect_key(value) -> str:
    """Canonical key for an effect value.

    Any JSON-able value becomes one deterministic string
    (keys sorted); a non-JSON-able value is keyed by its
    ``str()`` form. Keys longer than ``MAX_EFFECT_KEY_CHARS``
    are stored as ``sha256:<hex>`` of the canonical form, so
    keys stay bounded without ever colliding two different
    effects through truncation.
    """
    try:
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        canonical = str(value)
    if len(canonical) > MAX_EFFECT_KEY_CHARS:
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"
    return canonical


def _require_aware(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime, got {value!r}")
    return value


def _parse_time(raw: str | None, name: str) -> datetime | None:
    if raw is None:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must carry a timezone, got {raw!r}")
    return parsed


@dataclass(frozen=True)
class CausalHypothesis:
    """One causal claim: cause X leads to effect Y under condition C.

    ``cause`` and ``condition`` are plain strings; ``condition``
    "" means unconditional (the claim holds regardless).
    ``effect`` is the canonical key of the observed outcome
    (see :func:`causal_effect_key`); ``effect_value`` is the
    outcome itself when it was JSON-able. ``evidence`` is a
    list of ``{"kind", "id"}`` links (ledger event ids and/or
    observation ids). ``valid_from`` / ``valid_to`` is the
    bitemporal valid interval of the claim (see the module
    docstring); ``created_at`` is its known time. ``status``
    is ``active``, ``expired`` or ``contradicted``; the last
    two are terminal.
    """

    id: str
    cause: str
    effect: str
    condition: str
    strength: float
    evidence: list = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    expires_at: datetime = field(default_factory=utcnow)
    valid_from: datetime = field(default_factory=utcnow)
    valid_to: datetime | None = None
    status: str = "active"
    effect_value: object = None
    supporting: int = 0
    contradicting: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("hypothesis id must be non-empty")
        if not self.cause:
            raise ValueError("cause must be a non-empty string")
        if not self.effect:
            raise ValueError("effect must be a non-empty string")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be within 0..1, got {self.strength}")
        if self.status not in HYPOTHESIS_STATUSES:
            raise ValueError(f"status must be one of {HYPOTHESIS_STATUSES}, got {self.status!r}")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.expires_at, "expires_at")
        _require_aware(self.valid_from, "valid_from")
        _require_aware(self.valid_to, "valid_to")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError(
                f"valid_to ({self.valid_to}) must be after valid_from ({self.valid_from})"
            )
        if self.supporting < 0 or self.contradicting < 0:
            raise ValueError("observation counts must not be negative")
        for link in self.evidence:
            if not isinstance(link, dict) or link.get("kind") not in EVIDENCE_KINDS:
                raise ValueError(f"evidence links need a kind in {EVIDENCE_KINDS}: {link!r}")
            if not link.get("id"):
                raise ValueError(f"evidence links need a non-empty id: {link!r}")

    @property
    def is_active(self) -> bool:
        """True while the hypothesis is live (status active)."""
        return self.status == "active"

    def is_expired_at(self, at: datetime) -> bool:
        """True when the TTL has passed at ``at``."""
        _require_aware(at, "at")
        return at >= self.expires_at

    def is_valid_at(self, at: datetime) -> bool:
        """True when the claim held at ``at`` (valid time)."""
        _require_aware(at, "at")
        return self.valid_from <= at and (self.valid_to is None or at < self.valid_to)

    def to_dict(self) -> dict:
        """JSON-safe dict (times as ISO-8601 strings)."""
        return {
            "id": self.id,
            "cause": self.cause,
            "effect": self.effect,
            "condition": self.condition,
            "strength": self.strength,
            "evidence": [dict(link) for link in self.evidence],
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "status": self.status,
            "effect_value": self.effect_value,
            "supporting": self.supporting,
            "contradicting": self.contradicting,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CausalHypothesis:
        """Rebuild one hypothesis; raises ValueError on bad data."""
        return cls(
            id=str(data["id"]),
            cause=str(data["cause"]),
            effect=str(data["effect"]),
            condition=str(data.get("condition") or ""),
            strength=float(data["strength"]),
            evidence=[dict(link) for link in (data.get("evidence") or [])],
            created_at=_parse_time(data["created_at"], "created_at"),  # type: ignore[arg-type]
            expires_at=_parse_time(data["expires_at"], "expires_at"),  # type: ignore[arg-type]
            valid_from=_parse_time(data["valid_from"], "valid_from"),  # type: ignore[arg-type]
            valid_to=_parse_time(data.get("valid_to"), "valid_to"),
            status=str(data.get("status") or "active"),
            effect_value=data.get("effect_value"),
            supporting=int(data.get("supporting") or 0),
            contradicting=int(data.get("contradicting") or 0),
        )


class CausalLedger:
    """In-memory store of causal hypotheses.

    Keyed by the (cause, effect, condition) triple: at most one
    live hypothesis per triple. Expired and contradicted
    hypotheses stay stored as history under the same triple
    (the newest one is the live one, if any), so the full
    history of a claim survives. The read path
    (:meth:`expect`) only ever returns active hypotheses.
    """

    def __init__(self) -> None:
        self._hypotheses: dict[tuple[str, str, str], list[CausalHypothesis]] = {}

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _key(cause: str, effect: str, condition: str) -> tuple[str, str, str]:
        return (cause, effect, condition)

    @staticmethod
    def _check_strings(cause, condition) -> tuple[str, str]:
        cause = str(cause)
        condition = str(condition or "")
        if not cause:
            raise ValueError("cause must be a non-empty string")
        return cause, condition

    def _live(self, key: tuple[str, str, str]) -> CausalHypothesis | None:
        """The active hypothesis for a triple, or None."""
        for hypothesis in reversed(self._hypotheses.get(key, [])):
            if hypothesis.is_active:
                return hypothesis
        return None

    def _replace(self, old: CausalHypothesis, new: CausalHypothesis) -> None:
        """Swap one stored hypothesis for its updated version."""
        rows = self._hypotheses[self._key(old.cause, old.effect, old.condition)]
        for index, row in enumerate(rows):
            if row.id == old.id:
                rows[index] = new
                return
        raise ValueError(f"hypothesis {old.id!r} is not in this ledger")

    def _sweep_expired(self, now: datetime) -> int:
        """Mark live hypotheses past their TTL as expired.

        Runs at the start of every read and write, so expiry is
        real even when nobody calls :meth:`prune_expired`
        directly: an expired hypothesis is never returned, and
        its ``valid_to`` is its expiry time. Returns how many
        were expired by this sweep.
        """
        count = 0
        for rows in self._hypotheses.values():
            for hypothesis in list(rows):
                if hypothesis.is_active and hypothesis.is_expired_at(now):
                    expired = CausalHypothesis(
                        **{
                            **hypothesis.to_dict(),
                            "created_at": hypothesis.created_at,
                            "expires_at": hypothesis.expires_at,
                            "valid_from": hypothesis.valid_from,
                            "valid_to": hypothesis.expires_at,
                            "status": "expired",
                            "evidence": [dict(link) for link in hypothesis.evidence],
                        }
                    )
                    self._replace(hypothesis, expired)
                    count += 1
        return count

    @staticmethod
    def _with_evidence(existing: list[dict], evidence: list[dict] | None) -> list[dict]:
        """Old evidence plus new links (no duplicates, newest kept)."""
        links = [dict(link) for link in existing]
        seen = {(link["kind"], link["id"]) for link in links}
        for link in evidence or []:
            if not isinstance(link, dict) or link.get("kind") not in EVIDENCE_KINDS:
                raise ValueError(f"evidence links need a kind in {EVIDENCE_KINDS}: {link!r}")
            if not link.get("id"):
                raise ValueError(f"evidence links need a non-empty id: {link!r}")
            marker = (link["kind"], str(link["id"]))
            if marker in seen:
                continue
            seen.add(marker)
            links.append({"kind": link["kind"], "id": str(link["id"])})
        return links[-EVIDENCE_CAP:]

    # -- writes --------------------------------------------------------
    def propose(
        self,
        cause,
        effect,
        condition: str = "",
        *,
        evidence: list[dict] | None = None,
        now: datetime | None = None,
        ttl_days: int = DEFAULT_TTL_DAYS,
        valid_from: datetime | None = None,
        effect_value=None,
    ) -> CausalHypothesis:
        """Propose a hypothesis, or return the live one unchanged.

        Proposing is idempotent: when a live hypothesis already
        exists for the triple, it is returned as-is — proposing
        is not evidence, so it never changes the strength. When
        the triple is unknown, or its last hypothesis expired
        or was contradicted, a fresh hypothesis starts at
        ``INITIAL_STRENGTH`` with one supporting observation.
        """
        cause, condition = self._check_strings(cause, condition)
        effect_key = causal_effect_key(effect)
        moment = _require_aware(now, "now") or utcnow()
        if ttl_days <= 0:
            raise ValueError(f"ttl_days must be positive, got {ttl_days}")
        self._sweep_expired(moment)
        key = self._key(cause, effect_key, condition)
        live = self._live(key)
        if live is not None:
            return live
        value = effect if effect_value is None else effect_value
        try:
            json.dumps(value, default=str)
        except (TypeError, ValueError):
            value = str(value)
        hypothesis = CausalHypothesis(
            id=uuid.uuid4().hex,
            cause=cause,
            effect=effect_key,
            condition=condition,
            strength=INITIAL_STRENGTH,
            evidence=self._with_evidence([], evidence),
            created_at=moment,
            expires_at=moment + timedelta(days=ttl_days),
            valid_from=_require_aware(valid_from, "valid_from") or moment,
            effect_value=value,
            supporting=1,
            contradicting=0,
        )
        self._hypotheses.setdefault(key, []).append(hypothesis)
        return hypothesis

    def observe(
        self,
        cause,
        effect,
        condition: str = "",
        *,
        consistent: bool,
        evidence: list[dict] | None = None,
        now: datetime | None = None,
        ttl_days: int = DEFAULT_TTL_DAYS,
        valid_from: datetime | None = None,
        effect_value=None,
    ) -> CausalHypothesis | None:
        """Record one observation for a hypothesis.

        A consistent observation proposes the hypothesis when
        unknown (at ``INITIAL_STRENGTH``) and otherwise
        strengthens it by ``CONFIRM_STEP`` (capped at 1.0) and
        refreshes its expiry to now + TTL. A contradicting
        observation weakens a live hypothesis by
        ``CONTRADICT_STEP`` (floored at 0.0); below
        ``CONTRADICTED_FLOOR`` the hypothesis becomes
        ``contradicted`` and its valid interval closes at the
        observation time. Contradicting an unknown triple
        returns None and creates nothing.
        """
        cause, condition = self._check_strings(cause, condition)
        effect_key = causal_effect_key(effect)
        moment = _require_aware(now, "now") or utcnow()
        if ttl_days <= 0:
            raise ValueError(f"ttl_days must be positive, got {ttl_days}")
        self._sweep_expired(moment)
        key = self._key(cause, effect_key, condition)
        live = self._live(key)
        if live is None:
            if not consistent:
                return None
            return self.propose(
                cause,
                effect,
                condition,
                evidence=evidence,
                now=moment,
                ttl_days=ttl_days,
                valid_from=valid_from,
                effect_value=effect_value,
            )
        links = self._with_evidence(live.evidence, evidence)
        if consistent:
            strength = min(1.0, live.strength + CONFIRM_STEP)
            updated = CausalHypothesis(
                **{
                    **live.to_dict(),
                    "created_at": live.created_at,
                    "expires_at": moment + timedelta(days=ttl_days),
                    "valid_from": live.valid_from,
                    "valid_to": live.valid_to,
                    "strength": strength,
                    "evidence": links,
                    "supporting": live.supporting + 1,
                }
            )
        else:
            strength = max(0.0, live.strength - CONTRADICT_STEP)
            contradicted = strength < CONTRADICTED_FLOOR
            updated = CausalHypothesis(
                **{
                    **live.to_dict(),
                    "created_at": live.created_at,
                    "expires_at": live.expires_at,
                    "valid_from": live.valid_from,
                    "valid_to": moment if contradicted else live.valid_to,
                    "strength": strength,
                    "status": "contradicted" if contradicted else "active",
                    "evidence": links,
                    "contradicting": live.contradicting + 1,
                }
            )
        self._replace(live, updated)
        return updated

    def prune_expired(self, *, now: datetime | None = None) -> int:
        """Mark every live hypothesis past its TTL as expired.

        Returns how many were expired. The read path sweeps
        anyway, so this is for the daily sleep consolidation
        (K9): an explicit pass that records the count.
        """
        moment = _require_aware(now, "now") or utcnow()
        return self._sweep_expired(moment)

    # -- reads ---------------------------------------------------------
    def expect(
        self,
        cause,
        condition: str = "",
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """What do we expect if ``cause`` happens under ``condition``?

        Returns the active hypotheses for the cause as plain
        dicts, strongest first (ties: oldest first, then by
        effect and condition, so the order is deterministic
        even when two hypotheses were formed at the same
        instant). A hypothesis applies when its
        condition is unconditional ("") or equals the queried
        condition; a conditional claim is never returned when
        the caller does not state the condition. Expired and
        contradicted hypotheses are never returned. ``limit``
        caps the list when given.
        """
        cause, condition = self._check_strings(cause, condition)
        moment = _require_aware(now, "now") or utcnow()
        self._sweep_expired(moment)
        matches = []
        for (row_cause, _effect, row_condition), rows in self._hypotheses.items():
            if row_cause != cause:
                continue
            if row_condition not in ("", condition):
                continue
            for hypothesis in rows:
                if hypothesis.is_active:
                    matches.append(hypothesis)
        matches.sort(
            key=lambda item: (-item.strength, item.created_at, item.effect, item.condition)
        )
        if limit is not None:
            if limit < 0:
                raise ValueError(f"limit must not be negative, got {limit}")
            matches = matches[:limit]
        return [hypothesis.to_dict() for hypothesis in matches]

    def get(self, hypothesis_id: str) -> CausalHypothesis | None:
        """One hypothesis by id, any status, or None."""
        for rows in self._hypotheses.values():
            for hypothesis in rows:
                if hypothesis.id == str(hypothesis_id):
                    return hypothesis
        return None

    def list_all(self, *, include_terminal: bool = False) -> list[dict]:
        """All hypotheses as dicts, oldest first.

        By default only live ones; ``include_terminal=True``
        also returns expired and contradicted history.
        """
        rows = [
            hypothesis for key in sorted(self._hypotheses) for hypothesis in self._hypotheses[key]
        ]
        if not include_terminal:
            rows = [row for row in rows if row.is_active]
        rows.sort(key=lambda row: (row.created_at, row.id))
        return [row.to_dict() for row in rows]

    def __len__(self) -> int:
        return sum(len(rows) for rows in self._hypotheses.values())

    # -- serialization -------------------------------------------------
    def to_dict(self) -> dict:
        """Whole ledger as JSON-safe data: ``{"hypotheses": [...]}``."""
        return {"hypotheses": self.list_all(include_terminal=True)}

    @classmethod
    def from_dict(cls, data: dict) -> CausalLedger:
        """Rebuild a ledger; raises ValueError on bad data."""
        ledger = cls()
        for row in data.get("hypotheses") or []:
            hypothesis = CausalHypothesis.from_dict(row)
            key = cls._key(hypothesis.cause, hypothesis.effect, hypothesis.condition)
            ledger._hypotheses.setdefault(key, []).append(hypothesis)
        for rows in ledger._hypotheses.values():
            rows.sort(key=lambda row: (row.created_at, row.id))
        return ledger


class CausalExpectationMixin:
    """Causal read path for a model that owns a session ledger.

    Mixin, not a base class with state: the ledger is a session
    attachment, set by the session that owns the model
    (``puspa.chassis.agent_session`` and ``TaskRunner`` share the
    learning loop's ledger here). It lives here, not in
    ``puspa.world.model``, because that file's size baseline
    forbids growth and the read path is a clean seam: a one-method
    delegation to ``CausalLedger.expect``. ``WorldModel`` inherits
    it, so the read path stays ``WorldModel.expect``.
    """

    #: Session causal ledger, or None. A class attribute on purpose:
    #: the session assigns its own ledger per model instance, which
    #: shadows this default; never part of the model state.
    causal_ledger: CausalLedger | None = None

    def expect(self, cause, condition="", *, limit=None):
        """Causal read path: what do we expect if `cause` under `condition`?

        Active hypotheses for the cause, strongest first (see
        ``CausalLedger.expect``). Empty when this model has no causal
        ledger attached.
        """
        ledger = self.causal_ledger
        if ledger is None:
            return []
        try:
            return ledger.expect(cause, condition, limit=limit)
        except Exception:
            return []
