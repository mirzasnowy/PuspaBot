"""The three question shapes a decider may ask, and their answers.

Pattern rewritten from DuskBot's ``server/decider/`` (PolyForm
Noncommercial, study only): a decider question is exactly one of
three shapes, and the answer always has the same shape as its
question. Nothing else is a decider question, so a caller can
never smuggle an open-ended generation task through this module.

- ``ChoiceQuestion``: pick exactly one option id.
- ``ScoreQuestion``: score every option from 0 to 1.
- ``YesNoQuestion``: yes or no, with a confidence from 0 to 1.

Questions are frozen dataclasses validated at construction: an
empty text, an empty option list, a duplicated option id, or a
score/confidence outside 0..1 is a ``ValueError`` here, not a
failure value later — building a malformed question is a caller
bug, while a backend failing to *answer* a well-formed question
is a decision failure (see :mod:`puspa.decider.decider`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

__all__ = [
    "ChoiceAnswer",
    "ChoiceOption",
    "ChoiceQuestion",
    "ScoreAnswer",
    "ScoreQuestion",
    "YesNoAnswer",
    "YesNoQuestion",
    "build_prompt",
]

#: The three question kinds, as stable strings.
QUESTION_KINDS = ("choice", "score", "yes_no")


def _new_id() -> str:
    """A short random question id (callers may pass their own)."""
    return uuid.uuid4().hex[:12]


def _check_confidence(value: float, what: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{what} must be a number, not {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{what} must be between 0 and 1, not {value!r}")
    return float(value)


@dataclass(frozen=True)
class ChoiceOption:
    """One option a choice/score question may pick or score.

    ``keywords`` are the deterministic backend's evidence: the
    words that suggest this option fits the question text. The
    LLM backend never sees them (it sees id + label only), so a
    rule answer and an LLM answer stay comparable.
    """

    id: str
    label: str = ""
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("option id must be a non-empty string")
        if not isinstance(self.label, str):
            raise ValueError("option label must be a string")
        object.__setattr__(self, "keywords", tuple(str(k) for k in self.keywords))

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "keywords": list(self.keywords)}


@dataclass(frozen=True)
class ChoiceQuestion:
    """Pick exactly one option id."""

    text: str
    options: tuple[ChoiceOption, ...]
    id: str = field(default_factory=_new_id)
    context: dict = field(default_factory=dict)

    kind: str = field(default="choice", init=False)

    def __post_init__(self) -> None:
        _check_question(self.text, self.options, self.id)
        object.__setattr__(self, "context", dict(self.context))

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "text": self.text,
            "options": [o.to_dict() for o in self.options],
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class ScoreQuestion:
    """Score every option from 0 to 1."""

    text: str
    options: tuple[ChoiceOption, ...]
    id: str = field(default_factory=_new_id)
    context: dict = field(default_factory=dict)

    kind: str = field(default="score", init=False)

    def __post_init__(self) -> None:
        _check_question(self.text, self.options, self.id)
        object.__setattr__(self, "context", dict(self.context))

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "text": self.text,
            "options": [o.to_dict() for o in self.options],
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class YesNoQuestion:
    """Yes or no, with a confidence from 0 to 1."""

    text: str
    id: str = field(default_factory=_new_id)
    context: dict = field(default_factory=dict)

    kind: str = field(default="yes_no", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("question text must be a non-empty string")
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("question id must be a non-empty string")
        object.__setattr__(self, "context", dict(self.context))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "text": self.text, "context": dict(self.context)}


def _check_question(text, options, question_id) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("question text must be a non-empty string")
    if not isinstance(question_id, str) or not question_id:
        raise ValueError("question id must be a non-empty string")
    if not options:
        raise ValueError("a choice/score question needs at least one option")
    seen = set()
    for option in options:
        if not isinstance(option, ChoiceOption):
            raise ValueError(f"options must be ChoiceOption, not {option!r}")
        if option.id in seen:
            raise ValueError(f"duplicated option id: {option.id!r}")
        seen.add(option.id)


@dataclass(frozen=True)
class ChoiceAnswer:
    """The answer to a ``ChoiceQuestion``."""

    choice: str
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.choice, str) or not self.choice:
            raise ValueError("choice must be a non-empty option id")
        object.__setattr__(self, "confidence", _check_confidence(self.confidence, "confidence"))

    def to_dict(self) -> dict:
        return {"kind": "choice", "choice": self.choice, "confidence": self.confidence}


@dataclass(frozen=True)
class ScoreAnswer:
    """The answer to a ``ScoreQuestion``: one score per option id."""

    scores: dict

    def __post_init__(self) -> None:
        if not isinstance(self.scores, dict) or not self.scores:
            raise ValueError("scores must be a non-empty dict")
        checked = {}
        for option_id, score in self.scores.items():
            if not isinstance(option_id, str) or not option_id:
                raise ValueError("score keys must be non-empty option ids")
            checked[option_id] = _check_confidence(score, f"score for {option_id!r}")
        object.__setattr__(self, "scores", checked)

    def to_dict(self) -> dict:
        return {"kind": "score", "scores": dict(self.scores)}


@dataclass(frozen=True)
class YesNoAnswer:
    """The answer to a ``YesNoQuestion``."""

    value: bool
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.value, bool):
            raise ValueError(f"value must be a bool, not {self.value!r}")
        object.__setattr__(self, "confidence", _check_confidence(self.confidence, "confidence"))

    def to_dict(self) -> dict:
        return {"kind": "yes_no", "value": self.value, "confidence": self.confidence}


def build_prompt(question) -> str:
    """Render a question as the JSON-only prompt an LLM backend sends.

    The reply contract is in the prompt itself, so the parser in
    :mod:`puspa.decider.backends` can stay strict: anything that
    is not the asked-for JSON object is an unparseable answer.
    """
    if isinstance(question, ChoiceQuestion):
        options = [{"id": o.id, "label": o.label or o.id} for o in question.options]
        return (
            "Choose exactly one option for this question. "
            "Reply with JSON only: "
            '{"choice": "<option id>", "confidence": 0.0-1.0}.\n'
            f"Question: {question.text}\n"
            f"Options: {json.dumps(options, ensure_ascii=False)}"
        )
    if isinstance(question, ScoreQuestion):
        options = [{"id": o.id, "label": o.label or o.id} for o in question.options]
        return (
            "Score every option from 0 to 1 for this question. "
            "Reply with JSON only: "
            '{"scores": {"<option id>": 0.0-1.0, ...}}.\n'
            f"Question: {question.text}\n"
            f"Options: {json.dumps(options, ensure_ascii=False)}"
        )
    if isinstance(question, YesNoQuestion):
        return (
            "Answer yes or no for this question. "
            "Reply with JSON only: "
            '{"answer": true/false, "confidence": 0.0-1.0}.\n'
            f"Question: {question.text}"
        )
    raise ValueError(f"unknown question type: {type(question).__name__}")
