"""P-02 control layer: the stop-all dial the user sees (plan section 12.7).

This module is the user-facing control over the brain dial
(:mod:`puspa.autonomy.dial`, K11). It does not replace the dial
and does not score anything itself: it reads evidence the system
already recorded, turns it into a visible suggestion, and lets
the user move a ceiling — one step, only after evidence.

The five shown areas (plan 12.7: Money, Outgoing messages,
Delete, Schedule, Shopping) are a presentation of the dial's
areas. ``system`` and ``accounts`` are not shown: accounts is
already capped to ask-first by the dial itself, and system
(skill installs, settings) has no user-facing dial in this
version. Shopping is a dial area of its own (price checks,
comparisons, draft orders); the payment for a purchase is a
``money`` action, so it still asks first under the money lock.

Rules:

1. Ceilings. Each shown area has a ceiling — Ask first /
   Balanced / Autonomous — mapping to the dial stages
   ``propose`` / ``do_and_show`` / ``do_silently``. The
   effective stage is the lower of the evidence score stage
   and the ceiling, so a ceiling never lets an area do more
   than its evidence allows; it only holds it down. Every
   area starts at Ask first: trust is earned, never assumed.
2. Raises need evidence. A ceiling rises one step at a time,
   and only when the area's streak of consecutive accepted
   drafts reaches :data:`SUGGESTION_THRESHOLD` (10). The
   suggestion is visible in the state payload, with the
   threshold in plain words ("10 accepted drafts in a row"),
   so the user sees both the offer and what earned it.
   Lowering a ceiling is always allowed: getting more
   cautious never needs permission.
3. Money is locked. The Money area refuses any ceiling above
   Ask first, and the dial itself caps the money score at
   0.59, so money never leaves ask-first through any path.
4. Evidence. An "accepted draft" is one approval the user
   granted in that area. Signals come from two records the
   system already keeps, merged into one chronological
   stream and counted as a trailing run (a rejection or a
   correction anywhere in the area resets it to zero):

   - Ledger approval events (the audit log): ``approval_granted``
     / ``approval_rejected`` with a Gate kind (``send``,
     ``create_event``, ``prepare_payment``, ``delete``), matched
     to an area; a rejection whose kind cannot be resolved
     breaks every area's streak, because an unattributed
     correction must not leave a "10 in a row" claim standing.
     ``false_report`` breaks every streak the same way.
   - Drafts (``puspa.drafting``): an approved or sent draft is
     an accepted draft in ``messaging``; a draft the user
     revised instead of accepting (status back to ``draft``
     with a newer ``updated_at``) is a correction and resets
     the messaging streak. A draft still waiting for a
     decision is neither: it adds nothing and breaks nothing.

   A draft approval in the PWA draft flow writes no ledger
   event (``puspa.pwa.drafts_api`` does not pass a ledger),
   so without the drafts source the messaging streak would
   never move for the flow users actually use. Both sources
   are read, so the streak counts what the user really did.

Honest limits, stated plainly:

- Streaks are computed from the records above, over a window
  the caller supplies (the API reads the last 90 days). Older
  evidence does not count, and a user with no recorded
  approvals has a streak of zero everywhere — no suggestion
  is ever shown without evidence behind it.
- Ceilings and scores are in-memory, like the dial itself: a
  restart returns every area to Ask first. That is the safe
  direction, and the first version accepts it (same choice as
  the K6 ignorance map and the K10 budget).

Pure Python: no database, no network, no LLM call. Callers
pass in events and drafts they loaded; this module only
reads them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .dial import (
    STAGE_DO_AND_SHOW,
    STAGE_DO_SILENTLY,
    STAGE_PROPOSE,
    AutonomyDial,
)

__all__ = [
    "CEILINGS",
    "CEILING_ORDER",
    "DISPLAY_AREAS",
    "GATE_KIND_TO_AREA",
    "SUGGESTION_THRESHOLD",
    "AutonomyControls",
    "CeilingRefusedError",
    "Signal",
    "area_by_key",
    "signals_from_drafts",
    "signals_from_events",
    "streaks_from_signals",
    "suggestion_for",
]

#: Consecutive accepted drafts in a row that earn a raise
#: suggestion for one area (plan 12.7: "10 accepted drafts in
#: a row").
SUGGESTION_THRESHOLD = 10

#: The five areas the PWA shows (plan 12.7), in display order.
#: ``locked`` means the ceiling cannot be raised at all.
DISPLAY_AREAS = (
    {
        "area": "money",
        "label": "Money",
        "locked": True,
        "description": "Payments, billing, and purchases. Always asks first.",
    },
    {
        "area": "messaging",
        "label": "Outgoing messages",
        "locked": False,
        "description": "Sending, sharing, or posting to another person.",
    },
    {
        "area": "files",
        "label": "Delete",
        "locked": False,
        "description": "Deleting or changing files and system state.",
    },
    {
        "area": "scheduling",
        "label": "Schedule",
        "locked": False,
        "description": "Calendar events and reminders.",
    },
    {
        "area": "shopping",
        "label": "Shopping",
        "locked": False,
        "description": (
            "Finding, comparing, and drafting a purchase. The payment itself still asks first."
        ),
    },
)

#: Ceiling name (what the user sees) -> dial stage.
CEILINGS = {
    "ask_first": STAGE_PROPOSE,
    "balanced": STAGE_DO_AND_SHOW,
    "autonomous": STAGE_DO_SILENTLY,
}

#: Ceiling names in order of increasing autonomy.
CEILING_ORDER = ("ask_first", "balanced", "autonomous")

#: Gate approval kind -> dial area (``puspa.policy.gate.KINDS``).
#: ``deploy`` maps to ``system``, which has no shown area, so it
#: is left out on purpose: a deploy approval never feeds a streak
#: the user can see.
GATE_KIND_TO_AREA = {
    "send": "messaging",
    "create_event": "scheduling",
    "prepare_payment": "money",
    "delete": "files",
}

_DISPLAY_KEYS = tuple(entry["area"] for entry in DISPLAY_AREAS)

#: Draft statuses that count as "the user accepted this draft".
_ACCEPTED_DRAFT_STATUSES = ("approved", "sent")

#: Draft kinds: every draft kind is an outgoing message, so all
#: of them feed the messaging streak.
_DRAFT_KINDS = ("message", "document", "email")


class CeilingRefusedError(ValueError):
    """A ceiling change the controls refuse.

    ``reason`` is ``"locked"`` (the area cannot be raised at
    all, e.g. Money) or ``"needs_evidence"`` (the raise needs
    a suggestion first: ten accepted drafts in a row).
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Signal:
    """One piece of streak evidence: accepted or not, in one area.

    ``at`` orders signals from both sources (ledger events and
    drafts) into one stream; ``accepted`` is True when the user
    accepted the draft or approval, False when they rejected
    or corrected it.
    """

    at: datetime
    area: str
    accepted: bool


def area_by_key(key: str) -> dict | None:
    """The display-area entry for ``key``, or None when not shown."""
    for entry in DISPLAY_AREAS:
        if entry["area"] == key:
            return dict(entry)
    return None


def _as_utc(moment: datetime | None) -> datetime | None:
    """A datetime as UTC (naive values are read as UTC)."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _event_area(event: dict, request_kinds: dict[str, str]) -> str | None:
    """The dial area one ledger event belongs to, or None.

    The event's own ``kind`` wins; otherwise the ``request_id``
    is matched against the ``approval_requested`` events seen
    earlier in the walk. Events from flows that record neither
    (the PWA's own approval endpoint records only a request
    id) cannot be attributed, and return None.
    """
    content = event.get("content") or {}
    if not isinstance(content, dict):
        return None
    kind = content.get("kind")
    if isinstance(kind, str) and kind in GATE_KIND_TO_AREA:
        return GATE_KIND_TO_AREA[kind]
    request_id = content.get("request_id")
    if isinstance(request_id, str):
        return request_kinds.get(request_id)
    return None


def signals_from_events(events) -> list[Signal]:
    """Streak signals from ledger events, in the order given.

    Callers pass events oldest first (the ledger returns them
    that way). ``approval_requested`` events build the
    request-id -> area map; ``approval_granted`` adds an
    accepted signal, ``approval_rejected`` a rejected one, and
    ``false_report`` a rejected signal for every shown area.
    A rejection that cannot be attributed to one area rejects
    in every shown area: an unattributed correction must not
    leave any "in a row" claim standing.
    """
    signals: list[Signal] = []
    request_kinds: dict[str, str] = {}
    for event in events:
        event_type = event.get("type")
        at = _as_utc(event.get("created_at"))
        if at is None:
            continue
        if event_type == "approval_requested":
            content = event.get("content") or {}
            kind = content.get("kind") if isinstance(content, dict) else None
            request_id = content.get("request_id") if isinstance(content, dict) else None
            if (
                isinstance(kind, str)
                and kind in GATE_KIND_TO_AREA
                and isinstance(request_id, str)
                and request_id
            ):
                request_kinds[request_id] = GATE_KIND_TO_AREA[kind]
            continue
        if event_type == "approval_granted":
            area = _event_area(event, request_kinds)
            if area is not None:
                signals.append(Signal(at=at, area=area, accepted=True))
        elif event_type == "approval_rejected":
            area = _event_area(event, request_kinds)
            if area is not None:
                signals.append(Signal(at=at, area=area, accepted=False))
            else:
                signals.extend(Signal(at=at, area=key, accepted=False) for key in _DISPLAY_KEYS)
        elif event_type == "false_report":
            signals.extend(Signal(at=at, area=key, accepted=False) for key in _DISPLAY_KEYS)
    return signals


def signals_from_drafts(drafts) -> list[Signal]:
    """Streak signals from drafts, in the order given.

    An approved or sent draft is an accepted draft in
    ``messaging``. A draft back at ``draft`` status with a
    newer ``updated_at`` than ``created_at`` was revised by
    the user instead of accepted: a correction, so the
    messaging streak resets. A draft still waiting for a
    decision (``draft`` status, untouched since creation)
    adds no signal either way.
    """
    signals: list[Signal] = []
    for draft in drafts:
        kind = getattr(draft, "kind", None)
        status = getattr(draft, "status", None)
        if kind not in _DRAFT_KINDS:
            continue
        created_at = _as_utc(getattr(draft, "created_at", None))
        updated_at = _as_utc(getattr(draft, "updated_at", None))
        if status in _ACCEPTED_DRAFT_STATUSES:
            at = updated_at
            if status == "sent":
                at = _as_utc(getattr(draft, "sent_at", None)) or updated_at
            if at is not None:
                signals.append(Signal(at=at, area="messaging", accepted=True))
        elif (
            status == "draft"
            and created_at is not None
            and updated_at is not None
            and updated_at > created_at
        ):
            signals.append(Signal(at=updated_at, area="messaging", accepted=False))
    return signals


def streaks_from_signals(signals) -> dict[str, int]:
    """Consecutive accepted signals per shown area.

    Signals are ordered by time first, so callers may mix
    ledger and draft signals freely. A rejected signal resets
    its area to zero; an accepted one adds one. Areas with no
    signal report zero: no evidence, no streak.
    """
    streaks = {key: 0 for key in _DISPLAY_KEYS}
    for signal in sorted(signals, key=lambda item: item.at):
        if signal.area not in streaks:
            continue
        if signal.accepted:
            streaks[signal.area] += 1
        else:
            streaks[signal.area] = 0
    return streaks


def suggestion_for(area: str, streak: int, ceiling: str | None) -> dict | None:
    """The raise suggestion for one area, or None.

    A suggestion exists only when the area is not locked, its
    ceiling is not already Autonomous, and its streak reached
    the threshold. The suggestion names the next ceiling and
    the threshold in plain words, so the UI can show both the
    offer and what earned it.
    """
    entry = area_by_key(area)
    if entry is None:
        raise ValueError(f"unknown display area: {area!r}")
    if entry["locked"]:
        return None
    current = ceiling or "ask_first"
    if current not in CEILING_ORDER:
        raise ValueError(f"unknown ceiling: {current!r}")
    position = CEILING_ORDER.index(current)
    if position >= len(CEILING_ORDER) - 1:
        return None
    if streak < SUGGESTION_THRESHOLD:
        return None
    next_ceiling = CEILING_ORDER[position + 1]
    return {
        "area": area,
        "from_ceiling": current,
        "to_ceiling": next_ceiling,
        "threshold": SUGGESTION_THRESHOLD,
        "streak": streak,
        "threshold_text": f"{SUGGESTION_THRESHOLD} accepted drafts in a row",
    }


class AutonomyControls:
    """The P-02 dial controls over one :class:`AutonomyDial`.

    One instance serves one process lifetime (the PWA app owns
    one). Creating it sets every shown area's ceiling to Ask
    first on the dial; the user raises a ceiling only through
    :meth:`set_ceiling`, which enforces the evidence rule.
    """

    def __init__(self, dial: AutonomyDial) -> None:
        """Create the controls over ``dial`` (ceilings to Ask first)."""
        self.dial = dial
        for entry in DISPLAY_AREAS:
            dial.set_ceiling(entry["area"], STAGE_PROPOSE)

    # -- reads ---------------------------------------------------------
    def area_state(self, area: str, *, streak: int = 0) -> dict:
        """One shown area's full control state as a JSON-safe dict."""
        entry = area_by_key(area)
        if entry is None:
            raise ValueError(f"unknown display area: {area!r}")
        decision = self.dial.advise(area)
        ceiling = self.dial.ceiling(area) or STAGE_PROPOSE
        ceiling_name = next(name for name, stage in CEILINGS.items() if stage == ceiling)
        return {
            "area": area,
            "label": entry["label"],
            "locked": entry["locked"],
            "description": entry["description"],
            "score": round(self.dial.score(area), 4),
            "stage": self.dial.stage(area),
            "effective_stage": decision.stage,
            "ceiling": ceiling_name,
            "streak": streak,
            "threshold": SUGGESTION_THRESHOLD,
            "threshold_text": f"{SUGGESTION_THRESHOLD} accepted drafts in a row",
            "suggestion": suggestion_for(area, streak, ceiling_name),
        }

    def state(self, signals=()) -> dict:
        """Every shown area's control state, plus the threshold."""
        streaks = streaks_from_signals(signals)
        return {
            "areas": [
                self.area_state(entry["area"], streak=streaks[entry["area"]])
                for entry in DISPLAY_AREAS
            ],
            "threshold": SUGGESTION_THRESHOLD,
            "threshold_text": f"{SUGGESTION_THRESHOLD} accepted drafts in a row",
        }

    # -- writes --------------------------------------------------------
    def set_ceiling(self, area: str, ceiling: str, signals=()) -> dict:
        """Move one area's ceiling to ``ceiling``.

        ``ceiling`` is ``"ask_first"`` / ``"balanced"`` /
        ``"autonomous"``. Lowering is always allowed. Raising
        moves one step at a time and needs an active
        suggestion (the area's streak reached the threshold),
        otherwise :class:`CeilingRefusedError` with reason
        ``"needs_evidence"``. Money refuses any raise with
        reason ``"locked"``. Unknown areas or ceilings raise
        ``ValueError``. Returns the area's state after the move.
        """
        entry = area_by_key(area)
        if entry is None:
            raise ValueError(f"unknown display area: {area!r}")
        if ceiling not in CEILINGS:
            raise ValueError(f"unknown ceiling: {ceiling!r}")
        if entry["locked"] and ceiling != "ask_first":
            raise CeilingRefusedError(
                f"{entry['label']} is locked to Ask first and cannot be raised",
                reason="locked",
            )
        current = self.dial.ceiling(area) or STAGE_PROPOSE
        current_name = next(name for name, stage in CEILINGS.items() if stage == current)
        if CEILING_ORDER.index(ceiling) > CEILING_ORDER.index(current_name):
            streaks = streaks_from_signals(signals)
            suggestion = suggestion_for(area, streaks[area], current_name)
            if suggestion is None or suggestion["to_ceiling"] != ceiling:
                raise CeilingRefusedError(
                    f"{entry['label']} can only rise after "
                    f"{SUGGESTION_THRESHOLD} accepted drafts in a row "
                    f"(streak is {streaks[area]})",
                    reason="needs_evidence",
                )
        self.dial.set_ceiling(area, CEILINGS[ceiling])
        streaks = streaks_from_signals(signals)
        return self.area_state(area, streak=streaks[area])
