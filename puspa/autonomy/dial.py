"""Per-area autonomy dial (Kosmos K11).

One dial per action area, not one global dial, and the dial moves
with prediction calibration: it rises slowly while predictions in
that area keep coming true and falls fast when they do not. One
serious failure in an irreversible area drops that area's dial to
the lowest stage at once.

The rules (also written in ``docs/autonomy-dial.md`` — the doc is
the source of truth, this module is the enforcement):

1. Areas. Six, taken from the action kinds the system already
   recognises (the G4 taxonomy in ``puspa.policy.approvals`` plus
   the scheduling tools the turn loop dispatches):

   - ``money`` — payments, billing, purchases. Irreversible.
   - ``messaging`` — sending, sharing, or posting to another
     person. Irreversible: a sent message cannot be unsent.
   - ``files`` — deleting or changing files and system state.
     Irreversible: a delete cannot be undone by the agent.
   - ``scheduling`` — calendar events and reminders. Reversible.
   - ``system`` — installing skills and changing settings.
     Reversible.
   - ``accounts`` — credential changes. Irreversible, and capped:
     the dial can never promote this area past ``propose``
     (the G4 rule that credential changes are never approved
     by an answer still stands on top).

2. Stages, from the dial score (0..1):

   - ``propose`` — score below 0.60. The action does not run;
     the user is asked first.
   - ``do_and_show`` — score from 0.60 up to 0.85. The action
     runs, and its effect is shown to the user.
   - ``do_silently`` — score 0.85 or above. The action runs
     without surfacing it to the user.

3. Movement. Every area starts at 0.50 (``propose``): trust is
   earned, never assumed.

   - A correct outcome closes 5% of the remaining gap to 1.0:
     ``score += (1 - score) * 0.05``. From the 0.50 start that
     is 5 consecutive correct outcomes to reach ``do_and_show``
     and 24 to reach ``do_silently``. The rise is slow on
     purpose: one lucky streak must not buy silent autonomy.
   - An ordinary wrong outcome drops the score by 0.15, floored
     at 0.0. From 0.85 one wrong outcome falls back to
     ``do_and_show`` (0.70); two fall back to ``propose``
     (0.55). The fall is fast on purpose.
   - A serious failure (an action that failed harmfully, or an
     irreversible action that went wrong) in an irreversible
     area drops the score to 0.0 at once — the lowest stage,
     immediately, with no glide. Rebuilding from 0.0 takes 18
     consecutive correct outcomes to reach ``do_and_show`` and
     37 to reach ``do_silently``.
   - A serious failure in a reversible area drops the score by
     0.30, floored at 0.0: a hard fall, but not a reset, because
     the damage can be undone.
   - ``accounts`` is capped at 0.59, so correct outcomes there
     are recorded but the stage never leaves ``propose``.

4. Calibration source. The dial does NOT score predictions
   itself and is not a second calibration system. It aggregates
   outcomes that were already scored elsewhere, per area, through
   the single entry point :meth:`AutonomyDial.record_outcome`:

   - The world model scores predictions (``WorldModel.stats``:
     ``predictions`` / ``correct``, and the per-contact accuracy
     in ``AgentGoalModel``) and the learning loop scores user
     feedback (``record_feedback`` verdicts ``"correct"`` /
     ``"wrong"``). Callers that hold a scored outcome report it
     here with the area it belongs to.
   - The turn loop is the first such caller wired in: it reports
     each dispatched tool call's execution outcome (ran as
     expected, or errored) for the call's area. That is an
     execution-level signal, a proxy for prediction correctness,
     not the same thing — see the honest limit in
     ``docs/autonomy-dial.md``. The stronger feeder (the K8
     outcome watcher, which scores outcomes after the fact) is
     the follow-up; it feeds the same entry point.

   ``record_serious_failure`` is the separate entry point for
   harm: it is called by whoever knows an action failed
   seriously (the outcome watcher, a human report), never
   inferred from a plain tool error by this module.

5. No ledger event type is used here, on purpose: the closed
   set of 31 ledger event types (revisi 7) has no "dial moved"
   type, and adding a 32nd is forbidden. Dial moves are audited
   in the dial's own per-area history (``history`` / ``summary``),
   the same pattern as the K10 interruption budget's spend log.

6. Persistence, stated honestly: this dial is in-memory. A
   restart resets every area to the 0.50 start, so a restart
   can only make the agent more cautious, never bolder. That
   is the safe direction for the first version (the same
   in-memory choice as the K6 ignorance map and the K10
   budget). Persisting the per-area scores (a Postgres table +
   migration, the K3/K4/K5 pattern) is the follow-up when the
   dial needs to survive restarts.

Pure Python: no database, no network, no LLM call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from puspa.policy.approvals import classify_action

__all__ = [
    "AREAS",
    "INITIAL_SCORE",
    "IRREVERSIBLE_AREAS",
    "KIND_TO_AREA",
    "MAX_HISTORY_PER_AREA",
    "PROMOTE_TO_SHOW",
    "PROMOTE_TO_SILENT",
    "RISE_FRACTION",
    "SERIOUS_REVERSIBLE_DROP",
    "STAGE_DO_AND_SHOW",
    "STAGE_DO_SILENTLY",
    "STAGE_PROPOSE",
    "WRONG_DROP",
    "AreaState",
    "AutonomyDial",
    "DialDecision",
    "DialRecord",
    "area_for_tool",
    "dial_ask_payload",
]

#: The six action areas. See the module docstring for what each
#: covers and which are irreversible.
AREAS = ("money", "messaging", "files", "scheduling", "system", "accounts")

#: Areas whose effects cannot be undone by the agent. A serious
#: failure here drops the dial to 0.0 at once.
IRREVERSIBLE_AREAS = frozenset({"money", "messaging", "files", "accounts"})

#: Stages, in order of increasing autonomy.
STAGE_PROPOSE = "propose"
STAGE_DO_AND_SHOW = "do_and_show"
STAGE_DO_SILENTLY = "do_silently"

#: Score every area starts at: propose, trust earned not assumed.
INITIAL_SCORE = 0.50

#: Score at or above which the stage becomes do-and-show.
PROMOTE_TO_SHOW = 0.60

#: Score at or above which the stage becomes do-silently.
PROMOTE_TO_SILENT = 0.85

#: Fraction of the remaining gap to 1.0 one correct outcome closes.
RISE_FRACTION = 0.05

#: Score lost on one ordinary wrong outcome.
WRONG_DROP = 0.15

#: Score lost on a serious failure in a reversible area.
SERIOUS_REVERSIBLE_DROP = 0.30

#: Highest score the accounts area may reach: it never leaves propose.
ACCOUNTS_MAX_SCORE = 0.59

#: Dial history entries kept per area; older entries are dropped.
MAX_HISTORY_PER_AREA = 100

#: G4 taxonomy kind -> dial area. ``change_credentials`` maps to
#: ``accounts`` (capped at propose); ``install`` maps to
#: ``system`` (reversible: a skill can be uninstalled).
KIND_TO_AREA = {
    "pay": "money",
    "send_to_other": "messaging",
    "share": "messaging",
    "post": "messaging",
    "delete": "files",
    "change_credentials": "accounts",
    "install": "system",
}

#: Tool-name verbs that mean scheduling (the G4 taxonomy has no
#: scheduling kind, so these are matched on the tool name).
_SCHEDULING_VERBS = ("schedule", "remind", "reminder", "calendar")


def _tool_verb(tool_name: str) -> str:
    """First verb of a tool name: last dotted segment, first word."""
    name = str(tool_name or "").strip().lower().replace("-", "_").replace("/", ".")
    last = name.split(".")[-1] if name else ""
    return last.split("_")[0] if last else ""


def area_for_tool(tool_name: str, *, overrides: dict[str, str] | None = None) -> str | None:
    """The dial area for a tool name, or None when unknown.

    A caller override wins first. Otherwise the G4 taxonomy
    classifies the tool (tool verb / button signals) and the kind
    maps to an area; a tool the taxonomy does not recognise falls
    back to the scheduling verbs. An unknown tool returns None so
    the caller fails open — the dial never gates a tool it cannot
    place in an area.
    """
    if overrides:
        override = overrides.get(str(tool_name))
        if override is not None:
            if override not in AREAS:
                raise ValueError(f"unknown dial area in override: {override!r}")
            return override
    classification = classify_action(str(tool_name))
    if classification.kind is not None:
        return KIND_TO_AREA.get(classification.kind)
    normalized = str(tool_name or "").strip().lower()
    if "calendar" in normalized or _tool_verb(tool_name) in _SCHEDULING_VERBS:
        return "scheduling"
    return None


def _stage_for(score: float) -> str:
    """The stage a score belongs to."""
    if score >= PROMOTE_TO_SILENT:
        return STAGE_DO_SILENTLY
    if score >= PROMOTE_TO_SHOW:
        return STAGE_DO_AND_SHOW
    return STAGE_PROPOSE


def _max_score(area: str) -> float:
    """The highest score an area may reach (accounts is capped)."""
    return ACCOUNTS_MAX_SCORE if area == "accounts" else 1.0


@dataclass(frozen=True)
class DialDecision:
    """The verdict for one proposed action in one area.

    ``stage`` is ``"propose"``, ``"do_and_show"``, or
    ``"do_silently"``. ``run`` says whether the action may
    execute now (False only at ``propose``). ``show`` says
    whether the executed action's effect is shown to the user
    (True only at ``do_and_show``: at ``propose`` nothing runs
    so there is nothing to show, and at ``do_silently`` the
    action runs without surfacing it). ``score`` is the area's
    dial score at decision time; ``reason`` is one plain
    sentence naming the deciding factor.
    """

    area: str
    stage: str
    run: bool
    show: bool
    score: float
    reason: str

    def to_dict(self) -> dict:
        """JSON-safe dict of this decision."""
        return {
            "area": self.area,
            "stage": self.stage,
            "run": self.run,
            "show": self.show,
            "score": round(self.score, 4),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DialRecord:
    """One audited dial move: what happened and where it landed.

    ``event`` is ``"correct"``, ``"wrong"``, or
    ``"serious_failure"``. ``score_before`` / ``score_after``
    bracket the move; ``stage_after`` is the stage the area
    landed in. ``note`` is free text (a tool name, an outcome
    description), never shown to the user.
    """

    area: str
    event: str
    score_before: float
    score_after: float
    stage_after: str
    note: str = ""

    def to_dict(self) -> dict:
        """JSON-safe dict of this record."""
        return {
            "area": self.area,
            "event": self.event,
            "score_before": round(self.score_before, 4),
            "score_after": round(self.score_after, 4),
            "stage_after": self.stage_after,
            "note": self.note,
        }


@dataclass
class AreaState:
    """One area's dial: its score, its scored outcomes, its log."""

    area: str
    score: float = INITIAL_SCORE
    correct: int = 0
    wrong: int = 0
    serious_failures: int = 0
    history: list[DialRecord] = field(default_factory=list)

    @property
    def stage(self) -> str:
        """The stage this area is in right now."""
        return _stage_for(self.score)

    @property
    def accuracy(self) -> float | None:
        """Scored-outcome accuracy so far (None when none scored)."""
        total = self.correct + self.wrong
        return self.correct / total if total else None

    def to_dict(self) -> dict:
        """JSON-safe dict of this area's state."""
        accuracy = self.accuracy
        return {
            "area": self.area,
            "score": round(self.score, 4),
            "stage": self.stage,
            "correct": self.correct,
            "wrong": self.wrong,
            "serious_failures": self.serious_failures,
            "accuracy": round(accuracy, 4) if accuracy is not None else None,
            "history": [record.to_dict() for record in self.history],
        }


class AutonomyDial:
    """One autonomy dial per action area.

    One instance serves one user for one process lifetime (the
    turn loop owns one, or shares one passed in). All state is
    in-memory; see the module docstring for the honest restart
    limit. The dial never scores a prediction itself: every move
    comes from a scored outcome reported through
    :meth:`record_outcome` or :meth:`record_serious_failure`.
    """

    def __init__(self, *, initial_score: float = INITIAL_SCORE) -> None:
        """Create a dial.

        Raises ``ValueError`` for an initial score outside 0..1.
        Every area starts at that score.
        """
        if not 0.0 <= float(initial_score) <= 1.0:
            raise ValueError(f"initial score must be within 0..1, got {initial_score}")
        self.initial_score = float(initial_score)
        self._areas: dict[str, AreaState] = {
            area: AreaState(area=area, score=float(initial_score)) for area in AREAS
        }

    # -- reads ---------------------------------------------------------
    def _state(self, area: str) -> AreaState:
        try:
            return self._areas[area]
        except KeyError:
            raise ValueError(f"unknown dial area: {area!r}") from None

    def score(self, area: str) -> float:
        """The area's dial score right now."""
        return self._state(area).score

    def stage(self, area: str) -> str:
        """The area's stage right now."""
        return self._state(area).stage

    def accuracy(self, area: str) -> float | None:
        """The area's scored-outcome accuracy (None when none scored)."""
        return self._state(area).accuracy

    def advise(self, area: str) -> DialDecision:
        """Decide what an action in ``area`` may do right now.

        ``propose`` means ask the user first (the action does not
        run). ``do_and_show`` means run it and show the effect.
        ``do_silently`` means run it without surfacing it.
        """
        state = self._state(area)
        stage = state.stage
        if stage == STAGE_PROPOSE:
            return DialDecision(
                area=area,
                stage=stage,
                run=False,
                show=False,
                score=state.score,
                reason=(
                    f"{area} is at propose (score {state.score:.2f}, below "
                    f"{PROMOTE_TO_SHOW}): the action needs the user's go-ahead first"
                ),
            )
        if stage == STAGE_DO_AND_SHOW:
            return DialDecision(
                area=area,
                stage=stage,
                run=True,
                show=True,
                score=state.score,
                reason=(
                    f"{area} is at do-and-show (score {state.score:.2f}): "
                    "the action runs and its effect is shown"
                ),
            )
        return DialDecision(
            area=area,
            stage=stage,
            run=True,
            show=False,
            score=state.score,
            reason=(
                f"{area} is at do-silently (score {state.score:.2f}): "
                "the action runs without surfacing it"
            ),
        )

    def state(self, area: str) -> dict:
        """One area's full state as a JSON-safe dict."""
        return self._state(area).to_dict()

    def history(self, area: str) -> list[DialRecord]:
        """The area's audit log: every dial move, oldest first.

        Returns a copy.
        """
        return list(self._state(area).history)

    def summary(self) -> dict:
        """Every area's dial in numbers."""
        return {
            "areas": {area: self._state(area).to_dict() for area in AREAS},
        }

    # -- writes --------------------------------------------------------
    def _record(self, state: AreaState, event: str, before: float, note: str = "") -> None:
        """Append one move to the area's audit log, capped."""
        state.history.append(
            DialRecord(
                area=state.area,
                event=event,
                score_before=before,
                score_after=state.score,
                stage_after=state.stage,
                note=note,
            )
        )
        if len(state.history) > MAX_HISTORY_PER_AREA:
            del state.history[: len(state.history) - MAX_HISTORY_PER_AREA]

    def record_outcome(self, area: str, correct: bool, *, note: str = "") -> DialDecision:
        """Report one already-scored outcome in ``area``.

        ``correct`` is True when the prediction / action came out
        as expected, False when it did not. A correct outcome
        closes 5% of the remaining gap to 1.0 (slow rise); a
        wrong outcome drops the score by 0.15 (fast fall). The
        score is floored at 0.0 and capped per area (accounts
        never passes 0.59). Returns the decision the area lands
        in after the move.
        """
        state = self._state(area)
        before = state.score
        if correct:
            state.correct += 1
            state.score = min(
                _max_score(area),
                state.score + (1.0 - state.score) * RISE_FRACTION,
            )
            self._record(state, "correct", before, note)
        else:
            state.wrong += 1
            state.score = max(0.0, state.score - WRONG_DROP)
            self._record(state, "wrong", before, note)
        return self.advise(area)

    def record_serious_failure(
        self, area: str, *, reversible: bool | None = None, note: str = ""
    ) -> DialDecision:
        """Report one serious failure in ``area``: the emergency drop.

        A serious failure is an action that failed harmfully, or
        an irreversible action that went wrong — not a plain tool
        error (that is an ordinary wrong outcome). In an
        irreversible area the score drops to 0.0 at once: the
        lowest stage, immediately. In a reversible area the score
        drops by 0.30. ``reversible`` defaults to the area's own
        reversibility (``area`` in :data:`IRREVERSIBLE_AREAS`
        means irreversible). Returns the decision the area lands
        in after the drop.
        """
        state = self._state(area)
        if reversible is None:
            reversible = area not in IRREVERSIBLE_AREAS
        before = state.score
        state.serious_failures += 1
        if reversible:
            state.score = max(0.0, state.score - SERIOUS_REVERSIBLE_DROP)
        else:
            state.score = 0.0
        state.wrong += 1
        self._record(state, "serious_failure", before, note)
        return self.advise(area)

    def reset_area(self, area: str) -> DialDecision:
        """Forget one area's outcomes and put it back at the start.

        Returns the decision the area lands in (always propose
        at the initial score). This is an operator action, for
        tests and for a human resetting trust after a review —
        the agent itself never calls it on its own.
        """
        self._state(area)  # raises ValueError for an unknown area
        self._areas[area] = AreaState(area=area, score=self.initial_score)
        return self.advise(area)


def dial_ask_payload(decision: DialDecision, *, tool_name: str) -> str:
    """The tool-result text the loop returns when the dial says propose.

    A JSON object the model can read: the decision, the reason,
    the one question to put to the user, and the area and score
    behind it. The tool itself was NOT run — the payload says so,
    so the model must not report its effect as done.
    """
    if decision.stage != STAGE_PROPOSE:
        raise ValueError("dial_ask_payload is only for a 'propose' decision")
    payload: dict = {
        "autonomy_decision": "propose",
        "tool": tool_name,
        "area": decision.area,
        "score": round(decision.score, 4),
        "tool_was_run": False,
        "reason": decision.reason,
        "question": f"Shall I go ahead and run {tool_name}?",
    }
    return json.dumps(payload, ensure_ascii=False)
