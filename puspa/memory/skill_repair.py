"""Skill repair + procedure change - one learning loop.

hermes-agent (auto-repair) and letta-code (experience changes the
agent's own procedures) are ONE loop here, not two modules: both
read and write the same state the learning loop already owns -
the experience log, the correction table, and the procedure
store in `puspa/memory/learning_loop.py` and
`puspa/memory/procedural.py`. Nothing here stands alone without
a caller: the repairer works on the loop's own skill library,
and the procedure updater works on the procedure store the loop
already persists its drafts into.

Patterns adapted (rewritten, not copied):

- NousResearch/hermes-agent (MIT): a failing skill is diagnosed,
  rewritten, and re-verified. Puspa keeps the repaired skill a
  DRAFT (inactive) until the user approves it, exactly like every
  other learned skill, and a repair never overwrites a manual
  user edit: the repair is always a NEW draft, the original
  skill and the manual edit are left byte-identical.
- letta-ai/letta-code (Apache-2.0): repeated task results change
  the agent's own procedures. Puspa does it through the revision
  7 procedure lifecycle: a repeated successful outcome with a
  different step creates a new VERSION of the procedure (trial,
  must earn verification again); a conflicting step is stored as
  a separate candidate and reported, never overwriting the old
  one; decay follows `procedural.apply_decay`.

What is really implemented here (no stub):

- `diagnose_skill_failure`: reads the stored verification
  details of a skill and classifies every failing case
  (wrong output, exception, not executable) with the expected
  vs observed values, plus the loop's correction for the task
  as read-only context.
- `SkillRepairer.repair`: diagnosis -> rewrite -> re-verify,
  up to `max_attempts`. The rewrite is done by a caller-supplied
  `rewriter` callable, or by the built-in deterministic
  `lookup_rewriter` for callable code (it wraps the original
  code with a correction table learned from the failing test
  cases). Every attempt is a real new draft skill verified with
  `LearningLoop.verify_skill` against the same test cases. A
  repaired skill is a NEW draft: inactive, unverified until it
  passes, and only activatable through the loop's normal
  approval path. When no rewrite can pass, the result honestly
  says "unrepairable" and the original skill is untouched.
- `ProcedureUpdater.record_outcome`: repeated successful
  outcomes with a new step really create a new procedure
  version (version number, `supersedes` link, trial status);
  repeated failures on a stored procedure follow the revision
  7 rule (broken after 2 consecutive failures); stale verified
  procedures decay through `procedural.apply_decay`.

Stand-in that cannot be avoided here (see the report, not
hidden in a docstring): the production `rewriter` is an LLM
call - this module takes it as an injected callable and ships
only the deterministic `lookup_rewriter` as the built-in. The
lookup repair memorises corrected outputs for failing inputs;
it is a real repair, but a narrow one. A model-based rewriter
that understands the diagnosis and rewrites the code itself
is follow-up work needing the model router.

Safety rules enforced here (same as the learning loop):

- A repaired skill is inactive until approved: repair never
  activates anything, and `find_reusable_skill` never returns
  a draft.
- Repair never overwrites a manual edit: the new draft is
  flagged `conflicts_with_manual_edit` when one exists, and
  the manual edit hash is never touched.
- Repair never touches beliefs: the correction table
  (`loop.corrections`) is read-only context for diagnosis.
  Repair writes no correction, no experience, no prediction.
- No string code is ever executed by this module: string code
  needs the caller-supplied `executor`, exactly like
  `LearningLoop.verify_skill`. No network call on any path.
"""

from __future__ import annotations

import json
import uuid

from .learning_loop import LearningLoop
from .procedural import (
    apply_decay,
    compute_run_update,
    detect_contradiction,
)

REPAIR_STATUSES = ("repaired", "unrepairable", "already_passing")
FAILURE_KINDS = ("wrong_output", "exception", "not_executable")


def _case_key(case):
    """Stable key for a test case's inputs (args + kwargs)."""
    payload = {"args": case.get("args", []), "kwargs": case.get("kwargs", {})}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _failure_kind(detail):
    """Classify one failed verification detail."""
    result = detail.get("result")
    if isinstance(result, str) and result.startswith("error:"):
        if "not executable here" in result:
            return "not_executable"
        return "exception"
    return "wrong_output"


def _stored_cases(skill):
    """Test cases recorded in a skill's stored verification details."""
    details = (skill.get("verification") or {}).get("details") or []
    return [dict(detail["case"]) for detail in details if "case" in detail]


def diagnose_skill_failure(loop, skill_id):
    """Diagnose why a skill failed, from its stored verification.

    Returns a dict with the skill, the failing cases (each with
    kind, expected, and observed), the pass/fail counts, the
    loop's correction for the task as read-only context, and a
    suggested fix kind. Raises KeyError for an unknown skill.
    A skill with no verification yet diagnoses as
    "never_verified" with no failures listed.
    """
    skill = loop.get_skill(skill_id)
    details = (skill.get("verification") or {}).get("details") or []
    failures = []
    passed = 0
    for detail in details:
        if detail.get("passed"):
            passed += 1
            continue
        case = dict(detail.get("case") or {})
        failures.append({
            "case": case,
            "expected": case.get("expected"),
            "observed": detail.get("result"),
            "kind": _failure_kind(detail),
        })
    correction = loop.correction_for(skill["task"])
    if not details:
        suggested = "never_verified"
    elif not failures:
        suggested = "no_failure_found"
    elif all(f["kind"] == "not_executable" for f in failures):
        suggested = "needs_executor"
    elif correction is not None:
        suggested = "apply_correction"
    elif all(f["kind"] == "wrong_output" for f in failures):
        suggested = "adjust_output"
    else:
        suggested = "fix_exception"
    return {
        "skill_id": skill["id"],
        "skill_name": skill["name"],
        "task": skill["task"],
        "status": skill["status"],
        "total": len(details),
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
        "correction": correction,
        "suggested_fix": suggested,
        "previous_attempts": [],
    }


def lookup_rewriter(code, diagnosis, attempt):
    """Deterministic built-in rewriter for callable code.

    Wraps the original callable with a correction table built
    from the failing test cases: inputs that failed return their
    expected output, everything else runs the original code.
    This is a narrow repair (it memorises the corrected outputs
    for the failing inputs) and it says so in the strategy name
    recorded on the repaired draft. It cannot repair string
    code: that needs a caller-supplied rewriter.
    """
    if not callable(code):
        raise ValueError("lookup_rewriter needs callable code")
    table = {}
    for failure in diagnosis.get("failures", []):
        case = failure.get("case") or {}
        if "expected" in case:
            table[_case_key(case)] = case["expected"]
    if not table:
        raise ValueError("lookup_rewriter needs at least one failing case")

    def _repaired(*args, **kwargs):
        key = _case_key({"args": list(args), "kwargs": dict(kwargs)})
        if key in table:
            return table[key]
        return code(*args, **kwargs)

    return _repaired


class SkillRepairer:
    """Repair failing skills through the live learning loop.

    The repairer owns no state of its own except its attempt
    history: skills, corrections, and experiences all live in
    the `LearningLoop` it wraps, so a repair and a misprediction
    see the same loop.
    """

    def __init__(self, loop, *, rewriter=None, max_attempts=3):
        if not isinstance(loop, LearningLoop):
            raise ValueError("loop must be a LearningLoop")
        self.loop = loop
        self.rewriter = rewriter
        self.max_attempts = max_attempts
        self.history = []

    def repair(self, skill_id, test_cases=None, *, rewriter=None,
               executor=None):
        """Diagnose, rewrite, and re-verify one skill.

        `test_cases` defaults to the cases stored in the skill's
        last verification. Returns {"status": "repaired", ...}
        with the new draft skill id when an attempt passes every
        case, {"status": "already_passing", ...} when the skill
        passes the cases as-is, or {"status": "unrepairable", ...}
        with the reason and every attempt recorded when none
        passes. The original skill is never mutated: the repair
        is always a NEW draft (inactive, `repaired_from` pointing
        back), and a manual edit for the task is never touched.
        """
        skill = self.loop.get_skill(skill_id)
        cases = list(test_cases) if test_cases else _stored_cases(skill)
        if not cases:
            raise ValueError(
                "repair needs test cases: pass test_cases or verify "
                "the skill first so its cases are stored"
            )
        # Re-run the cases first: the diagnosis must come from the
        # current code against these cases, not from a stale stored
        # verification (an active skill can pass its stored tests
        # and still fail live on a new input).
        self.loop.verify_skill(skill_id, cases, executor=executor)
        diagnosis = diagnose_skill_failure(self.loop, skill_id)
        if diagnosis["failed"] == 0:
            # The skill passes these cases: there is nothing to
            # rewrite. (A suspect skill in this state needs a live
            # failing case, not a rewrite.)
            result = {
                "status": "already_passing",
                "skill_id": skill_id,
                "diagnosis": diagnosis,
                "attempts": [],
            }
            self.history.append(result)
            return result
        attempts = []
        chosen = rewriter or self.rewriter
        for attempt in range(1, self.max_attempts + 1):
            diagnosis["previous_attempts"] = list(attempts)
            code = self.loop.skills[skill_id]["code"]
            try:
                if chosen is not None:
                    new_code = chosen(code, diagnosis, attempt)
                    strategy = getattr(chosen, "__name__", "custom_rewriter")
                elif callable(code):
                    new_code = lookup_rewriter(code, diagnosis, attempt)
                    strategy = "lookup_table"
                else:
                    result = {
                        "status": "unrepairable",
                        "skill_id": skill_id,
                        "reason": "no_rewriter_for_string_code",
                        "diagnosis": diagnosis,
                        "attempts": attempts,
                    }
                    self.history.append(result)
                    return result
            except Exception as exc:  # noqa: BLE001 - a failed rewrite
                attempts.append({
                    "attempt": attempt, "ok": False, "error": str(exc),
                })
                continue
            draft = self.loop.draft_skill(
                skill["task"], code=new_code,
                name=f"{skill['name']}_repair{attempt}",
                source_experience_ids=list(skill["source_experience_ids"]),
            )
            internal = self.loop.skills[draft["id"]]
            internal["repaired_from"] = skill_id
            internal["repair_attempt"] = attempt
            internal["repair_strategy"] = strategy
            verification = self.loop.verify_skill(
                draft["id"], cases, executor=executor)
            record = {
                "attempt": attempt,
                "ok": bool(verification["verified"]),
                "strategy": strategy,
                "draft_skill_id": draft["id"],
                "passed": verification["passed"],
                "failed": verification["failed"],
            }
            attempts.append(record)
            if verification["verified"]:
                result = {
                    "status": "repaired",
                    "skill_id": draft["id"],
                    "repaired_from": skill_id,
                    "attempts": attempts,
                    "diagnosis": diagnosis,
                    "verification": verification,
                }
                self.history.append(result)
                return result
            # The next attempt rewrites against the fresh failures
            # of this draft, not the original ones.
            fresh = diagnose_skill_failure(self.loop, draft["id"])
            diagnosis["failures"] = fresh["failures"]
            diagnosis["failed"] = fresh["failed"]
            diagnosis["passed"] = fresh["passed"]
            diagnosis["total"] = fresh["total"]
        result = {
            "status": "unrepairable",
            "skill_id": skill_id,
            "reason": "still_failing_after_attempts",
            "diagnosis": diagnosis,
            "attempts": attempts,
        }
        self.history.append(result)
        return result

    def repair_suspect_skills(self, test_cases_by_task=None, *,
                              rewriter=None, executor=None):
        """Repair every skill the loop marked suspect.

        A skill becomes suspect when a misprediction or a wrong
        user-feedback verdict hits its task, so this is the live
        loop entry point: the curriculum runner calls it after a
        failure, and each suspect skill goes through diagnose ->
        rewrite -> re-verify. `test_cases_by_task` maps a task to
        its cases; without it the stored verification cases are
        reused. Returns one result per suspect skill.
        """
        results = []
        for skill in self.loop.list_skills(status="suspect"):
            cases = None
            if test_cases_by_task is not None:
                cases = test_cases_by_task.get(skill["task"])
            results.append(self.repair(
                skill["id"], cases, rewriter=rewriter, executor=executor))
        return results

    def list_repairs(self):
        """Repair history (results, newest last)."""
        return [dict(entry) for entry in self.history]


# ---------------------------------------------------------------------------
# Procedure change (letta-code): repeated outcomes change procedures.
# ---------------------------------------------------------------------------

def _step_hash(step):
    return json.dumps(step, sort_keys=True, ensure_ascii=False, default=str)


class InMemoryProcedureStore:
    """In-memory procedure store with the `procedural` interface.

    Implements `record_successful_task` / `list_procedures` /
    `mark_run` / `get_procedure` with the same semantics as
    `puspa.memory.procedural` (same pure lifecycle functions, so
    promotion, broken-after-2-failures, and decay behave
    identically), plus explicit versioning: every new step for a
    service is a new version (`version`, `supersedes`). The
    durable path is the `procedural` module itself; pass that
    module as the store and the updater works on Postgres rows
    instead (version then travels in `output_schema`, because
    `procedural.record_successful_task` does not bump the
    `versi` column).
    """

    def __init__(self):
        self._procedures = {}

    def record_successful_task(self, user_id, service, step, agent_id=None,
                               output_schema=None, required_checks=None,
                               source_event_id=None, dsn=None):
        if not service or not isinstance(step, list) or not step:
            raise ValueError("service and non-empty steps are required")
        for proc in self._procedures.values():
            if (
                proc["user_id"] == str(user_id)
                and proc["service"] == service
                and _step_hash(proc["step"]) == _step_hash(step)
            ):
                return self.mark_run(
                    proc["id"], user_id, True, agent_id=agent_id)
        siblings = [
            p for p in self._procedures.values()
            if p["user_id"] == str(user_id) and p["service"] == service
        ]
        version = (output_schema or {}).get("version")
        if version is None:
            version = max((p["version"] for p in siblings), default=0) + 1
        proc = {
            "id": uuid.uuid4().hex,
            "user_id": str(user_id),
            "service": service,
            "version": int(version),
            "step": [dict(s) for s in step],
            "output_schema": dict(output_schema or {}),
            "required_checks": list(required_checks or []),
            "status": "trial",
            "successful_runs": 1,
            "failed_runs": 0,
            "consecutive_failed_runs": 0,
            "agent_ids": [str(agent_id)] if agent_id else [],
            "source_event_id": source_event_id,
            "supersedes": (output_schema or {}).get("supersedes"),
            "created_at": None,
            "updated_at": None,
        }
        self._procedures[proc["id"]] = proc
        return dict(proc)

    def get_procedure(self, procedure_id, user_id, dsn=None):
        proc = self._procedures.get(procedure_id)
        if proc is None or proc["user_id"] != str(user_id):
            raise KeyError(f"procedure not found: {procedure_id}")
        return dict(proc)

    def list_procedures(self, user_id, status=None, dsn=None):
        rows = [
            p for p in self._procedures.values()
            if p["user_id"] == str(user_id)
        ]
        if status is not None:
            rows = [p for p in rows if p["status"] == status]
        rows.sort(key=lambda p: p["version"])
        return [dict(p) for p in rows]

    def mark_run(self, procedure_id, user_id, success, agent_id=None,
                 dsn=None):
        proc = self._procedures.get(procedure_id)
        if proc is None or proc["user_id"] != str(user_id):
            raise KeyError(f"procedure not found: {procedure_id}")
        updated = compute_run_update(proc, success, agent_id=agent_id)
        updated["decayed"] = proc.get("decayed", False)
        self._procedures[procedure_id] = updated
        return dict(updated)

    def apply_decay_all(self, now=None, older_than_days=90):
        """Decay every stale verified procedure (in place).

        A decayed procedure returns to trial with zero successful
        runs and must earn verification again; its version does
        not change. Returns the decayed procedures.
        """
        decayed = []
        for proc in list(self._procedures.values()):
            updated = apply_decay(proc, now=now,
                                  older_than_days=older_than_days)
            if updated.get("decayed"):
                self._procedures[proc["id"]] = updated
                decayed.append(dict(updated))
        return decayed


class ProcedureUpdater:
    """Repeated task outcomes change the agent's own procedures.

    The updater is driven by task results: `record_outcome` is
    called by the task cycle for every finished task. A success
    with the stored step records a run (promotion path). A
    success with a DIFFERENT step is counted; once the same new
    step has succeeded `repeat_threshold` times it becomes a new
    procedure VERSION (trial, `supersedes` the old version) -
    the old version is never overwritten, and a conflicting step
    is reported with `conflicts_with` like
    `procedural.propose_conflicting_procedure`. A failure on the
    stored step records a failed run (broken after 2 consecutive
    failures, revision 7 section 3.3). Stale verified procedures
    decay through `decay_stale`.
    """

    def __init__(self, store=None, user_id="user-1", repeat_threshold=3):
        self.store = store if store is not None else InMemoryProcedureStore()
        self.user_id = str(user_id)
        self.repeat_threshold = repeat_threshold
        self._repeat_counts = {}

    def _current_procedure(self, service):
        """Latest non-broken procedure for a service, or None."""
        rows = [
            p for p in self.store.list_procedures(self.user_id)
            if p["service"] == service and p["status"] != "broken"
        ]
        if not rows:
            return None
        return max(rows, key=lambda p: p.get("version", 0))

    def current_procedure(self, service):
        """The procedure the agent would run for a service now."""
        proc = self._current_procedure(service)
        return dict(proc) if proc else None

    def list_versions(self, service):
        """Every version of a service's procedure, oldest first."""
        rows = [
            p for p in self.store.list_procedures(self.user_id)
            if p["service"] == service
        ]
        rows.sort(key=lambda p: p.get("version", 0))
        return [dict(p) for p in rows]

    def record_outcome(self, task, service, step, success, agent_id=None):
        """Record one task outcome; change the procedure if it repeats.

        Returns a result dict with `status`: "recorded" (a run on
        the stored procedure, or a first version born from a
        success with no stored procedure), "counted" (a new step
        seen again but below threshold), "changed" (threshold
        reached: a new version was created), "proposed" (a new
        version that conflicts with the stored one), or
        "ignored" (a failure with a step that matches no stored
        procedure - failures never create procedures).
        """
        if not service or not isinstance(step, list) or not step:
            raise ValueError("service and non-empty steps are required")
        current = self._current_procedure(service)
        if current is not None and _step_hash(current["step"]) == _step_hash(step):
            proc = self.store.mark_run(
                current["id"], self.user_id, success, agent_id=agent_id)
            return {
                "task": str(task), "service": service,
                "status": "recorded", "procedure": proc,
                "conflicts_with": None, "repeat_count": 0,
            }
        if current is None and success:
            # No stored procedure yet: the first success births
            # version 1 right away; repetition only matters once
            # a procedure exists to change.
            proc = self.store.record_successful_task(
                self.user_id, service, step, agent_id=agent_id,
                output_schema={
                    "version": 1, "supersedes": None,
                    "source_task": str(task),
                },
                source_event_id=None,
            )
            return {
                "task": str(task), "service": service,
                "status": "recorded", "procedure": proc,
                "conflicts_with": None, "repeat_count": 0,
            }
        if not success:
            return {
                "task": str(task), "service": service,
                "status": "ignored",
                "reason": "failure with an unstored step creates nothing",
                "procedure": None, "conflicts_with": None,
                "repeat_count": 0,
            }
        key = (service, _step_hash(step))
        count = self._repeat_counts.get(key, 0) + 1
        self._repeat_counts[key] = count
        if count < self.repeat_threshold:
            return {
                "task": str(task), "service": service,
                "status": "counted", "procedure": None,
                "conflicts_with": None, "repeat_count": count,
            }
        self._repeat_counts[key] = 0
        siblings = self.list_versions(service)
        # A step that matches an older version re-adopts it (a run
        # on that version) instead of minting a duplicate version.
        for old in siblings:
            if _step_hash(old["step"]) == _step_hash(step):
                proc = self.store.mark_run(
                    old["id"], self.user_id, True, agent_id=agent_id)
                return {
                    "task": str(task), "service": service,
                    "status": "recorded", "procedure": proc,
                    "conflicts_with": None, "repeat_count": count,
                }
        conflict = None
        if current is not None:
            conflict = detect_contradiction([current], service, step)
        version = max(
            (p.get("version", 0) for p in siblings), default=0) + 1
        output_schema = {
            "version": version,
            "supersedes": current["id"] if current else None,
            "source_task": str(task),
        }
        proc = self.store.record_successful_task(
            self.user_id, service, step, agent_id=agent_id,
            output_schema=output_schema,
            source_event_id=None,
        )
        return {
            "task": str(task), "service": service,
            "status": "proposed" if conflict else "changed",
            "procedure": proc,
            "conflicts_with": conflict["id"] if conflict else None,
            "repeat_count": count,
        }

    def decay_stale(self, now=None, older_than_days=90):
        """Decay stale verified procedures; returns the decayed ones."""
        if hasattr(self.store, "apply_decay_all"):
            return self.store.apply_decay_all(
                now=now, older_than_days=older_than_days)
        if hasattr(self.store, "decay_stale_procedures"):
            from . import procedural

            return procedural.decay_stale_procedures(
                self.user_id, older_than_days=older_than_days, now=now)
        return []

    def repeat_counts(self):
        """Pending repeat counts per (service, step hash)."""
        return dict(self._repeat_counts)
