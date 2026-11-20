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

Causal Ledger (Kosmos K4, ``puspa.world.causal``): when the
loop is built with a ``causal_ledger`` (a ``CausalLedger``),
every recorded outcome also feeds it. A misprediction
contradicts the hypothesis "this task leads to the predicted
outcome" and proposes "this task leads to the observed
outcome"; a "correct" verdict strengthens the hypothesis for
the predicted outcome; a "wrong" verdict without a correction
only contradicts. The ledger is opt-in (default None): a loop
without one behaves exactly as before. ``predict`` falls back
to the ledger when no correction exists — the strongest active
hypothesis for the task supplies the prediction — so
hypotheses influence predictions, not just storage. The read
path is ``LearningLoop.expect(cause, condition)``. Evidence
links carry the experience id of the outcome plus, when the
caller passes ``evidence_event_id``, the ledger event id of
the real ledger event that recorded it. Causal updates are
fail-open: a ledger failure never breaks recording.

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

Curriculum runner guardrails (plan section 7.5, see
`puspa/memory/curriculum_guardrails.py`): an experience
teaches only when it is verified or user-confirmed. A record
marked unverified, or one carrying an orphan number, is
quarantined by `record_misprediction` and never touches the
correction table until a user releases it with
`confirm_experience`; a user correction whose figure is an
orphan number is held the same way by `record_feedback`. New
learned skills are capped per ISO week
(`max_new_skills_per_week`; repair drafts do not count), and
a skill reaches the user only through `submit_for_approval`,
which requires a passed holdout evaluation on cases that
differ from the verification cases; `activate_skill` refuses
a skill that was never submitted.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from .curriculum_guardrails import (
    WeeklySkillCap,
    classify_experience,
    find_orphan_numbers,
)
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


def _same_case_set(cases_a, cases_b):
    """True when two case lists hold the same cases (order aside)."""

    def canon(cases):
        return sorted(
            json.dumps(case, sort_keys=True, ensure_ascii=False, default=str) for case in cases
        )

    return canon(cases_a) == canon(cases_b)


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
    except Exception:
        return False


class LearningLoop:
    """In-memory learning loop: experience, curriculum, skills."""

    def __init__(
        self, embedder=None, procedure_store=None, causal_ledger=None, max_new_skills_per_week=None
    ):
        self.embedder = embedder or HashEmbedding()
        self.procedure_store = procedure_store
        self.causal_ledger = causal_ledger
        self.experiences = []
        self.corrections = {}
        self.gaps = {}
        self.skills = {}
        self.manual_edits = {}
        self.feedback_proposals = []
        self.approval_submissions = []
        # Section 7.5 guardrail: at most this many newly learned
        # skills per ISO week (repair drafts do not count).
        self.weekly_skill_cap = WeeklySkillCap(max_new_skills_per_week)
        self.max_new_skills_per_week = self.weekly_skill_cap.max_per_week

    # -- causal ledger: hypotheses learn from the same outcomes ---

    def _causal_links(self, experience_id, evidence_event_id):
        """Evidence links for one outcome: its experience id, plus
        the ledger event id when the caller recorded one."""
        links = [{"kind": "observation", "id": experience_id}]
        if evidence_event_id:
            links.append({"kind": "ledger_event", "id": str(evidence_event_id)})
        return links

    def _causal_observe(self, cause, effect, *, consistent, condition, links):
        """One causal observation, fail-open (never breaks recording)."""
        ledger = self.causal_ledger
        if ledger is None:
            return None
        try:
            return ledger.observe(
                cause,
                effect,
                condition,
                consistent=consistent,
                evidence=links,
                effect_value=effect,
            )
        except Exception:
            return None

    def _causal_outcome(self, task, *, predicted, observed, condition, links):
        """Fold one action -> outcome pair into the ledger.

        The predicted outcome did not happen, so the hypothesis
        for it is contradicted (when one exists); the observed
        outcome did happen, so its hypothesis is proposed or
        strengthened. Either side may be None: a missing
        predicted means nothing to contradict, a missing
        observed means nothing to propose.
        """
        if predicted is not None:
            self._causal_observe(
                task, predicted, consistent=False, condition=condition, links=links
            )
        if observed is not None:
            self._causal_observe(task, observed, consistent=True, condition=condition, links=links)

    def expect(self, cause, condition="", *, limit=None):
        """Causal read path: what do we expect if `cause` under `condition`?

        Active hypotheses for the cause, strongest first (see
        ``puspa.world.causal.CausalLedger.expect``). Empty when
        this loop has no causal ledger attached.
        """
        ledger = self.causal_ledger
        if ledger is None:
            return []
        try:
            return ledger.expect(cause, condition, limit=limit)
        except Exception:
            return []

    # -- experience: mispredictions correct the model ------------

    def _teach_misprediction(
        self,
        task,
        predicted,
        observed,
        error,
        experience_id,
        *,
        condition="",
        evidence_event_id=None,
    ):
        """Apply one misprediction as teaching (correction + gap).

        Shared by ``record_misprediction`` and by
        ``confirm_experience`` when a quarantined experience is
        released: the causal ledger, the correction table, the
        curriculum gap, and the suspect-marking of active skills
        all happen here, and only here, so a quarantined entry
        teaches exactly once, at release time.
        """
        self._causal_outcome(
            task,
            predicted=predicted,
            observed=observed,
            condition=condition,
            links=self._causal_links(experience_id, evidence_event_id),
        )
        previous = self.corrections.get(task)
        self.corrections[task] = {
            "task": task,
            "predicted": predicted,
            "observed": observed,
            "error": error,
            "count": (previous["count"] if previous else 0) + 1,
            "experience_ids": ((previous["experience_ids"] if previous else []) + [experience_id]),
        }
        gap = self.gaps.get(task, {"mispredictions": 0, "last_error": None})
        gap["mispredictions"] += 1
        gap["last_error"] = error
        self.gaps[task] = gap
        for skill in self.skills.values():
            if skill["task"] == task and skill["status"] == "active":
                skill["status"] = "suspect"
        return dict(self.corrections[task])

    # -- quarantine: unverified experience never teaches ----------

    def record_experience(
        self, entry, *, verification="world_observation", quarantine_reasons=None
    ):
        """Append one non-teaching experience to the log.

        The entry is stored as-is (id, verification status, and
        quarantine flag added) and never touches the correction
        table, the gaps, or the skills: this is the path for
        outcomes that are recorded but must not teach, such as
        the curriculum runner's own runs. ``verification`` is
        one of ``curriculum_guardrails.VERIFICATION_STATUSES``;
        "quarantined" marks the entry quarantined with the
        given reasons. Returns the stored entry.
        """
        stored = dict(entry)
        stored["id"] = stored.get("id") or uuid.uuid4().hex
        stored["verification"] = verification
        stored["quarantined"] = verification == "quarantined"
        stored["quarantine_reasons"] = list(quarantine_reasons or [])
        self.experiences.append(stored)
        return dict(stored)

    def quarantine_experience(self, entry, *, reasons=None):
        """Store one experience as quarantined (never teaches).

        ``reasons`` names why it is held (see
        ``curriculum_guardrails.QUARANTINE_REASONS``); when
        omitted the reason is "unverified". The entry stays in
        the experience log and is listed by
        ``list_quarantined`` until a user releases it with
        ``confirm_experience``. Returns the stored entry.
        """
        return self.record_experience(
            entry, verification="quarantined", quarantine_reasons=list(reasons or ["unverified"])
        )

    def list_quarantined(self, task=None):
        """Quarantined experiences awaiting user review, oldest first."""
        entries = [e for e in self.experiences if e.get("quarantined")]
        if task is not None:
            entries = [e for e in entries if e.get("task") == str(task)]
        return [dict(e) for e in entries]

    def confirm_experience(self, experience_id, *, by=None):
        """Release one quarantined experience so it teaches.

        The user (``by``) vouches for the held outcome, so the
        entry is replayed through the teaching path it was held
        from: a misprediction-shaped entry applies its
        correction and gap, a user-feedback correction is
        applied to the prediction (unless a manual edit now
        holds the task, in which case it stays a proposal), and
        a completed curriculum entry clears the gap for its
        task. The entry is marked "user_confirmed" and is no
        longer quarantined. Raises KeyError for an unknown id
        and ValueError when the entry is not quarantined.
        Returns the released entry.
        """
        entry = next((e for e in self.experiences if e.get("id") == experience_id), None)
        if entry is None:
            raise KeyError(f"experience not found: {experience_id}")
        if not entry.get("quarantined"):
            raise ValueError("only a quarantined experience can be confirmed")
        task = str(entry.get("task") or "")
        entry["quarantined"] = False
        entry["verification"] = "user_confirmed"
        if by is not None:
            entry["confirmed_by"] = by
        if entry.get("kind") == "user_feedback" and entry.get("verdict") == "wrong":
            correction = entry.get("observed")
            if correction is not None and task and task not in self.manual_edits:
                self._apply_feedback_correction(
                    task,
                    correction,
                    entry["id"],
                    source=entry.get("feedback_source", "user"),
                )
                self._confirm_proposal(entry["id"])
            return dict(entry)
        if entry.get("outcome") == "completed" and entry.get("kind") in (
            "curriculum",
            "curriculum_success",
        ):
            if task:
                self.gaps.pop(task, None)
            return dict(entry)
        if task and "predicted" in entry and "observed" in entry:
            self._teach_misprediction(
                task,
                entry.get("predicted"),
                entry.get("observed"),
                entry.get("error"),
                entry["id"],
            )
        return dict(entry)

    def _confirm_proposal(self, experience_id):
        """Mark the feedback proposal for an experience confirmed."""
        for proposal in self.feedback_proposals:
            if proposal.get("experience_id") == experience_id:
                proposal["status"] = "confirmed"

    def _apply_feedback_correction(
        self,
        task,
        correction,
        experience_id,
        *,
        source="user",
        condition="",
        evidence_event_id=None,
    ):
        """Apply a user correction as teaching (no gap change).

        The gap for a wrong answer is counted once, when the
        feedback arrives, so a correction released later from
        quarantine must not count it again. Used by
        ``record_feedback`` and by ``confirm_experience``.
        """
        predicted = self.predict(task)
        previous = self.corrections.get(task)
        self.corrections[task] = {
            "task": task,
            "predicted": predicted,
            "observed": correction,
            "error": "user feedback: answer was wrong",
            "count": (previous["count"] if previous else 0) + 1,
            "experience_ids": ((previous["experience_ids"] if previous else []) + [experience_id]),
            "source": FEEDBACK_SOURCE,
            "feedback_source": source,
        }
        self._causal_observe(
            task,
            correction,
            consistent=True,
            condition=condition,
            links=self._causal_links(experience_id, evidence_event_id),
        )
        for skill in self.skills.values():
            if skill["task"] == task and skill["status"] == "active":
                skill["status"] = "suspect"
        return dict(self.corrections[task])

    def record_misprediction(self, record, *, condition="", evidence_event_id=None, verified=None):
        """Record one prediction error as correcting experience.

        `record` must hold task, predicted, observed, error and
        context. Returns the stored correction. After this call
        `predict(task)` returns the observed value, and the
        curriculum proposes a retry task for the gap. A draft
        skill for the same task is marked suspect, so a wrong
        skill is never trusted again without re-verification.
        When this loop has a causal ledger, the outcome also
        feeds it: the predicted outcome is contradicted and the
        observed outcome is proposed (see `_causal_outcome`).
        `condition` names the condition the causal claim holds
        under ("" when unknown); `evidence_event_id` links the
        real ledger event that recorded the outcome.

        Section 7.5 guardrail: `verified` is True when an
        independent check backs the outcome, False when the
        caller knows it does not, and None (default) for a
        world observation, which is trusted as before. A record
        marked unverified, or one whose predicted or observed
        text carries an orphan number (a meaningful figure with
        no evidence), is quarantined instead of teaching: it is
        stored with `quarantined` True, no correction is made,
        no gap grows, and the return value reports the
        quarantine (`{"quarantined": True, ...}`) instead of a
        correction. Release it with `confirm_experience`.
        """
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        missing = [k for k in MISPREDICTION_KEYS if k not in record]
        if missing:
            raise ValueError(f"record is missing keys: {missing}")
        task = str(record["task"])
        if not task:
            raise ValueError("record task must be a non-empty string")
        if verified is None:
            context = record.get("context") or {}
            verified = record.get("verified", context.get("verified"))
        classification = classify_experience(
            predicted=record["predicted"], observed=record["observed"], verified=verified
        )
        if classification["quarantined"]:
            stored = self.quarantine_experience(
                {
                    "task": task,
                    "predicted": record["predicted"],
                    "observed": record["observed"],
                    "error": record["error"],
                    "context": record["context"],
                },
                reasons=classification["quarantine_reasons"],
            )
            return {
                "task": task,
                "quarantined": True,
                "experience_id": stored["id"],
                "verification": "quarantined",
                "quarantine_reasons": list(stored["quarantine_reasons"]),
            }
        experience = {
            "id": uuid.uuid4().hex,
            "task": task,
            "predicted": record["predicted"],
            "observed": record["observed"],
            "error": record["error"],
            "context": record["context"],
            "verification": classification["verification"],
            "quarantined": False,
            "quarantine_reasons": [],
        }
        self.experiences.append(experience)
        return self._teach_misprediction(
            task,
            record["predicted"],
            record["observed"],
            record["error"],
            experience["id"],
            condition=condition,
            evidence_event_id=evidence_event_id,
        )

    def record_feedback(
        self, task, verdict, *, correction=None, source="user", condition="", evidence_event_id=None
    ):
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
        the manual version. When this loop has a causal ledger,
        the outcome also feeds it: "correct" strengthens the
        hypothesis for the predicted outcome, "wrong"
        contradicts it and (when a correction is applied, never
        when it is held as a manual-edit proposal) proposes the
        correction. `condition` and `evidence_event_id` are the
        causal condition and the ledger event id, as in
        `record_misprediction`. Returns a result dict with `task`,
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
            "error": ("user feedback: answer was wrong" if verdict == "wrong" else None),
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
            self._causal_observe(
                task,
                predicted,
                consistent=True,
                condition=condition,
                links=self._causal_links(experience["id"], evidence_event_id),
            )
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
        links = self._causal_links(experience["id"], evidence_event_id)
        self._causal_observe(task, predicted, consistent=False, condition=condition, links=links)
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
        # Section 7.5 guardrail: a user correction whose figure
        # is an orphan number (no evidence behind it) is held
        # in quarantine instead of teaching. The "wrong" verdict
        # itself is user-confirmed, so the gap and the suspect
        # marking above stand; only the corrected figure is held
        # until a user releases it with `confirm_experience`.
        if find_orphan_numbers(str(correction)):
            experience["quarantined"] = True
            experience["verification"] = "quarantined"
            experience["quarantine_reasons"] = ["orphan_numbers"]
            proposal = {
                "id": uuid.uuid4().hex,
                "task": task,
                "correction": correction,
                "source": FEEDBACK_SOURCE,
                "feedback_source": source,
                "status": "quarantined",
                "conflicts_with_manual_edit": False,
                "quarantine_reasons": ["orphan_numbers"],
                "experience_id": experience["id"],
            }
            self.feedback_proposals.append(proposal)
            result["status"] = "quarantined"
            result["correction"] = correction
            result["quarantine_reasons"] = ["orphan_numbers"]
            result["experience_id"] = experience["id"]
            result["proposal_id"] = proposal["id"]
            return result
        correction = self._apply_feedback_correction(
            task,
            correction,
            experience["id"],
            source=source,
            condition=condition,
            evidence_event_id=evidence_event_id,
        )
        result["status"] = "applied"
        result["correction"] = correction
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
        for the task when one exists, otherwise the strongest
        active causal hypothesis for the task (when this loop
        has a ledger attached), otherwise `default`. This is
        the behaviour-change seam: the same call returns a
        different (corrected) answer after experience. A loop
        without a ledger behaves exactly as before.
        """
        correction = self.corrections.get(str(task))
        if correction is not None:
            return correction["observed"]
        for row in self.expect(str(task)):
            if row.get("effect_value") is not None:
                return row["effect_value"]
        return default

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
                task: dict(correction) for task, correction in self.corrections.items()
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
        self.corrections = {str(task): dict(correction) for task, correction in corrections.items()}
        self.experiences = [dict(exp) for exp in experiences]
        self.gaps = {}
        for task, correction in self.corrections.items():
            self.gaps[task] = {
                "mispredictions": int(correction.get("count", 1)),
                "last_error": correction.get("error"),
            }
        return self.loop_state()

    # -- curriculum: propose the next task from observed gaps ----

    def propose_next_task(self, exclude=None):
        """Propose the next learning task, or None when nothing gaps.

        Priority: the task with the most mispredictions first
        (ties broken by task name, deterministic), as a retry
        with the corrected model; then the oldest unverified
        draft skill, as a verification task. `exclude` is an
        optional set of task names to skip (the curriculum
        runner passes the tasks it already completed today, so
        an unverified completion whose gap stays open does not
        block the runner from moving on to the next gap).
        """
        excluded = {str(task) for task in (exclude or ())}
        gaps = {task: gap for task, gap in self.gaps.items() if task not in excluded}
        if gaps:
            task = min(gaps.items(), key=lambda item: (-item[1]["mispredictions"], item[0]))[0]
            return {
                "task": task,
                "reason": "misprediction_gap",
                "gap": dict(gaps[task]),
                "difficulty": "retry",
            }
        drafts = [
            s
            for s in self.skills.values()
            if s["status"] == "draft" and f"verify_skill:{s['name']}" not in excluded
        ]
        if drafts:
            skill = min(drafts, key=lambda s: s["created_order"])
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

    def draft_skill(
        self,
        task,
        code,
        *,
        name=None,
        verification=None,
        embedding_key=None,
        source_experience_ids=None,
        is_repair=False,
        now=None,
    ):
        """Draft a learned skill from a successful task.

        The draft is INACTIVE (status "draft", active False) and
        stays that way until the user approves it. `code` is the
        tested executable code: a callable, or a string when the
        caller verifies it through a sandbox executor. The
        embedding key indexes the skill for reuse: by default the
        embedding of the task description. Returns the skill dict.

        Section 7.5 guardrail: at most
        ``max_new_skills_per_week`` newly learned skills may be
        drafted per ISO week (``now`` selects the week, for
        tests); drafting past the cap raises ValueError. A
        repair draft (``is_repair=True``, from
        ``SkillRepairer``) maintains an existing skill and does
        not count toward the cap.
        """
        task = str(task)
        if not task:
            raise ValueError("task must be a non-empty string")
        if code is None:
            raise ValueError("code is required for a learned skill")
        if not is_repair:
            self.weekly_skill_cap.record(now)
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
            "holdout": {},
            "holdout_passed": False,
            "submitted_for_approval": False,
            "is_repair": bool(is_repair),
            "source_experience_ids": list(source_experience_ids or []),
            "conflicts_with_manual_edit": task in self.manual_edits,
            "created_order": len(self.skills),
        }
        self.skills[skill["id"]] = skill
        return self._public_skill(skill)

    def skills_drafted_this_week(self, now=None):
        """Newly learned skills drafted in the week of ``now``."""
        return self.weekly_skill_cap.drafted_this_week(now)

    def new_skills_remaining_this_week(self, now=None):
        """New skills still draftable in the week of ``now``."""
        return self.weekly_skill_cap.remaining_this_week(now)

    @staticmethod
    def _run_test_cases(code, test_cases, executor):
        """Run ``code`` against cases; return (passed, failed, details).

        Shared by ``verify_skill`` and ``evaluate_holdout`` so
        both judge code the same way. Callable code is called
        directly; string code needs an ``executor`` callable
        ``executor(code, case)`` and fails closed without one.
        """
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
            except Exception as exc:
                ok, result = False, f"error: {exc}"
            details.append({"case": case, "result": result, "passed": ok})
            if ok:
                passed += 1
            else:
                failed += 1
        return passed, failed, details

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
        passed, failed, details = self._run_test_cases(skill["code"], test_cases, executor)
        verified = failed == 0 and passed > 0
        skill["verified"] = verified
        skill["verification"] = {
            "passed": passed,
            "failed": failed,
            "details": details,
        }
        return {
            "skill_id": skill_id,
            "passed": passed,
            "failed": failed,
            "verified": verified,
            "details": details,
        }

    def evaluate_holdout(self, skill_id, holdout_cases, *, executor=None):
        """Evaluate a drafted skill on a holdout set.

        `holdout_cases` use the same shape as `verify_skill`
        cases, but they must be a different set: a holdout that
        repeats the verification cases is not a holdout, and
        raises ValueError. Callable code is called directly;
        string code needs an `executor` and stays unevaluated
        without one. Returns {"passed", "failed", "verified",
        "details"} and records the result on the skill as
        `holdout` / `holdout_passed`. A skill passes its
        holdout only when every case passes. This is the gate
        before `submit_for_approval`: a skill that fails its
        holdout can never be put to the user.
        """
        skill = self.skills.get(skill_id)
        if skill is None:
            raise KeyError(f"skill not found: {skill_id}")
        if not holdout_cases:
            raise ValueError("at least one holdout case is required")
        stored_cases = [
            detail.get("case")
            for detail in (skill.get("verification") or {}).get("details", [])
            if detail.get("case") is not None
        ]
        if stored_cases and _same_case_set(holdout_cases, stored_cases):
            raise ValueError("holdout cases must differ from the verification cases")
        passed, failed, details = self._run_test_cases(skill["code"], holdout_cases, executor)
        verified = failed == 0 and passed > 0
        skill["holdout"] = {
            "passed": passed,
            "failed": failed,
            "details": details,
        }
        skill["holdout_passed"] = verified
        return {
            "skill_id": skill_id,
            "passed": passed,
            "failed": failed,
            "verified": verified,
            "details": details,
        }

    def submit_for_approval(self, skill_id, holdout_cases=None, *, executor=None):
        """Submit a drafted skill for user approval.

        The pre-approval gate (plan section 7.5): the skill
        must be verified, and it must have passed a holdout
        evaluation. When `holdout_cases` are given they are
        evaluated first (see `evaluate_holdout`); otherwise a
        previously recorded passing holdout is required.
        Raises ValueError when the skill is rejected, conflicts
        with a manual user edit, is unverified, or has not
        passed its holdout. On success the skill is marked
        `submitted_for_approval` and the submission record is
        returned (and kept in `approval_submissions`).
        """
        skill = self.skills.get(skill_id)
        if skill is None:
            raise KeyError(f"skill not found: {skill_id}")
        if skill["status"] == "rejected":
            raise ValueError("a rejected skill cannot be submitted for approval")
        if skill.get("conflicts_with_manual_edit"):
            raise ValueError(
                "skill conflicts with a manual user edit and cannot be submitted for approval"
            )
        if not skill.get("verified"):
            raise ValueError("only a verified skill can be submitted for approval")
        if holdout_cases is not None:
            self.evaluate_holdout(skill_id, holdout_cases, executor=executor)
        if not skill.get("holdout_passed"):
            raise ValueError(
                "skill has not passed holdout evaluation; "
                "evaluate it on a holdout set before submitting it for approval"
            )
        skill["submitted_for_approval"] = True
        submission = {
            "skill_id": skill_id,
            "skill_name": skill["name"],
            "task": skill["task"],
            "status": "submitted_for_approval",
            "holdout": dict(skill["holdout"]),
        }
        self.approval_submissions.append(submission)
        return dict(submission)

    def activate_skill(
        self, skill_id, *, user_id=None, gate=None, approval_request_id=None, approved_by=None
    ):
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

        Section 7.5 guardrail: the skill must also have passed
        a holdout evaluation and been submitted for approval
        (see `submit_for_approval`) before it can be activated,
        even when the user already approved it. The approval
        check runs first, so a missing approval still raises
        PermissionError exactly as before.
        """
        skill = self.skills.get(skill_id)
        if skill is None:
            raise KeyError(f"skill not found: {skill_id}")
        if skill["status"] == "rejected":
            raise ValueError("a rejected skill cannot be activated")
        if skill.get("conflicts_with_manual_edit"):
            raise ValueError(
                "skill conflicts with a manual user edit and cannot be activated over it"
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
            raise PermissionError("learned skill needs user approval before activation")
        if not skill.get("holdout_passed") or not skill.get("submitted_for_approval"):
            raise ValueError(
                "skill must pass holdout evaluation and be submitted for approval before activation"
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
            user_id,
            f"{LEARNED_DRAFT_PREFIX}{skill['name']}",
            step,
            output_schema={"skill_id": skill["id"], "verified": skill["verified"]},
            required_checks=["user_approval"],
            source_event_id=None,
            dsn=dsn,
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
            "holdout": skill.get("holdout", {}),
            "holdout_passed": skill.get("holdout_passed", False),
            "submitted_for_approval": skill.get("submitted_for_approval", False),
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


def record_misprediction(record, *, condition="", evidence_event_id=None):
    """Record a prediction error as experience that corrects the model.

    `record` is a dict with keys `task`, `predicted`, `observed`,
    `error`, `context`. Returns the stored correction dict.
    Delegates to the default `LearningLoop` instance. `condition`
    and `evidence_event_id` feed the causal ledger, as in
    `LearningLoop.record_misprediction`.
    """
    return _DEFAULT_LOOP.record_misprediction(
        record, condition=condition, evidence_event_id=evidence_event_id
    )


def record_feedback(
    task, verdict, *, correction=None, source="user", condition="", evidence_event_id=None
):
    """Record user feedback about an answer for a task.

    `verdict` is "correct" or "wrong"; `correction` is the right
    value when the answer was wrong. Returns the result dict
    from the default `LearningLoop` instance (see
    `LearningLoop.record_feedback`). `condition` and
    `evidence_event_id` feed the causal ledger, as in
    `LearningLoop.record_feedback`.
    """
    return _DEFAULT_LOOP.record_feedback(
        task,
        verdict,
        correction=correction,
        source=source,
        condition=condition,
        evidence_event_id=evidence_event_id,
    )
