"""The known decider jobs, each with its own seam.

Pattern rewritten from DuskBot's ``server/decider/`` (PolyForm
Noncommercial, study only): jobs are listed explicitly — a job
that is not in :data:`DECIDER_JOBS` is refused with reason
``unknown_job`` instead of being guessed at — and each job owns a
seam that turns a generic question into that job's specific
question and interprets the typed answer back into the job's
result. A new job is added only with its seam, never by
free-forming a question at a call site.

Jobs:

- ``moduleRouting``: route one message to one module. The
  payload carries the message plus the candidate modules (id,
  keywords, description); the seam asks a ``ChoiceQuestion``
  over the module ids and interprets the pick as
  ``{"module_id", "confidence"}``.
- ``inboxTriage``: triage one incoming message. The seam asks a
  ``ChoiceQuestion`` over the actions ``act_now`` /
  ``act_later`` / ``ignore`` and interprets the pick as
  ``{"action", "priority"}``; priority is derived from the
  sender and the action, not guessed by the model.
"""

from __future__ import annotations

import hashlib

from .backends import BackendError
from .questions import ChoiceAnswer, ChoiceOption, ChoiceQuestion

__all__ = [
    "DECIDER_JOBS",
    "JOB_SEAMS",
    "SeamError",
    "get_seam",
]

#: The only jobs a decider may run. Add a job only with its seam.
DECIDER_JOBS = ("moduleRouting", "inboxTriage")


class SeamError(Exception):
    """A job seam could not build its question. ``reason`` is a
    stable code the decider returns as a failure value."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


class _JobSeam:
    """One job: build its question, interpret its answer."""

    def __init__(self, build_question, interpret) -> None:
        self.build_question = build_question
        self.interpret = interpret


def _stable_id(prefix: str, text: str) -> str:
    """A deterministic question id: same input, same id, so the
    decision state hash is replayable across processes."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _module_routing_question(payload: dict) -> ChoiceQuestion:
    message = payload.get("message") if isinstance(payload, dict) else None
    modules = payload.get("modules") if isinstance(payload, dict) else None
    if not isinstance(message, str) or not message.strip():
        raise SeamError("invalid_payload", "moduleRouting needs a message")
    if not isinstance(modules, list) or not modules:
        raise SeamError("no_modules", "moduleRouting needs at least one module")
    options = []
    for module in modules:
        if not isinstance(module, dict) or not module.get("id"):
            raise SeamError("invalid_payload", "each module needs an id")
        options.append(
            ChoiceOption(
                id=str(module["id"]),
                label=str(module.get("description") or module["id"]),
                keywords=tuple(module.get("keywords") or ()),
            )
        )
    return ChoiceQuestion(
        text=message,
        options=tuple(options),
        id=_stable_id("module-routing", message),
        context={"job": "moduleRouting"},
    )


def _module_routing_interpret(answer, payload: dict) -> dict:
    if not isinstance(answer, ChoiceAnswer):
        raise BackendError("answer_type_mismatch", "moduleRouting expects a choice answer")
    return {"module_id": answer.choice, "confidence": answer.confidence}


#: Keywords that push a message toward acting now.
TRIAGE_ACT_NOW_KEYWORDS = (
    "urgent",
    "asap",
    "deadline",
    "today",
    "broken",
    "down",
    "payment due",
    "overdue",
    "failed",
    "error",
    "help",
)

#: Keywords that push a message toward acting later.
TRIAGE_ACT_LATER_KEYWORDS = (
    "next week",
    "when you have time",
    "fyi",
    "update",
    "reminder",
    "sometime",
    "eventually",
    "for later",
)

#: Keywords that push a message toward ignoring it.
TRIAGE_IGNORE_KEYWORDS = (
    "newsletter",
    "promo",
    "unsubscribe",
    "advertisement",
    "sale",
    "discount",
    "offer ends",
    "click here",
)


def _inbox_triage_question(payload: dict) -> ChoiceQuestion:
    if not isinstance(payload, dict):
        raise SeamError("invalid_payload", "inboxTriage needs a payload dict")
    subject = payload.get("subject") or ""
    body = payload.get("body") or ""
    if not isinstance(subject, str) or not isinstance(body, str):
        raise SeamError("invalid_payload", "inboxTriage subject/body must be strings")
    text = (subject + "\n" + body).strip()
    if not text:
        raise SeamError("invalid_payload", "inboxTriage needs a message")
    options = (
        ChoiceOption(id="act_now", label="Act now", keywords=TRIAGE_ACT_NOW_KEYWORDS),
        ChoiceOption(id="act_later", label="Act later", keywords=TRIAGE_ACT_LATER_KEYWORDS),
        ChoiceOption(id="ignore", label="Ignore", keywords=TRIAGE_IGNORE_KEYWORDS),
    )
    return ChoiceQuestion(
        text=text,
        options=options,
        id=_stable_id("inbox-triage", text),
        context={"job": "inboxTriage", "sender": str(payload.get("sender") or "")},
    )


def _inbox_triage_interpret(answer, payload: dict) -> dict:
    if not isinstance(answer, ChoiceAnswer):
        raise BackendError("answer_type_mismatch", "inboxTriage expects a choice answer")
    sender = str(payload.get("sender") or "")
    known = payload.get("known_senders") or []
    known_sender = sender in known
    if answer.choice == "ignore":
        priority = "low"
    elif answer.choice == "act_now":
        priority = "high" if known_sender else "medium"
    else:  # act_later
        priority = "medium" if known_sender else "low"
    return {"action": answer.choice, "priority": priority, "confidence": answer.confidence}


JOB_SEAMS = {
    "moduleRouting": _JobSeam(_module_routing_question, _module_routing_interpret),
    "inboxTriage": _JobSeam(_inbox_triage_question, _inbox_triage_interpret),
}


def get_seam(job: str) -> _JobSeam:
    """The seam for a known job, else a ``SeamError`` (unknown_job)."""
    seam = JOB_SEAMS.get(job)
    if seam is None:
        raise SeamError(
            "unknown_job", f"unknown decider job: {job!r} (known: {', '.join(DECIDER_JOBS)})"
        )
    return seam
