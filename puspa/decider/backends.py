"""Pluggable backends that answer decider questions.

Pattern rewritten from DuskBot's ``server/decider/`` (PolyForm
Noncommercial, study only): the backend is swappable, and every
backend failure is a ``BackendError`` the decider turns into a
failure *value* — a backend never throws into the turn.

Backends:

- ``RuleBackend`` — deterministic keyword overlap. Always
  available (its key-check always passes), so it is the test
  backend and the last link of every fallback chain.
- ``LLMBackend`` — the router's cheapest reader model through a
  ``complete(prompt)`` callable (see
  ``puspa.router.complete.default_llm_complete``). Strict JSON
  parsing: a reply that is not the asked-for object, names an
  unknown option, or carries a score outside 0..1 is a
  ``BackendError`` with reason ``unparseable_answer``.
- ``UserEngineBackend`` — the ``dusk-chooser`` alternative: the
  small model comes from the user's own engine instead of the
  router. It takes an ``engine_complete(prompt)`` callable; with
  none configured it fails fast with reason ``no_user_engine``.
  No engine seam exists in the repo today (see the G9 report),
  so this backend is implemented and tested with an injected
  callable and its live wiring is a follow-up.

A backend may declare ``timeout_ms``: the decider enforces that
budget for the backend instead of its own default. The chooser
backend defaults to 8.000 ms (DuskBot's ``dusk-chooser`` budget);
the others use the decider default of 1.500 ms.
"""

from __future__ import annotations

import json
import re

from .questions import (
    ChoiceAnswer,
    ChoiceQuestion,
    ScoreAnswer,
    ScoreQuestion,
    YesNoAnswer,
    YesNoQuestion,
    build_prompt,
)

__all__ = [
    "BackendError",
    "LLMBackend",
    "RuleBackend",
    "UserEngineBackend",
    "parse_reply",
]

#: Reason when a backend has no usable answer at all.
NO_ANSWER = "no_answer"

_WORD = re.compile(r"[a-z0-9]+")


class BackendError(Exception):
    """One backend failed to answer. ``reason`` is a stable code.

    Reasons: ``unparseable_answer`` (the reply is not the
    asked-for JSON, or names an unknown option), ``no_user_engine``
    (the chooser backend has no engine callable),
    ``backend_error`` (the completion call itself raised).
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _overlap(question_text: str, keywords) -> int:
    """Keyword hits of one option against the question text."""
    words = _words(question_text)
    hits = 0
    for keyword in keywords or []:
        key_words = _words(str(keyword))
        if key_words and key_words & words:
            hits += 1
        elif str(keyword).lower() in question_text.lower():
            hits += 1
    return hits


def parse_reply(question, raw: str):
    """Parse an LLM reply into the answer matching ``question``.

    Strict: ``raw`` must be one JSON object with exactly the
    fields :func:`puspa.decider.questions.build_prompt` asked for.
    Unknown option ids, scores outside 0..1, and a confidence
    outside 0..1 are all ``BackendError`` (``unparseable_answer``)
    — never a best-effort guess.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise BackendError(
            "unparseable_answer",
            f"reply is not JSON: {str(exc)[:120]}") from exc
    if not isinstance(data, dict):
        raise BackendError(
            "unparseable_answer", "reply is not a JSON object")
    if isinstance(question, ChoiceQuestion):
        choice = data.get("choice")
        known = {o.id for o in question.options}
        if not isinstance(choice, str) or choice not in known:
            raise BackendError(
                "unparseable_answer",
                f"choice {choice!r} is not one of the options")
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError) as exc:
            raise BackendError(
                "unparseable_answer", "confidence is not a number") from exc
        if not 0.0 <= confidence <= 1.0:
            raise BackendError(
                "unparseable_answer", "confidence outside 0..1")
        return ChoiceAnswer(choice=choice, confidence=confidence)
    if isinstance(question, ScoreQuestion):
        scores = data.get("scores")
        if not isinstance(scores, dict):
            raise BackendError(
                "unparseable_answer", "scores is not an object")
        known = {o.id for o in question.options}
        if set(scores) != known:
            raise BackendError(
                "unparseable_answer",
                "scores must cover exactly the options")
        checked = {}
        for option_id, score in scores.items():
            try:
                value = float(score)
            except (TypeError, ValueError) as exc:
                raise BackendError(
                    "unparseable_answer",
                    f"score for {option_id!r} is not a number") from exc
            if not 0.0 <= value <= 1.0:
                raise BackendError(
                    "unparseable_answer",
                    f"score for {option_id!r} outside 0..1")
            checked[option_id] = value
        return ScoreAnswer(scores=checked)
    if isinstance(question, YesNoQuestion):
        value = data.get("answer")
        if not isinstance(value, bool):
            raise BackendError(
                "unparseable_answer", "answer is not true/false")
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError) as exc:
            raise BackendError(
                "unparseable_answer", "confidence is not a number") from exc
        if not 0.0 <= confidence <= 1.0:
            raise BackendError(
                "unparseable_answer", "confidence outside 0..1")
        return YesNoAnswer(value=value, confidence=confidence)
    raise BackendError(
        "unparseable_answer",
        f"unknown question type: {type(question).__name__}")


class RuleBackend:
    """Deterministic keyword-overlap backend. No key, no network.

    - Choice: the option with the most keyword hits wins; a tie
      goes to the earliest option. Confidence is the winner's
      margin over the runner-up — ``(best - second) / best`` —
      so a lone hit still counts as evidence (1.0 when no other
      option scored at all) while a tie at the top scores 0.0:
      the pick was arbitrary. With no hits anywhere the first
      option wins at 0.0 — the rule backend always answers, and
      the low confidence says the answer is a guess.
    - Score: each option's hits divided by its keyword count
      (0.5 with no keywords), so every option gets a real score.
    - Yes/no: the question ``context`` may carry ``yes_keywords``
      and ``no_keywords`` lists; a hit on either side decides,
      yes winning a tie. With no hit the context ``default``
      (default False) answers at confidence 0.0.
    """

    name = "rule"
    timeout_ms = None

    def ask(self, question):
        if isinstance(question, ChoiceQuestion):
            return self._choice(question)
        if isinstance(question, ScoreQuestion):
            return self._score(question)
        if isinstance(question, YesNoQuestion):
            return self._yes_no(question)
        raise BackendError(
            "unparseable_answer",
            f"unknown question type: {type(question).__name__}")

    def _choice(self, question: ChoiceQuestion) -> ChoiceAnswer:
        best = question.options[0]
        best_hits = -1
        second_hits = 0
        for option in question.options:
            hits = _overlap(question.text, option.keywords)
            if hits > best_hits:
                second_hits = best_hits if best_hits > 0 else 0
                best, best_hits = option, hits
            elif hits > second_hits:
                second_hits = hits
        if best_hits <= 0:
            confidence = 0.0
        else:
            confidence = (best_hits - second_hits) / best_hits
        return ChoiceAnswer(choice=best.id, confidence=confidence)

    def _score(self, question: ScoreQuestion) -> ScoreAnswer:
        scores = {}
        for option in question.options:
            hits = _overlap(question.text, option.keywords)
            if not option.keywords:
                scores[option.id] = 0.5
            else:
                scores[option.id] = hits / len(option.keywords)
        return ScoreAnswer(scores=scores)

    def _yes_no(self, question: YesNoQuestion) -> YesNoAnswer:
        context = question.context or {}
        yes_hits = _overlap(question.text, context.get("yes_keywords") or [])
        no_hits = _overlap(question.text, context.get("no_keywords") or [])
        if yes_hits or no_hits:
            return YesNoAnswer(
                value=yes_hits >= no_hits,
                confidence=1.0 if yes_hits != no_hits else 0.5)
        return YesNoAnswer(
            value=bool(context.get("default", False)), confidence=0.0)

    def check_key(self):
        """The rule backend needs no credential: always ready."""
        return True, None


class LLMBackend:
    """The router's cheapest reader model, via ``complete(prompt)``.

    ``complete`` is any ``prompt -> str`` callable, normally
    ``puspa.router.complete.default_llm_complete(role="reader")``.
    Use :meth:`from_router` to build one with the live router
    (the caller's timeout is passed through, so a slow model
    cannot outlive the decider budget). A completion failure —
    missing key, transport error, empty reply — becomes a
    ``BackendError``; the decider turns it into a failure value
    and falls through to the next backend.
    """

    name = "llm"
    timeout_ms = None

    def __init__(self, complete, *, key_resolver=None) -> None:
        if not callable(complete):
            raise ValueError("complete must be a callable")
        self._complete = complete
        self._key_resolver = key_resolver

    @classmethod
    def from_router(cls, *, role: str = "reader",
                    timeout: float = 1.5, **kwargs):
        """Build the backend on the live router completion.

        ``timeout`` bounds one completion call; extra keyword
        arguments pass through to ``complete`` (``model=``,
        ``api_key=``, ``transport=``, ``key_resolver=``, ...).
        Building makes no network call and needs no key; the key
        is resolved on the first call.
        """
        from puspa.router.complete import default_llm_complete

        complete = default_llm_complete(
            role=role, timeout=timeout, **kwargs)
        return cls(complete,
                   key_resolver=kwargs.get("key_resolver"))

    def ask(self, question):
        try:
            raw = self._complete(build_prompt(question))
        except Exception as exc:  # noqa: BLE001 - any failure is a value
            raise BackendError(
                "backend_error",
                f"completion failed: {type(exc).__name__}: {exc}") from exc
        if not isinstance(raw, str) or not raw.strip():
            raise BackendError("unparseable_answer", "empty completion")
        return parse_reply(question, raw)

    def check_key(self):
        """Check the credential before calling, so a missing key
        fails fast with a clear reason instead of a timeout.

        An injected ``key_resolver`` is tried first (tests). With
        none, the router's BYOK resolver is tried: a ``KeyError``
        there means no usable key. Any other error is reported
        as-is instead of being mistaken for a missing key.
        """
        if self._key_resolver is not None:
            try:
                key = self._key_resolver("openrouter")
            except Exception as exc:  # noqa: BLE001 - key check must not throw
                return False, f"key_resolver failed: {exc}"
            if not key or not str(key).strip():
                return False, "missing_api_key"
            return True, None
        try:
            from puspa.router.complete import resolve_api_key

            resolve_api_key("openrouter")
        except KeyError:
            return False, "missing_api_key"
        except Exception as exc:  # noqa: BLE001 - key check must not throw
            return False, f"key check failed: {exc}"
        return True, None


class UserEngineBackend:
    """The chooser alternative: a small model from the user's engine.

    ``engine_complete`` is a ``prompt -> str`` callable owned by
    the user's engine (8.000 ms budget, the DuskBot
    ``dusk-chooser`` timeout). With no callable configured the
    backend fails fast with reason ``no_user_engine`` — the
    decider then falls through to the next backend instead of
    waiting on an engine that is not there. No engine seam
    exists in the repo today, so live wiring is a follow-up; the
    backend itself is real and tested with an injected callable.
    """

    name = "user_engine"
    timeout_ms = 8000

    def __init__(self, engine_complete=None) -> None:
        if engine_complete is not None and not callable(engine_complete):
            raise ValueError("engine_complete must be a callable or None")
        self._engine_complete = engine_complete

    def ask(self, question):
        if self._engine_complete is None:
            raise BackendError(
                "no_user_engine",
                "no user engine configured for the chooser backend")
        try:
            raw = self._engine_complete(build_prompt(question))
        except Exception as exc:  # noqa: BLE001 - any failure is a value
            raise BackendError(
                "backend_error",
                f"engine call failed: {type(exc).__name__}: {exc}") from exc
        if not isinstance(raw, str) or not raw.strip():
            raise BackendError("unparseable_answer", "empty engine reply")
        return parse_reply(question, raw)

    def check_key(self):
        """Ready only when an engine callable is configured."""
        if self._engine_complete is None:
            return False, "no_user_engine"
        return True, None
