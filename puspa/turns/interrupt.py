"""Interruptible model calls (G21).

Pattern studied, then rewritten as Puspa's own code (see
``docs/reuse.md``): hermes-agent runs the HTTP request on a
background thread while the main thread waits for three things
— the response, the interrupt event, or the timeout. Only the
PATTERN is taken: no hermes-agent code is copied.

Rules, and they are absolute for this module:

- The request runs on a daemon thread. The main thread waits
  on the interrupt event and the result queue at the same time.
- When the user interrupts, the API thread is ABANDONED: its
  result is discarded when (if) it arrives, and NO partial
  response ever enters the history. The history is only ever
  extended with a complete reply — see ``puspa.turns.turn_recover``.
- A timeout is a failure of the call, not of the turn: the
  caller decides (the loop ends the turn with a clear message).
- The underlying HTTP request cannot be cancelled from here —
  only abandoned. The daemon thread dies with the process; a
  late response is dropped by the queue check, never delivered.

``run_interruptible`` takes any zero-argument callable, so the
turn loop can wrap a whole fallback chain (all tiers) in one
interruptible call, not just a single HTTP request.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Callable
from typing import Any

__all__ = [
    "TurnInterrupted",
    "TurnTimeout",
    "run_interruptible",
]


class TurnInterrupted(Exception):
    """The user interrupted the turn mid-call: the reply is discarded."""

    def __init__(self, message: str = "turn interrupted by the user") -> None:
        super().__init__(message)


class TurnTimeout(Exception):
    """One model call ran past its timeout (all tiers together)."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"model call timed out after {timeout_seconds:g}s")


def _wait_for_result(
    result_queue: queue.Queue[Any],
    interrupt_event: threading.Event | None,
    timeout: float | None,
) -> tuple[str, Any]:
    """Wait for the result, an interrupt, or the timeout.

    Returns ``("ready", value)``, ``("interrupted", None)``, or
    ``("timeout", None)``. Polls in short slices so an interrupt
    is noticed promptly even with a long timeout.
    """
    deadline = None if timeout is None else timeout
    while True:
        wait = 0.05 if deadline is None else min(0.05, deadline)
        if wait <= 0:
            return "timeout", None
        try:
            kind, payload = result_queue.get(timeout=wait)
        except queue.Empty:
            kind, payload = None, None
        if kind is not None:
            return kind, payload
        if interrupt_event is not None and interrupt_event.is_set():
            return "interrupted", None
        if deadline is not None:
            deadline -= wait
            if deadline <= 0:
                # One last non-blocking drain: a result that landed
                # exactly on the deadline still counts as ready.
                try:
                    kind, payload = result_queue.get_nowait()
                except queue.Empty:
                    return "timeout", None
                return kind, payload


def run_interruptible(
    call_fn: Callable[[], Any],
    *,
    interrupt_event: threading.Event | None = None,
    timeout: float | None = None,
) -> Any:
    """Run ``call_fn`` on a background thread; wait for it, an
    interrupt, or a timeout.

    Returns the callable's return value. Raises
    :class:`TurnInterrupted` when the event fires first (the
    background result is discarded whenever it arrives) and
    :class:`TurnTimeout` when the deadline passes first. An
    exception raised by ``call_fn`` is re-raised unchanged on the
    calling thread.
    """
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def _target() -> None:
        try:
            result_queue.put(("ready", call_fn()))
        except BaseException as exc:
            with contextlib.suppress(queue.Full):
                result_queue.put(("error", exc))

    thread = threading.Thread(target=_target, daemon=True, name="turn-model-call")
    thread.start()
    status, payload = _wait_for_result(result_queue, interrupt_event, timeout)
    if status == "ready":
        return payload
    if status == "error":
        raise payload
    if status == "interrupted":
        raise TurnInterrupted()
    raise TurnTimeout(timeout if timeout is not None else 0.0)
