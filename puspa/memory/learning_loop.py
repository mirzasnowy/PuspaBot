"""Learning loop - hermes-agent + Voyager shape, rewritten for Puspa.

The loop learns from experience instead of repeating the same
mistake: a curriculum proposes the next task from observed gaps,
successful tasks draft new skills, and mispredictions correct
the model. Patterns adapted (rewritten, not copied):

- NousResearch/hermes-agent (MIT): skills are created and
  repaired automatically and a persistent user model is kept.
  Puspa keeps every learned skill INACTIVE until the user
  approves it; learning never overwrites a manual user edit.
- MineDojo/Voyager (MIT, research archive): an automatic
  curriculum proposes tasks, self-verification tests generated
  code, and successful code is stored in a skill library
  indexed by embeddings. Puspa adapts the shape only (the
  Minecraft code is not reusable): the curriculum proposes the
  next task, verification runs caller-supplied tests (or a
  sandbox executor supplied by the caller), and each learned
  skill is stored as tested executable code with an embedding
  key in `puspa/memory/procedural.py`.
- letta-ai/letta-code (Apache-2.0): self-improvement from
  experience; the same approval-gate condition applies.

Safety rules enforced here:

- A drafted skill is inactive (`active` False, status
  "draft") until `activate_skill` is called with a user
  approval. Approval is verified against an approved request
  in `puspa/policy/gate.py` (the Gate has no kind for skill
  activation and `gate.py` must not be modified, so the caller
  obtains the approval through the Gate with the activation
  described in the request content, then hands the approved
  request id here), or passed explicitly as
  `approved_by=<user_id>` for flows where the user approved
  outside the Gate. Without either, activation raises.
- Learning never overwrites a manual user edit: `note_manual_edit`
  records the user's version of a task, and `draft_skill` for
  the same task stores a separate draft flagged
  `conflicts_with_manual_edit` instead of replacing anything.
- Verification is fail-closed: string code is never executed
  by this module. A skill whose code is a string needs an
  `executor` callable (in production: the sandbox runner in
  `puspa/sandbox/runner.py`); without one the skill stays
  unverified. No network call is made on any path here.

Seam for the world-model agent (exact signature required):

    record_misprediction(record)

with `record` a dict holding the keys `task`, `predicted`,
`observed`, `error`, `context`. It records the prediction
error as experience that corrects the model: later
`predict(task)` returns the observed (corrected) value instead
of the old prediction, and the curriculum proposes a retry
task for the gap. The module-level function delegates to a
default `LearningLoop` instance; use `get_learning_loop()` /
`reset_learning_loop()` in tests.

User feedback is the same seam in the other direction:

    record_feedback(task, verdict, correction=None, source="user")

with `verdict` "correct" or "wrong". A "wrong" verdict with a
correction corrects the next prediction like a misprediction;
a "correct" verdict only reinforces and never changes a
prediction. Feedback never overwrites a manual user edit: when
`note_manual_edit` already holds a version for the task, a
correction from feedback is stored as a proposal instead.

This module is deliberately in-memory: the loop state (log,
corrections, drafts) lives in a `LearningLoop` instance so the
behaviour is testable without a database. Durable storage is
an adapter: pass a `procedure_store` (an object with
`record_successful_task` / `list_procedures` / `mark_run`,
such as the `puspa.memory.procedural` module) and drafts are
also persisted as trial procedures there. The correction table
itself is persisted through `puspa.memory.loop_store`
(`loop_state` / `restore_loop_state` below): corrections plus
the experiences that carry them survive a restart, while
skills, manual edits, and feedback proposals stay session-only.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from .embeddings import HashEmbedding, cosine_similarity
from .procedural import LEARNED_DRAFT_PREFIX

MISPREDICTION_KEYS = ("task", "predicted", "observed", "error", "context")

SKILL_STATUSES = ("draft", "active", "rejected", "suspect")

FEEDBACK_VERDICTS = ("correct", "wrong")
FEEDBACK_SOURCE = "user_feedback"


def _skill_id():
    return uuid.uuid4().hex


def _content_hash(value):
    canon = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _check_gate_approval(gate, request_id, user_id):
    """True when the Gate holds an approved request for this user.

    Reads the Gate's stored requests (request / approve /
    execute lifecycle in `puspa/policy/gate.py`). The request
    must exist, belong to the user, and be approved or executed.
    Any lookup problem means "not approved" (fail-closed).
    """
    try:
        requests = getattr(gate, "_requests", None) or {}
        request = requests.get(request_id)
        if request is None:
            return False
        if getattr(request, "user_id", None) != user_id:
            return False
        return getattr(request, "status", None) in ("approved", "executed")
    except Exception:  # noqa: BLE001 - fail closed on any gate problem
        return False


class LearningLoop:
    """In-memory learning loop: experience, curriculum, skills."""

    def __init__(self, embedder=None, procedure_store=None):
        self.embedder = embedder or HashEmbedding()
        self.procedure_store = procedure_store
        self.experiences = []
        self.corrections = {}
        self.gaps = {}
        self.skills = {}
        self.manual_edits = {}
        self.feedback_proposals = []

    # -- experience: mispredictions correct the model ------------

    def record_misprediction(self, record):
        """Record one prediction error as correcting experience.

        `record` must hold task, predicted, observed, error and
        context. Returns the stored correction. After this call
        `predict(task)` returns the observed value, and the
        curriculum proposes a retry task for the gap. A draft
        skill for the same task is marked suspect, so a wrong
        skill is never trusted again without re-verification.
        """
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        missing = [k for k in MISPREDICTION_KEYS if k not in record]
        if missing:
            raise ValueError(f"record is missing keys: {missing}")
        task = str(record["task"])
        if not task:
            raise ValueError("record task must be a non-empty string")
        experience = {
            "id": uuid.uuid4().hex,
            "task": task,
            "predicted": record["predicted"],
            "observed": record["observed"],
            "error": record["error"],
            "context": record["context"],
        }
        self.experiences.append(experience)
        previous = self.corrections.get(task)
        self.corrections[task] = {
            "task": task,
            "predicted": record["predicted"],
            "observed": record["observed"],
            "error": record["error"],
            "count": (previous["count"] if previous else 0) + 1,
            "experience_ids": (
                (previous["experience_ids"] if previous else [])
                + [experience["id"]]
            ),
        }
        gap = self.gaps.get(task, {"mispredictions": 0, "last_error": None})
        gap["mispredictions"] += 1
        gap["last_error"] = record["error"]
        self.gaps[task] = gap
        for skill in self.skills.values():
            if skill["task"] == task and skill["status"] == "active":
                skill["status"] = "suspect"
        return dict(self.corrections[task])

    def record_feedback(self, task, verdict, *, correction=None, source="user"):
        """Record user feedback about an answer for a task.

        `verdict` is "correct" or "wrong". A "wrong" verdict with
        a `correction` corrects the next prediction like a
        misprediction: `predict(task)` returns the correction,
        the active skill for the task becomes suspect, and the
        curriculum proposes a retry. A "wrong" verdict without a
        correction still logs the gap (the curriculum proposes a
        retry) but changes no prediction. A "correct" verdict
        only records a positive experience: it never changes a
        prediction and never creates a correction, so learned
        truth is not overwritten. Every feedback-derived entry
        carries source "user_feedback" (the actor is kept in
        `feedback_source`) so the origin is auditable. Feedback
        never overwrites a manual user edit: when
        `note_manual_edit` already holds a version for the task,
        a correction from feedback is stored as a proposal with
        `conflicts_with_manual_edit` True instead of replacing
        the manual version. Returns a result dict with `task`,
        `verdict`, `status` and `source`.
        """
        task = str(task)
        if not task:
            raise ValueError("task must be a non-empty string")
        if verdict not in FEEDBACK_VERDICTS:
            raise ValueError(f"verdict must be one of {FEEDBACK_VERDICTS}")
        predicted = self.predict(task)
        experience = {
            "id": uuid.uuid4().hex,
            "task": task,
            "kind": "user_feedback",
            "source": FEEDBACK_SOURCE,
            "feedback_source": source,
            "verdict": verdict,
            "predicted": predicted,
            "observed": correction if verdict == "wrong" else predicted,
            "error": (
                "user feedback: answer was wrong" if verdict == "wrong"
                else None
            ),
            "context": {"source": FEEDBACK_SOURCE, "feedback_source": source},
        }
        self.experiences.append(experience)
        result = {
            "task": task,
            "verdict": verdict,
            "source": FEEDBACK_SOURCE,
            "feedback_source": source,
            "experience_id": experience["id"],
        }
        if verdict == "correct":
            result["status"] = "recorded"
            result["predicted"] = predicted
            return result
        # verdict == "wrong": the answer failed, so the gap is real
        # even when no correction was given.
        gap = self.gaps.get(task, {"mispredictions": 0, "last_error": None})
        gap["mispredictions"] += 1
        gap["last_error"] = "user feedback: answer was wrong"
        self.gaps[task] = gap
        for skill in self.skills.values():
            if skill["task"] == task and skill["status"] == "active":
                skill["status"] = "suspect"
        if correction is None:
            result["status"] = "recorded"
            result["correction"] = None
            return result
        if task in self.manual_edits:
            proposal = {
                "id": uuid.uuid4().hex,
                "task": task,
                "correction": correction,
                "source": FEEDBACK_SOURCE,
                "feedback_source": source,
                "status": "proposed",
                "conflicts_with_manual_edit": True,
                "experience_id": experience["id"],
            }
            self.feedback_proposals.append(proposal)
            result["status"] = "proposed"
            result["correction"] = correction
            result["conflicts_with_manual_edit"] = True
            result["proposal_id"] = proposal["id"]
            return result
        previous = self.corrections.get(task)
        self.corrections[task] = {
            "task": task,
            "predicted": predicted,
            "observed": correction,
            "error": "user feedback: answer was wrong",
            "count": (previous["count"] if previous else 0) + 1,
            "experience_ids": (
                (previous["experience_ids"] if previous else [])
                + [experience["id"]]
            ),
            "source": FEEDBACK_SOURCE,
            "feedback_source": source,
        }
        result["status"] = "applied"
        result["correction"] = dict(self.corrections[task])
        return result

    def list_feedback_proposals(self, task=None):
        """Feedback corrections held as proposals (manual-edit conflicts)."""
        proposals = self.feedback_proposals
        if task is not None:
            proposals = [p for p in proposals if p["task"] == str(task)]
        return [dict(p) for p in proposals]

    def correction_for(self, task):
        """The learned correction for a task, or None."""
        correction = self.corrections.get(str(task))
        return dict(correction) if correction else None

    def predict(self, task, default=None):
        """Predict for a task, corrected by past experience.

        Returns the observed value from the latest misprediction
        for the task when one exists, otherwise `default`. This
        is the behaviour-change seam: the same call returns a
        different (corrected) answer after experience.
        """
        correction = self.corrections.get(str(task))
        if correction is None:
            return default
        return correction["observed"]

    # -- durable state: corrections survive a restart ----------

    def loop_state(self):
        """Durable snapshot: the correction table plus its experiences.

        Plain dicts only, so the store (`puspa.memory.loop_store`)
        can JSON-encode them. Only corrections and the experiences
        that carry them are in the snapshot: positive feedback,
        gaps without a correction, draft skills, manual edits, and
        feedback proposals are session-only and are not restored.
        Gaps are rebuilt from the corrections on restore.
        """
        return {
            "corrections": {
                task: dict(correction)
                for task, correction in self.corrections.items()
            },
            "experiences": [dict(exp) for exp in self.experiences],
        }

    def restore_loop_state(self, state):
        """Replace this loop's corrections with a saved snapshot.

        `state` is a dict as returned by `loop_state` (corrections
        plus experiences). Gaps are rebuilt from the corrections
        (count and last error), so `propose_next_task` offers the
        same retry after a restart and `predict` answers with the
        same corrected values. Skills, manual edits, and feedback
        proposals are not in the snapshot and are left untouched.
        Returns the restored snapshot.
        """
        if not isinstance(state, dict):
            raise ValueError("state must be a dict")
        corrections = state.get("corrections") or {}
        experiences = state.get("experiences") or []
        self.corrections = {
            str(task): dict(correction)
            for task, correction in corrections.items()
        }
        self.experiences = [dict(exp) for exp in experiences]
        self.gaps = {}
        for task, correction in self.corrections.items():
            self.gaps[task] = {
                "mispredictions": int(correction.get("count", 1)),
                "last_error": correction.get("error"),
            }
        return self.loop_state()

    # -- curriculum: propose the next task from observed gaps ----

    def propose_next_task(self):
        """Propose the next learning task, or None when nothing gaps.

        Priority: the task with the most mispredictions first
        (ties broken by task name, deterministic), as a retry
        with the corrected model; then the oldest unverified
        draft skill, as a verification task.
        """
        if self.gaps:
            task = sorted(
                self.gaps.items(),
                key=lambda item: (-item[1]["mispredictions"], item[0]),
            )[0][0]
            return {
                "task": task,
                "reason": "misprediction_gap",
                "gap": dict(self.gaps[task]),
                "difficulty": "retry",
            }
        drafts = [s for s in self.skills.values() if s["status"] == "draft"]
        if drafts:
            skill = sorted(drafts, key=lambda s: s["created_order"])[0]
            return {
                "task": f"verify_skill:{skill['name']}",
                "reason": "unverified_draft",
                "gap": {"skill_id": skill["id"], "skill_name": skill["name"]},
                "difficulty": "verify",
            }
        return None

    # -- manual edits: learning never overwrites the user --------

    def note_manual_edit(self, task, content):
        """Record the user's manual version of a task's solution.

        Later drafts for the same task are flagged as conflicting
        with the manual edit and never replace it.
        """
        self.manual_edits[str(task)] = _content_hash(content)
        return {"task": str(task), "content_hash": self.manual_edits[str(task)]}

    # -- skill library: draft, verify, approve, activate ---------

    def draft_skill(self, task, code, *, name=None, verification=None,
                    embedding_key=None, source_experience_ids=None):
        """Draft a learned skill from a successful task.

        The draft is INACTIVE (status "draft", active False) and
        stays that way until the user approves it. `code` is the
        tested executable code: a callable, or a string when the
        caller verifies it through a sandbox executor. The
        embedding key indexes the skill for reuse: by default the
        embedding of the task description. Returns the skill dict.
        """
        task = str(task)
        if not task:
            raise ValueError("task must be a non-empty string")
        if code is None:
            raise ValueError("code is required for a learned skill")
        skill_name = name or f"learned_{task}"
        key_text = embedding_key or f"{task}: {skill_name}"
        skill = {
            "id": _skill_id(),
            "name": skill_name,
            "task": task,
            "code": code,
            "embedding_key": self.embedder.embed(key_text),
            "embedding_key_text": key_text,
            "status": "draft",
            "active": False,
            "verified": False,
            "verification": verification or {},
            "source_experience_ids": list(source_experience_ids or []),
            "conflicts_with_manual_edit": task in self.manual_edits,
            "created_order": len(self.skills),
        }
        self.skills[skill["id"]] = skill
        return self._public_skill(skill)

    def verify_skill(self, skill_id, test_cases, *, executor=None):
        """Self-verification: run the skill against test cases.

        `test_cases` is a list of {"args": [...], "expected": ...}
        (or {"kwargs": {...}, "expected": ...}). Callable code is
        called directly; string code needs an `executor` callable
        `executor(code, case)` (in production: the sandbox
        runner) and stays unverified without one. Returns
        {"passed", "failed", "verified", "details"} and records
        the result on the skill. A skill is verified only when
        every case passes.
        """
        skill = self.skills.get(skill_id)
        if skill is None:
            raise KeyError(f"skill not found: {skill_id}")
        if not test_cases:
            raise ValueError("at least one test case is required")
        code = skill["code"]
        passed, failed, details = 0, 0, []
        for case in test_cases:
            expected = case.get("expected")
            try:
                if callable(code):
                    args = case.get("args", [])
                    kwargs = case.get("kwargs", {})
                    result = code(*args, **kwargs)
                elif isinstance(code, str) and executor is not None:
                    result = executor(code, case)
                else:
                    raise RuntimeError("code is not executable here")
                ok = result == expected
            except Exception as exc:  # noqa: BLE001 - a failing case
                ok, result = False, f"error: {exc}"
            details.append({"case": case, "result": result, "passed": ok})
            if ok:
                passed += 1
            else:
                failed += 1
        verified = failed == 0 and passed > 0
        skill["verified"] = verified
        skill["verification"] = {
            "passed": passed, "failed": failed, "details": details,
        }
        return {
            "skill_id": skill_id, "passed": passed, "failed": failed,
            "verified": verified, "details": details,
        }

    def activate_skill(self, skill_id, *, user_id=None, gate=None,
                       approval_request_id=None, approved_by=None):
        """Activate a drafted skill after user approval.

        Requires either an approved Gate request (`gate` plus
        `approval_request_id` plus the owning `user_id`, verified
        against the Gate's stored requests) or an explicit
        `approved_by` user id. Raises PermissionError without
        approval, KeyError for an unknown skill, and ValueError
        when the skill was rejected or is still unverified.
        Activation never touches a manual edit: a skill flagged
        `conflicts_with_manual_edit` stays a separate draft and
        is not activated over the user's version.
        """
        skill = self.skills.get(skill_id)
        if skill is None:
            raise KeyError(f"skill not found: {skill_id}")
        if skill["status"] == "rejected":
            raise ValueError("a rejected skill cannot be activated")
        if skill.get("conflicts_with_manual_edit"):
            raise ValueError(
                "skill conflicts with a manual user edit and cannot "
                "be activated over it"
            )
        if not skill.get("verified"):
            raise ValueError("only a verified skill can be activated")
        approved = False
        if gate is not None and approval_request_id is not None:
            if user_id is None:
                raise ValueError("user_id is required to verify a Gate approval")
            approved = _check_gate_approval(gate, approval_request_id, user_id)
        elif approved_by is not None:
            approved = user_id is None or approved_by == user_id
        if not approved:
            raise PermissionError(
                "learned skill needs user approval before activation"
            )
        skill["status"] = "active"
        skill["active"] = True
        return self._public_skill(skill)

    def reject_skill(self, skill_id, *, reason=None):
        """User rejects a drafted skill; it stays recorded, inactive."""
        skill = self.skills.get(skill_id)
        if skill is None:
            raise KeyError(f"skill not found: {skill_id}")
        skill["status"] = "rejected"
        skill["active"] = False
        if reason is not None:
            skill["rejection_reason"] = reason
        return self._public_skill(skill)

    def list_skills(self, status=None):
        """List learned skills (public view, no code), newest last."""
        skills = sorted(self.skills.values(), key=lambda s: s["created_order"])
        if status is not None:
            skills = [s for s in skills if s["status"] == status]
        return [self._public_skill(s) for s in skills]

    def get_skill(self, skill_id):
        """One learned skill (public view, no code)."""
        skill = self.skills.get(skill_id)
        if skill is None:
            raise KeyError(f"skill not found: {skill_id}")
        return self._public_skill(skill)

    def find_reusable_skill(self, task, *, threshold=0.7):
        """Find the best active skill for a task by embedding key.

        Returns the public skill dict when the cosine similarity
        of the task embedding against the skill key meets the
        threshold, otherwise None. Only active skills are reused:
        drafts are never returned here.
        """
        query_vec = self.embedder.embed(str(task))
        best, best_score = None, -1.0
        for skill in self.skills.values():
            if skill["status"] != "active":
                continue
            score = cosine_similarity(query_vec, skill["embedding_key"])
            if score > best_score:
                best, best_score = skill, score
        if best is not None and best_score >= threshold:
            result = self._public_skill(best)
            result["reuse_score"] = best_score
            return result
        return None

    # -- durable drafts in procedural storage ---------------------

    def save_draft_to_procedures(self, skill_id, user_id, *, dsn=None):
        """Persist a draft skill as a trial procedure.

        The draft is stored under the `learned_draft:` service
        prefix, so it is never selected for execution until the
        user approves it (see `activate_draft_procedure` in
        `procedural.py`). Needs a `procedure_store` (default:
        the `puspa.memory.procedural` module). Returns the
        stored procedure dict.
        """
        skill = self.skills.get(skill_id)
        if skill is None:
            raise KeyError(f"skill not found: {skill_id}")
        store = self.procedure_store
        if store is None:
            from . import procedural as store
        step = [
            {"step": f"run learned skill {skill['name']}"},
            {"step": f"for task {skill['task']}"},
        ]
        return store.record_successful_task(
            user_id, f"{LEARNED_DRAFT_PREFIX}{skill['name']}", step,
            output_schema={"skill_id": skill["id"], "verified": skill["verified"]},
            required_checks=["user_approval"],
            source_event_id=None, dsn=dsn,
        )

    def _public_skill(self, skill):
        """Public view of a skill: everything except the code."""
        return {
            "id": skill["id"],
            "name": skill["name"],
            "task": skill["task"],
            "embedding_key_text": skill["embedding_key_text"],
            "status": skill["status"],
            "active": skill["active"],
            "verified": skill["verified"],
            "verification": skill["verification"],
            "source_experience_ids": list(skill["source_experience_ids"]),
            "conflicts_with_manual_edit": skill["conflicts_with_manual_edit"],
        }


# -- module-level seam for the world-model agent -------------------

_DEFAULT_LOOP = LearningLoop()


def get_learning_loop():
    """The default shared learning loop instance."""
    return _DEFAULT_LOOP


def reset_learning_loop():
    """Replace the default loop with a fresh one (tests only)."""
    global _DEFAULT_LOOP
    _DEFAULT_LOOP = LearningLoop()
    return _DEFAULT_LOOP


def record_misprediction(record):
    """Record a prediction error as experience that corrects the model.

    `record` is a dict with keys `task`, `predicted`, `observed`,
    `error`, `context`. Returns the stored correction dict.
    Delegates to the default `LearningLoop` instance.
    """
    return _DEFAULT_LOOP.record_misprediction(record)


def record_feedback(task, verdict, *, correction=None, source="user"):
    """Record user feedback about an answer for a task.

    `verdict` is "correct" or "wrong"; `correction` is the right
    value when the answer was wrong. Returns the result dict
    from the default `LearningLoop` instance (see
    `LearningLoop.record_feedback`).
    """
    return _DEFAULT_LOOP.record_feedback(
        task, verdict, correction=correction, source=source
    )
