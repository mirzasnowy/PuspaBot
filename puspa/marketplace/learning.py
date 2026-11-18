"""Learn skills from chat + post-reply background review (G3).

Pattern rewritten from DuskBot ``skill-learn.ts`` + ``skill-review.ts``
(PolyForm Noncommercial, study only, not copied): the agent learns
from chat and reviews a skill after a reply, in the background.

Bounds (hard, tested):

- At most **3 changes** per review.
- The review runs with a **3-minute timeout** (180 seconds); a
  timeout abandons the review, it never blocks the turn.
- **3 consecutive failures discard the step**: after three failed
  reviews in a row the learning step is discarded (``status ==
  "discarded"``) and no further attempt runs until the state is
  reset.

Two entry points:

- :func:`learn_from_turn` — extract candidate skill changes from
  one finished turn (deterministic correction patterns by default;
  an injected ``llm_complete`` callable may propose changes as
  JSON instead — any failure falls back to the deterministic
  extractor, never to a faked proposal).
- :func:`review_after_reply` — run the review in a daemon thread
  after a reply (``sync=True`` runs inline, for tests). Each
  proposed change is applied through the injected ``apply_change``
  callable; a change the callable rejects counts as a failure.

Call-site note (NOT wired in): there is no clean seam from the
reply loop into this module yet — the runtime reply loop lives in
``puspa/runtime/`` (off-limits for this worker) and the G24
background review (``puspa/memory/background_review.py``) writes
facts, not skill changes. The follow-up is: wire
``review_after_reply`` into the reply loop's post-reply hook once
that hook exists (after the G24 wiring lands), passing the
registry's skill store as ``apply_change``. Until then this module
is tested standalone and must not be called from anywhere.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

__all__ = [
    "MAX_CHANGES_PER_REVIEW",
    "MAX_CONSECUTIVE_FAILURES",
    "REVIEW_TIMEOUT_SECONDS",
    "LearningStep",
    "ReviewOutcome",
    "learn_from_turn",
    "review_after_reply",
]

#: Maximum skill changes applied by one review.
MAX_CHANGES_PER_REVIEW = 3
#: Consecutive failed reviews before the step is discarded.
MAX_CONSECUTIVE_FAILURES = 3
#: Review timeout, seconds (3 minutes).
REVIEW_TIMEOUT_SECONDS = 180

#: Deterministic correction patterns: "skill X should ...",
#: "skill X is wrong ...", "fix skill X ...". Kept deliberately
#: narrow — a vague complaint is not a skill change.
_CORRECTION = re.compile(
    r"skill\s+([a-z0-9]+(?:-[a-z0-9]+)*)\s+"
    r"(?:should|is wrong|is incorrect|fix|needs?)\b\s*(.*)",
    re.IGNORECASE,
)


@dataclass
class LearningStep:
    """One learn-from-chat step, with its failure history."""

    step_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    skill_id: str = ""
    consecutive_failures: int = 0
    discarded: bool = False
    changes_applied: int = 0

    def record_failure(self) -> None:
        """Count a failure; discard the step at the third in a row."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.discarded = True

    def record_success(self) -> None:
        self.consecutive_failures = 0


@dataclass
class ReviewOutcome:
    """Outcome of one post-reply review."""

    status: str  # "completed" | "failed" | "timeout" | "discarded" | "skipped"
    changes_applied: int = 0
    changes_proposed: int = 0
    errors: list = field(default_factory=list)
    duration_seconds: float = 0.0
    step_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "changes_applied": self.changes_applied,
            "changes_proposed": self.changes_proposed,
            "errors": list(self.errors),
            "duration_seconds": self.duration_seconds,
            "step_id": self.step_id,
        }


def _deterministic_changes(messages: list, known_skill_ids: set) -> list[dict]:
    """Extract candidate changes from user messages (no LLM)."""
    changes: list[dict] = []
    seen = set()
    for message in messages or []:
        text = message.get("content", "") if isinstance(message, dict) else ""
        if not isinstance(text, str):
            continue
        for match in _CORRECTION.finditer(text):
            skill_id = match.group(1).lower()
            note = (match.group(2) or "").strip()[:500]
            if known_skill_ids and skill_id not in known_skill_ids:
                continue
            key = (skill_id, note)
            if key in seen:
                continue
            seen.add(key)
            changes.append(
                {
                    "skill_id": skill_id,
                    "note": note or "correction requested",
                    "source": "chat",
                }
            )
            if len(changes) >= MAX_CHANGES_PER_REVIEW:
                return changes
    return changes


def _llm_changes(messages: list, known_skill_ids: set, llm_complete):
    """Ask the LLM for up to 3 proposed changes. ``None`` on failure."""
    try:
        transcript = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}"
            for m in messages or []
            if isinstance(m, dict)
        )
        prompt = (
            "From this chat transcript, propose at most 3 concrete skill "
            "improvements. Reply with JSON only: "
            '{"changes": [{"skill_id": "...", "note": "..."}]}. '
            "Propose nothing when there is no concrete improvement.\n"
            f"Known skills: {sorted(known_skill_ids)}\n"
            f"Transcript:\n{transcript}"
        )
        raw = llm_complete(prompt)
        data = json.loads(raw)
        proposed = data.get("changes")
        if not isinstance(proposed, list):
            return None
        changes = []
        for item in proposed[:MAX_CHANGES_PER_REVIEW]:
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("skill_id", "")).strip().lower()
            note = str(item.get("note", "")).strip()[:500]
            if not skill_id or not note:
                continue
            if known_skill_ids and skill_id not in known_skill_ids:
                continue
            changes.append({"skill_id": skill_id, "note": note, "source": "llm"})
        return changes
    except Exception:
        return None


def learn_from_turn(messages: list, *, known_skill_ids=None, llm_complete=None) -> list[dict]:
    """Propose at most 3 skill changes learned from one turn.

    The LLM path runs first when ``llm_complete`` is given; any
    failure falls back to the deterministic extractor. No live LLM
    call is ever faked: without ``llm_complete`` only the
    deterministic patterns apply.
    """
    known = set(known_skill_ids or [])
    if llm_complete is not None:
        changes = _llm_changes(messages, known, llm_complete)
        if changes is not None:
            return changes
    return _deterministic_changes(messages, known)


class _ReviewHandle:
    """Handle for an async review (daemon thread)."""

    def __init__(self, thread, box, step_id=None):
        self._thread = thread
        self._box = box
        self.step_id = step_id

    def wait(self, timeout=REVIEW_TIMEOUT_SECONDS):
        """Wait for the review; ``"timeout"`` outcome when it overruns."""
        self._thread.join(timeout=timeout)
        result = self._box.get("result")
        if result is not None:
            return result
        return ReviewOutcome(
            status="timeout",
            errors=[f"review exceeded {timeout}s"],
            step_id=self.step_id,
        )

    @property
    def done(self):
        return not self._thread.is_alive()


def _run_review(changes, *, apply_change, step: LearningStep, box):
    """Apply at most 3 changes through ``apply_change`` (thread entry)."""
    started = time.monotonic()
    outcome = ReviewOutcome(status="completed", step_id=step.step_id)
    try:
        outcome.changes_proposed = len(changes)
        for change in changes[:MAX_CHANGES_PER_REVIEW]:
            try:
                applied = apply_change(change)
            except Exception as exc:
                outcome.errors.append(f"change for {change.get('skill_id')!r} failed: {exc}")
                continue
            if applied is False:
                outcome.errors.append(f"change for {change.get('skill_id')!r} was rejected")
                continue
            outcome.changes_applied += 1
            step.changes_applied += 1
        if outcome.errors and outcome.changes_applied == 0:
            step.record_failure()
            outcome.status = "discarded" if step.discarded else "failed"
        else:
            step.record_success()
            outcome.status = "completed"
    except Exception as exc:
        step.record_failure()
        outcome.status = "discarded" if step.discarded else "failed"
        outcome.errors.append(str(exc))
    outcome.duration_seconds = time.monotonic() - started
    box["result"] = outcome


def review_after_reply(
    messages: list,
    *,
    apply_change,
    step=None,
    known_skill_ids=None,
    llm_complete=None,
    sync=False,
    timeout_seconds=REVIEW_TIMEOUT_SECONDS,
):
    """Review one finished turn after the reply, in the background.

    ``apply_change(change) -> bool`` applies one proposed change
    (return ``False`` / raise to count it as failed).
    ``step`` carries the consecutive-failure count across reviews;
    a discarded step returns a ``"discarded"`` outcome immediately
    and runs nothing. ``sync=True`` runs inline (deterministic, for
    tests); otherwise a daemon thread is spawned and a handle is
    returned (``handle.wait(timeout_seconds)`` enforces the
    3-minute limit).
    """
    if step is None:
        step = LearningStep()
    if step.discarded:
        return ReviewOutcome(
            status="discarded",
            errors=[
                (
                    "learning step was discarded after "
                    f"{MAX_CONSECUTIVE_FAILURES} consecutive failures"
                )
            ],
            step_id=step.step_id,
        )
    changes = learn_from_turn(
        messages,
        known_skill_ids=known_skill_ids,
        llm_complete=llm_complete,
    )
    if not changes:
        return ReviewOutcome(status="skipped", step_id=step.step_id)
    box: dict = {}
    if sync:
        _run_review(changes, apply_change=apply_change, step=step, box=box)
        return box["result"]
    thread = threading.Thread(
        target=_run_review,
        args=(changes,),
        kwargs={"apply_change": apply_change, "step": step, "box": box},
        daemon=True,
        name=f"skill-review-{step.step_id[:8]}",
    )
    thread.start()
    return _ReviewHandle(thread, box, step_id=step.step_id)
