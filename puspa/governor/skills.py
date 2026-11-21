"""Compiled skills: what the depth governor promotes a repeated deep task into.

A compiled skill is a deep plan that succeeded repeatedly, stored
as a procedure so the next similar task runs as reflex instead of
deep ("deep once, reflex afterwards", Kosmos Brain v2, Phase 2).
This module is only the storage shape and the regression check —
the governor decides *when* to compile (see
:mod:`puspa.governor.governor`); the full skill compiler that
generalises plans into reusable skills is a later item (V2-13)
and is NOT built here.

Two stores implement the same :class:`SkillStore` seam:

- :class:`InMemorySkillStore`: a dict, for unit tests and for
  callers with no user and no database.
- :class:`ProceduralSkillStore`: the durable store, an adapter
  over the memory procedural layer
  (:mod:`puspa.memory.procedural`, revision 7 section 3.3). A
  compiled skill is a trial procedure under the ``compiled:``
  service prefix, with its regression case kept in the
  procedure's ``required_checks`` as one ``{"kind":
  "regression", ...}`` entry, so the check travels with the
  skill and a later reader can re-run it. The procedural
  lifecycle still applies: trial until 3 successful runs by 2
  agents, broken after 2 consecutive failures, never used once
  broken.

A skill is only stored after its regression case passes (the
governor runs the regression on the candidate first and stores
it only on a pass), so a stored skill always has a passing
regression behind it. The regression itself replays the case's
steps through a caller-supplied ``step_runner`` — one step dict
in, one result dict out, the same seam the turn loop's tool
registry offers — and checks the last result against the
expected postcondition. A step result that is not a dict, or a
postcondition key that is missing or different, fails the
regression. A ``step_runner`` that raises is the caller's bug,
not a failed regression: the exception propagates, so a broken
runner is never mistaken for a broken skill.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from puspa.memory import procedural as procedural_store

__all__ = [
    "COMPILED_SERVICE_PREFIX",
    "CompiledSkill",
    "InMemorySkillStore",
    "ProceduralSkillStore",
    "RegressionCase",
    "RegressionResult",
    "SkillStore",
]

#: Service prefix for governor-compiled procedures in the
#: procedural store. The task key follows, so one task key maps
#: to exactly one service.
COMPILED_SERVICE_PREFIX = "compiled:"

#: Marker inside a procedure's ``required_checks`` that holds a
#: compiled skill's regression case.
_REGRESSION_CHECK_KIND = "regression"


def _is_steps(steps: object) -> bool:
    """True when ``steps`` is a non-empty list of dicts."""
    return (
        isinstance(steps, list) and len(steps) > 0 and all(isinstance(step, dict) for step in steps)
    )


@dataclass(frozen=True)
class RegressionCase:
    """The check a compiled skill must pass before it may be stored.

    ``steps`` are the replayed steps (usually the deep plan's own
    steps), each a ``{"tool": ..., "arguments": {...}}`` dict.
    ``expect`` is the postcondition: expected key/value pairs in
    the LAST step's result. An empty ``expect`` means only "the
    steps run to completion" is checked.
    """

    steps: list[dict]
    expect: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _is_steps(self.steps):
            raise ValueError("a regression case needs a non-empty list of step dicts")
        if not isinstance(self.expect, dict):
            raise ValueError("a regression case's expected postcondition must be a dict")

    @classmethod
    def from_steps(cls, steps: list[dict], expect: dict | None = None) -> RegressionCase:
        """Build a case that replays ``steps`` and checks ``expect``."""
        return cls(steps=list(steps), expect=dict(expect) if expect is not None else {})

    def to_dict(self) -> dict:
        """JSON-safe dict of this case (how it is stored)."""
        return {"steps": [dict(step) for step in self.steps], "expect": dict(self.expect)}

    @classmethod
    def from_dict(cls, data: dict) -> RegressionCase:
        """Rebuild a case from :meth:`to_dict` output; ValueError when malformed."""
        if not isinstance(data, dict):
            raise ValueError("a regression case must be a dict")
        return cls(steps=data.get("steps", []), expect=data.get("expect", {}))


@dataclass(frozen=True)
class RegressionResult:
    """The verdict of one regression run: passed or not, and why."""

    passed: bool
    steps_run: int
    detail: str
    last_result: dict | None = None

    def to_dict(self) -> dict:
        """JSON-safe dict of this verdict."""
        return {
            "passed": self.passed,
            "steps_run": self.steps_run,
            "detail": self.detail,
            "last_result": self.last_result,
        }


@dataclass
class CompiledSkill:
    """A deep plan compiled into a reflex skill.

    ``task_key`` names the task (one key, one skill). ``steps``
    are the plan's steps. ``regression`` is the case the skill
    must pass. ``skill_id`` is set by the store on save (None
    while the skill is still a candidate). ``status`` is
    ``"candidate"`` until stored, then the procedural status
    (``"trial"`` at first, see the module docstring).
    """

    task_key: str
    steps: list[dict]
    regression: RegressionCase
    skill_id: str | None = None
    status: str = "candidate"

    def __post_init__(self) -> None:
        if not self.task_key or not isinstance(self.task_key, str):
            raise ValueError("a compiled skill needs a non-empty task key")
        if not _is_steps(self.steps):
            raise ValueError("a compiled skill needs a non-empty list of step dicts")

    def run_regression(self, step_runner: Callable[[dict], dict]) -> RegressionResult:
        """Replay the regression case and check its postcondition.

        ``step_runner`` takes one step dict and returns one result
        dict. The verdict is ``passed`` only when every step ran
        and the last result carries every expected key with the
        expected value. A non-dict step result fails the run with
        the step named; a ``step_runner`` that raises propagates
        (see the module docstring).
        """
        last_result: dict | None = None
        for index, step in enumerate(self.regression.steps):
            result = step_runner(step)
            if not isinstance(result, dict):
                return RegressionResult(
                    passed=False,
                    steps_run=index,
                    detail=(
                        f"regression step {index} ({step.get('tool', '?')!r}) "
                        "returned a non-dict result, so the postcondition "
                        "cannot be checked"
                    ),
                    last_result=None,
                )
            last_result = result
        if last_result is None:  # pragma: no cover - guarded by __post_init__
            return RegressionResult(
                passed=False,
                steps_run=0,
                detail="regression case has no steps",
            )
        for key, expected in self.regression.expect.items():
            if key not in last_result:
                return RegressionResult(
                    passed=False,
                    steps_run=len(self.regression.steps),
                    detail=f"postcondition key {key!r} is missing from the last result",
                    last_result=last_result,
                )
            if last_result[key] != expected:
                return RegressionResult(
                    passed=False,
                    steps_run=len(self.regression.steps),
                    detail=(
                        f"postcondition key {key!r} is {last_result[key]!r}, expected {expected!r}"
                    ),
                    last_result=last_result,
                )
        return RegressionResult(
            passed=True,
            steps_run=len(self.regression.steps),
            detail="all regression steps ran and the postcondition holds",
            last_result=last_result,
        )

    def to_dict(self) -> dict:
        """JSON-safe dict of this skill."""
        return {
            "task_key": self.task_key,
            "skill_id": self.skill_id,
            "status": self.status,
            "steps": [dict(step) for step in self.steps],
            "regression": self.regression.to_dict(),
        }


class SkillStore(Protocol):
    """Where compiled skills live: save one, find one by task key."""

    def save(self, skill: CompiledSkill, *, agent_id: str | None = None) -> CompiledSkill:
        """Store ``skill`` and return it with its id set."""
        ...

    def find(self, task_key: str) -> CompiledSkill | None:
        """The stored skill for ``task_key``, or None when there is none."""
        ...


class InMemorySkillStore:
    """A dict-backed skill store (unit tests, no user, no database)."""

    def __init__(self) -> None:
        self._skills: dict[str, CompiledSkill] = {}

    def save(self, skill: CompiledSkill, *, agent_id: str | None = None) -> CompiledSkill:
        """Store ``skill`` under its task key (a re-save replaces it)."""
        stored = CompiledSkill(
            task_key=skill.task_key,
            steps=[dict(step) for step in skill.steps],
            regression=skill.regression,
            skill_id=f"skill:{skill.task_key}",
            status="trial",
        )
        self._skills[skill.task_key] = stored
        return stored

    def find(self, task_key: str) -> CompiledSkill | None:
        """The stored skill for ``task_key``, or None."""
        return self._skills.get(task_key)


class ProceduralSkillStore:
    """A durable skill store over the memory procedural layer.

    ``user_id`` owns the procedures; ``dsn`` is passed through
    to the procedural functions (None means "read it from the
    environment", exactly as the procedural layer does — no
    database name is hardcoded here).
    """

    def __init__(self, *, user_id: str, dsn: str | None = None) -> None:
        if not user_id or not isinstance(user_id, str):
            raise ValueError("a procedural skill store needs a non-empty user id")
        self._user_id = user_id
        self._dsn = dsn

    @staticmethod
    def _service(task_key: str) -> str:
        return f"{COMPILED_SERVICE_PREFIX}{task_key}"

    def save(self, skill: CompiledSkill, *, agent_id: str | None = None) -> CompiledSkill:
        """Store ``skill`` as a trial procedure; return it with its id set.

        The regression case travels in ``required_checks`` so a
        later reader can re-run it from the stored row alone.
        """
        check = {"kind": _REGRESSION_CHECK_KIND, **skill.regression.to_dict()}
        row = procedural_store.record_successful_task(
            self._user_id,
            self._service(skill.task_key),
            [dict(step) for step in skill.steps],
            agent_id=agent_id,
            required_checks=[check],
            dsn=self._dsn,
        )
        return CompiledSkill(
            task_key=skill.task_key,
            steps=[dict(step) for step in skill.steps],
            regression=skill.regression,
            skill_id=str(row["id"]),
            status=str(row["status"]),
        )

    def find(self, task_key: str) -> CompiledSkill | None:
        """The stored skill for ``task_key``, or None.

        A row whose ``required_checks`` hold no regression entry
        is not a governor-compiled skill and is skipped, so a
        procedure another path wrote under the same service prefix
        can never be mistaken for one.
        """
        service = self._service(task_key)
        for row in procedural_store.list_procedures(self._user_id, dsn=self._dsn):
            if row["service"] != service:
                continue
            regression = _regression_from_checks(row.get("required_checks"))
            if regression is None:
                continue
            steps = row["step"]
            if not _is_steps(steps):
                continue
            return CompiledSkill(
                task_key=task_key,
                steps=[dict(step) for step in steps],
                regression=regression,
                skill_id=str(row["id"]),
                status=str(row["status"]),
            )
        return None


def _regression_from_checks(required_checks: object) -> RegressionCase | None:
    """The regression case inside a procedure's checks, or None.

    Returns None when the checks are not a list, hold no
    ``"regression"`` entry, or hold one that does not parse —
    a malformed stored check is skipped, never trusted.
    """
    if not isinstance(required_checks, list):
        return None
    for check in required_checks:
        if not isinstance(check, dict) or check.get("kind") != _REGRESSION_CHECK_KIND:
            continue
        try:
            return RegressionCase.from_dict(check)
        except ValueError:
            return None
    return None
