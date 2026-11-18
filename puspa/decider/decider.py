"""The decider: cheap decisions under a planner and world model.

Pattern rewritten from DuskBot's ``server/decider/`` (PolyForm
Noncommercial, study only). The decider sits *below* the
planner (``puspa.world.planner``) and the world model
(``puspa.world.model``): it answers one small question — route
this message, triage this inbox item — with the cheapest model
that can, and never with the big model the planner uses. It does
not plan and does not simulate; it decides.

Contract (the whole point of the module):

- A decision is one known job (see :mod:`puspa.decider.jobs`),
  asked as one of the three question shapes, answered in the
  same shape, through a chain of backends.
- Default timeout 1.500 ms per backend, enforced two ways: the
  backend runs on a daemon thread joined at the budget, and the
  elapsed time (from an injectable clock, so tests use a fake
  one) is checked after the call. A backend that overruns is a
  ``timeout`` failure, not a hung turn.
- The key-check runs before each backend call, so a backend
  with no credential fails fast instead of timing out.
- ``decide`` NEVER throws to the turn: every failure — unknown
  job, bad payload, missing key, timeout, unparseable answer,
  unknown backend — is a ``{"ok": False, "reason": ...}``
  decision value, with the per-backend attempts listed so the
  caller can see which link of the chain broke.
- The fallback chain runs in order (LLM first, rule last by
  default) until one backend answers; the answering backend is
  named on the decision, and every decision is written to the
  log with its state hash when a log is attached.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .backends import BackendError, RuleBackend
from .jobs import get_seam
from .keycheck import check_backend
from .log import DecisionLog, state_hash
from .questions import ChoiceAnswer, ScoreAnswer, YesNoAnswer

__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "Decider",
    "Decision",
    "default_backends",
]

#: Default per-backend budget, milliseconds (DuskBot: 1.500 ms).
DEFAULT_TIMEOUT_MS = 1500

#: Answer type each question kind must produce.
_ANSWER_TYPES = {
    "choice": ChoiceAnswer,
    "score": ScoreAnswer,
    "yes_no": YesNoAnswer,
}


@dataclass
class Decision:
    """The result of one ``decide`` call — success or failure value.

    ``ok`` False always carries ``reason`` (a stable code) and
    ``value`` None; ``ok`` True always carries ``value`` (the
    seam's interpreted result) and ``reason`` None. ``backend``
    names the backend that answered (None when none did).
    ``attempts`` lists one ``{"backend", "ok", "reason"}`` per
    backend tried. ``state_hash`` is the audit hash (see
    :mod:`puspa.decider.log`).
    """

    ok: bool
    job: str
    value: dict | None = None
    reason: str | None = None
    backend: str | None = None
    latency_ms: float = 0.0
    state_hash: str | None = None
    attempts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "job": self.job,
            "value": self.value,
            "reason": self.reason,
            "backend": self.backend,
            "latency_ms": self.latency_ms,
            "state_hash": self.state_hash,
            "attempts": list(self.attempts),
        }


def default_backends(llm_complete=None, *, engine_complete=None):
    """The default chain: LLM first, user engine next, rule last.

    ``llm_complete`` is a ``complete(prompt)`` callable (see
    ``puspa.router.complete.default_llm_complete``); without it
    the chain is engine + rule. ``engine_complete`` wires the
    chooser backend; without it that link fails fast with
    ``no_user_engine`` and the chain moves on. The rule backend
    is always last: it needs no key and always answers, so a
    chain ending in it only fails when the question itself is
    broken.
    """
    from .backends import LLMBackend, UserEngineBackend

    chain = []
    if llm_complete is not None:
        chain.append(LLMBackend(llm_complete))
    chain.append(UserEngineBackend(engine_complete))
    chain.append(RuleBackend())
    return chain


class Decider:
    """Decide one known job through a backend chain.

    ``backends`` is the ordered chain (see
    :func:`default_backends`). ``timeout_ms`` is the default
    per-backend budget; a backend with its own ``timeout_ms``
    overrides it for that link. ``clock`` is a ``monotonic()``
    callable (``time.monotonic`` by default); tests inject a
    fake clock. ``log`` is an optional :class:`DecisionLog`.
    """

    def __init__(self, backends=None, *, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                 clock=None, log: DecisionLog | None = None) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self.backends = list(backends) if backends else [RuleBackend()]
        if not self.backends:
            raise ValueError("at least one backend is required")
        self.timeout_ms = timeout_ms
        self.clock = clock if clock is not None else time.monotonic
        self.log = log

    def decide(self, job: str, payload: dict) -> Decision:
        """Decide ``job`` for ``payload``. Never raises.

        Returns a :class:`Decision`: success carries the seam's
        interpreted result; failure carries a stable ``reason``.
        """
        started = self.clock()
        attempts: list[dict] = []

        def finish(ok, *, value=None, reason=None, backend=None,
                   question=None) -> Decision:
            latency = (self.clock() - started) * 1000.0
            digest = state_hash(job=job, question=question,
                                backend=backend, value=value,
                                reason=reason)
            decision = Decision(
                ok=ok, job=job, value=value, reason=reason,
                backend=backend, latency_ms=latency,
                state_hash=digest, attempts=attempts)
            if self.log is not None:
                self.log.record(decision.to_dict(), question)
            return decision

        try:
            seam = get_seam(job)
        except Exception as exc:  # noqa: BLE001 - decide never throws
            reason = getattr(exc, "reason", "unknown_job")
            return finish(False, reason=reason)
        try:
            question = seam.build_question(payload)
        except Exception as exc:  # noqa: BLE001 - decide never throws
            reason = getattr(exc, "reason", "invalid_payload")
            return finish(False, reason=reason)

        for backend in self.backends:
            name = getattr(backend, "name", type(backend).__name__)
            budget_ms = getattr(backend, "timeout_ms", None) or self.timeout_ms
            ready = check_backend(backend)
            if not ready["ok"]:
                attempts.append({"backend": name, "ok": False,
                                 "reason": ready["reason"]})
                continue
            outcome, answer = self._ask_with_timeout(
                backend, question, budget_ms)
            if outcome == "timeout":
                attempts.append({"backend": name, "ok": False,
                                 "reason": "timeout"})
                continue
            if outcome == "error":
                reason = getattr(answer, "reason", "backend_error")
                attempts.append({"backend": name, "ok": False,
                                 "reason": reason})
                continue
            expected = _ANSWER_TYPES.get(question.kind)
            if expected is not None and not isinstance(answer, expected):
                attempts.append({"backend": name, "ok": False,
                                 "reason": "answer_type_mismatch"})
                continue
            try:
                value = seam.interpret(answer, payload)
            except Exception as exc:  # noqa: BLE001 - decide never throws
                reason = getattr(exc, "reason", "interpret_failed")
                attempts.append({"backend": name, "ok": False,
                                 "reason": reason})
                continue
            attempts.append({"backend": name, "ok": True, "reason": None})
            return finish(True, value=value, backend=name,
                          question=question)
        last_reason = attempts[-1]["reason"] if attempts else "no_backends"
        return finish(False, reason=last_reason, question=question)

    def _ask_with_timeout(self, backend, question, budget_ms: int):
        """Run one backend call inside the budget.

        Returns ``("ok", answer)``, ``("timeout", None)``, or
        ``("error", exception)``. The call runs on a daemon
        thread joined at the budget, so a hung backend cannot
        hold the turn past the budget; the injected clock is
        then checked too, so a fake clock can prove the timeout
        path without waiting.
        """
        started = self.clock()
        box: dict = {}

        def run() -> None:
            try:
                box["answer"] = backend.ask(question)
            except BaseException as exc:  # noqa: BLE001 - captured, not raised
                box["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(budget_ms / 1000.0)
        if thread.is_alive():
            return "timeout", None
        elapsed_ms = (self.clock() - started) * 1000.0
        if elapsed_ms > budget_ms:
            return "timeout", None
        if "error" in box:
            return "error", box["error"]
        return "ok", box.get("answer")
