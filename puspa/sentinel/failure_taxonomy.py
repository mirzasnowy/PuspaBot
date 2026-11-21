"""Failure taxonomy (Kosmos Brain v2, Phase 3 "Self-repair", V2-16).

Every failure follows one path: detect it, classify the cause,
apply the repair for that class, and add a regression case so
the same failure cannot return silently. This module is the
classify step of that path.

Five detection signals (``docs/kosmos-brain-v2.md``, Self-repair)
-----------------------------------------------------------------
1. ``tool_error`` — a tool raised or returned an error.
2. ``postcondition_failure`` — the live result does not show the
   intended change (the V2-13 compiler's replay verdict).
3. ``prediction_error`` — the forecast and the observed outcome
   differ beyond a threshold (K19; build against the K7/K8
   signals until V2-15 lands).
4. ``silent_human`` — the user corrects, repeats, edits, or
   ignores without an explicit error: an undo, an edited result,
   an ignored suggestion, a repeated question.
5. ``late_failure`` — the K8 outcome watcher found the outcome
   changed after the task was marked done.

Six failure classes, each with its repair (the doc's table)
-----------------------------------------------------------
- ``wrong_intent`` — update the intent model and the person
  model; add the correction to pinned memory.
- ``missing_knowledge`` — research or ingest, then close the
  entry in the ignorance map.
- ``bad_plan`` — re-plan in deep mode with the failure context;
  store the lesson.
- ``tool_misuse`` — patch the skill and re-run its regression
  case.
- ``environment_changed`` — refresh world state with a new
  valid time (K3).
- ``repeated_model_error`` — move the step up a model tier or
  add an independent verifier.

How a signal becomes a class
----------------------------
The signal alone does not fix the class: the event's context
disambiguates it, in this priority order.

1. Two automatic repair attempts already spent
   (``repair_attempts >= 2``) end in escalation, whatever the
   signal — the doc's loop rule. The class is still classified,
   so the escalation says what failed and what was tried.
2. ``silent_human`` is always ``wrong_intent``: a correction,
   repeat, or ignored suggestion means the intent was
   misunderstood. The repair asks the user (clarify, then pin
   the correction) instead of silently updating the model.
3. ``late_failure`` is always ``environment_changed``: the
   world moved after "done". The repair refreshes world state.
4. ``prediction_error`` with ``knowledge_gap`` is
   ``missing_knowledge`` (repair: research); with
   ``world_changed`` it is ``environment_changed`` (repair:
   refresh world state); otherwise it is ``wrong_intent``
   (repair: update the intent model).
5. ``postcondition_failure`` on a compiled skill (``skill_id``
   in the context) is ``tool_misuse`` (repair: repair the
   skill); without a skill it is ``bad_plan`` (repair: re-plan).
6. ``tool_error`` repeated (``repeat_count >= 2``) is
   ``repeated_model_error`` (repair: move the step up a model
   tier); otherwise it is ``tool_misuse`` and the first repair
   is a retry — a transient tool error is retried before the
   skill is patched.

``classify`` is pure: no database, no network, no model call.
The K8 seam is :func:`from_late_failure`, which builds the
event from an :class:`OutcomeWatcher
<puspa.sentinel.outcome_watcher.OutcomeWatcher>` late-failure
result, so a watched outcome that changed after "done" flows
straight into the taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

__all__ = [
    "MAX_AUTO_REPAIR_ATTEMPTS",
    "REPAIR_BY_CLASS",
    "Classification",
    "FailureClass",
    "FailureEvent",
    "FailureSignal",
    "RepairAction",
    "classify",
    "from_late_failure",
]

#: Automatic repair attempts allowed per task before escalation
#: (the doc's loop rule: two failed repairs end in escalation).
MAX_AUTO_REPAIR_ATTEMPTS = 2

#: A repeated tool error is a model error, not a one-off slip,
#: once it has failed this many times.
REPEATED_ERROR_COUNT = 2


class FailureSignal(StrEnum):
    """One of the five detection signals."""

    TOOL_ERROR = "tool_error"
    POSTCONDITION_FAILURE = "postcondition_failure"
    PREDICTION_ERROR = "prediction_error"
    SILENT_HUMAN = "silent_human"
    LATE_FAILURE = "late_failure"


class FailureClass(StrEnum):
    """One of the six failure classes from the doc's table."""

    WRONG_INTENT = "wrong_intent"
    MISSING_KNOWLEDGE = "missing_knowledge"
    BAD_PLAN = "bad_plan"
    TOOL_MISUSE = "tool_misuse"
    ENVIRONMENT_CHANGED = "environment_changed"
    REPEATED_MODEL_ERROR = "repeated_model_error"


class RepairAction(StrEnum):
    """A concrete repair to attempt for a classified failure."""

    RETRY = "retry"
    REPLAN = "replan"
    REPAIR_SKILL = "repair_skill"
    ASK_USER = "ask_user"
    ESCALATE = "escalate"
    REFRESH_WORLD_STATE = "refresh_world_state"
    RESEARCH = "research"
    UPDATE_INTENT_MODEL = "update_intent_model"
    MOVE_MODEL_TIER = "move_model_tier"


#: The doc-table default repair for each failure class.
REPAIR_BY_CLASS: dict[FailureClass, RepairAction] = {
    FailureClass.WRONG_INTENT: RepairAction.UPDATE_INTENT_MODEL,
    FailureClass.MISSING_KNOWLEDGE: RepairAction.RESEARCH,
    FailureClass.BAD_PLAN: RepairAction.REPLAN,
    FailureClass.TOOL_MISUSE: RepairAction.REPAIR_SKILL,
    FailureClass.ENVIRONMENT_CHANGED: RepairAction.REFRESH_WORLD_STATE,
    FailureClass.REPEATED_MODEL_ERROR: RepairAction.MOVE_MODEL_TIER,
}


@dataclass(frozen=True)
class FailureEvent:
    """One detected failure, with the context that disambiguates it.

    ``signal`` is one of the five detection signals (a
    :class:`FailureSignal` or its string value). ``task`` names
    what was being done. ``detail`` is the plain description of
    what failed. ``context`` carries the disambiguating facts:
    ``tool`` (the tool that errored), ``transient`` (a timeout
    or rate limit, worth one retry), ``repeat_count`` (how many
    times this failure already happened), ``repair_attempts``
    (automatic repairs already spent on this task), ``skill_id``
    (a compiled skill was replaying), ``knowledge_gap`` (the
    ignorance map has an open entry for this), ``world_changed``
    (fresh data shows the world moved), ``human_action`` (what
    the user silently did: ``"undo"``, ``"edit"``,
    ``"ignore"``, ``"repeat"``), ``predicted`` / ``observed``
    (the forecast against what the world showed).
    """

    signal: FailureSignal | str
    task: str
    detail: str = ""
    context: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.signal, str):
            try:
                object.__setattr__(self, "signal", FailureSignal(self.signal))
            except ValueError:
                raise ValueError(f"unknown failure signal: {self.signal!r}") from None
        if not isinstance(self.signal, FailureSignal):
            raise ValueError(f"unknown failure signal: {self.signal!r}")
        if not isinstance(self.task, str) or not self.task.strip():
            raise ValueError("a failure event needs a non-empty task")
        if not isinstance(self.context, dict):
            raise ValueError("a failure event's context must be a dict")


@dataclass(frozen=True)
class Classification:
    """What :func:`classify` decided for one failure event.

    ``failure_class`` is the diagnosed cause, ``repair`` is the
    concrete action to attempt next, ``reason`` says why in one
    plain sentence. ``escalated`` is True only when the repair
    is ``escalate`` (the attempt budget is spent); the class is
    still set, so the escalation carries the diagnosis.
    """

    event: FailureEvent
    failure_class: FailureClass
    repair: RepairAction
    reason: str
    escalated: bool = False

    @property
    def signal(self) -> FailureSignal:
        """The signal this classification came from."""
        # __post_init__ normalises a plain string to the enum.
        return cast(FailureSignal, self.event.signal)

    def to_dict(self) -> dict:
        """JSON-safe dict of this classification (ledger shape)."""
        return {
            "signal": cast(FailureSignal, self.event.signal).value,
            "task": self.event.task,
            "detail": self.event.detail,
            "failure_class": self.failure_class.value,
            "repair": self.repair.value,
            "reason": self.reason,
            "escalated": self.escalated,
        }


def _classify_signal(event: FailureEvent) -> tuple[FailureClass, RepairAction, str]:
    """The signal-plus-context rules, in priority order."""
    context = event.context
    signal = event.signal
    if signal is FailureSignal.SILENT_HUMAN:
        human_action = context.get("human_action") or "corrected"
        return (
            FailureClass.WRONG_INTENT,
            RepairAction.ASK_USER,
            (
                f"the user {human_action} without an explicit error, "
                "so the intent was misunderstood; ask to clarify, then pin the correction"
            ),
        )
    if signal is FailureSignal.LATE_FAILURE:
        return (
            FailureClass.ENVIRONMENT_CHANGED,
            RepairAction.REFRESH_WORLD_STATE,
            (
                "the outcome changed after the task was marked done, "
                "so the world moved; refresh world state with a new valid time"
            ),
        )
    if signal is FailureSignal.PREDICTION_ERROR:
        if context.get("knowledge_gap"):
            return (
                FailureClass.MISSING_KNOWLEDGE,
                RepairAction.RESEARCH,
                (
                    "the forecast missed because knowledge was missing; "
                    "research or ingest, then close the ignorance-map entry"
                ),
            )
        if context.get("world_changed"):
            return (
                FailureClass.ENVIRONMENT_CHANGED,
                RepairAction.REFRESH_WORLD_STATE,
                (
                    "the forecast missed because the world moved; "
                    "refresh world state with a new valid time"
                ),
            )
        return (
            FailureClass.WRONG_INTENT,
            RepairAction.UPDATE_INTENT_MODEL,
            (
                "the forecast missed what the user wanted; "
                "update the intent model and the person model"
            ),
        )
    if signal is FailureSignal.POSTCONDITION_FAILURE:
        skill_id = context.get("skill_id")
        if skill_id:
            return (
                FailureClass.TOOL_MISUSE,
                RepairAction.REPAIR_SKILL,
                (
                    f"the compiled skill {skill_id!r} broke its postcondition; "
                    "patch the skill and re-run its regression case"
                ),
            )
        return (
            FailureClass.BAD_PLAN,
            RepairAction.REPLAN,
            (
                "the live result does not show the intended change and no "
                "skill was replaying; re-plan in deep mode with the failure context"
            ),
        )
    # Tool error: repeated is a model error, otherwise retry first.
    repeat_count = context.get("repeat_count") or 0
    if isinstance(repeat_count, int) and repeat_count >= REPEATED_ERROR_COUNT:
        return (
            FailureClass.REPEATED_MODEL_ERROR,
            RepairAction.MOVE_MODEL_TIER,
            (
                f"the tool failed {repeat_count} times in a row; "
                "move the step up a model tier or add an independent verifier"
            ),
        )
    tool = context.get("tool")
    tool_note = f"tool {tool!r} " if tool else "the tool "
    return (
        FailureClass.TOOL_MISUSE,
        RepairAction.RETRY,
        f"{tool_note}errored; retry once before patching the skill",
    )


def classify(event: FailureEvent) -> Classification:
    """Classify one failure event into a class and a repair to attempt.

    The class and repair come from the signal-plus-context
    rules; when the task already spent its two automatic repair
    attempts, the repair is ``escalate`` instead — the class is
    still classified, so the escalation carries the diagnosis
    and what was tried.
    """
    failure_class, repair, reason = _classify_signal(event)
    attempts = event.context.get("repair_attempts") or 0
    if isinstance(attempts, int) and attempts >= MAX_AUTO_REPAIR_ATTEMPTS:
        return Classification(
            event=event,
            failure_class=failure_class,
            repair=RepairAction.ESCALATE,
            reason=(
                f"{reason}; two automatic repair attempts already spent, "
                "so escalate with the diagnosis instead of trying again"
            ),
            escalated=True,
        )
    return Classification(event=event, failure_class=failure_class, repair=repair, reason=reason)


def from_late_failure(result, *, task: str | None = None) -> FailureEvent:
    """Build a failure event from a K8 late-failure check result.

    ``result`` is a :class:`CheckResult
    <puspa.sentinel.outcome_watcher.CheckResult>` from
    :meth:`OutcomeWatcher.check_due
    <puspa.sentinel.outcome_watcher.OutcomeWatcher.check_due>`
    (a dict with the same keys works too). Anything that is not
    a late failure raises ValueError — only a changed-after-done
    outcome is this signal.
    """
    if isinstance(result, dict):
        status = result.get("status")
        watched = result.get("watched") or {}
        fresh = result.get("fresh_observed")
    else:
        status = getattr(result, "status", None)
        watched = getattr(result, "watched", None)
        fresh = getattr(result, "fresh_observed", None)
    if status != "late_failure":
        raise ValueError(f"a late-failure event needs a late_failure result, got {status!r}")
    if isinstance(watched, dict):
        watched_task = watched.get("task")
        observed = watched.get("observed")
        outcome_id = watched.get("outcome_id")
    else:
        watched_task = getattr(watched, "task", None)
        observed = getattr(watched, "observed", None)
        outcome_id = getattr(watched, "outcome_id", None)
    resolved_task = task or watched_task or "unknown task"
    return FailureEvent(
        signal=FailureSignal.LATE_FAILURE,
        task=str(resolved_task),
        detail=(
            f"outcome {outcome_id} failed after it was marked done: "
            f"recorded {observed!r}, recheck found {fresh!r}"
        ),
        context={
            "outcome_id": outcome_id,
            "original_observed": observed,
            "fresh_observed": fresh,
            "world_changed": True,
        },
    )
