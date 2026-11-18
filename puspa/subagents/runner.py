"""Child execution: context, cooperative cancellation, runner (G25).

A child runs through a ``child_fn`` callable ``(ctx) -> str``. The
child cooperates with cancellation by calling ``ctx.boundary()`` at
each safe iteration boundary; the boundary raises
:class:`ChildCancelled` when a cancel was requested, and the
lifecycle turns that into the terminal ``CANCELLED`` state. A child
that never reaches a boundary keeps running: cancel is cooperative
by design, and ``wait`` never reports a terminal state it has not
seen.

The runner is injectable so tests can drive the child body
deterministically: the default :class:`ThreadRunner` starts a
daemon thread; a test runner may capture the body and run it later,
which is how ``PENDING`` is observed without a race.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .records import ChildRecord

__all__ = [
    "ChildCancelled",
    "ChildContext",
    "Runner",
    "ThreadRunner",
]


class ChildCancelled(Exception):
    """Raised at an iteration boundary when cancel was requested."""


@dataclass
class ChildContext:
    """What a running child may do: check cancel, drain steer, charge cost.

    ``steer`` text queued by the parent is drained here, at the
    next iteration boundary — never injected mid-step. Cost is
    recorded in rupiah and rolled up to the parent budget when the
    child finishes.
    """

    record: ChildRecord
    cancel_event: threading.Event
    lock: threading.Lock

    def should_stop(self) -> bool:
        """True when a cancel was requested (checked at a boundary)."""
        return self.cancel_event.is_set()

    def boundary(self) -> None:
        """Safe iteration boundary: raise when a cancel was requested."""
        if self.cancel_event.is_set():
            raise ChildCancelled("cancel requested at an iteration boundary")

    def drain_steer(self) -> list[str]:
        """Take all queued steered texts, in order, clearing the queue."""
        with self.lock:
            queued = list(self.record.steer_queue)
            self.record.steer_queue.clear()
            return queued

    def record_cost(self, rupiah: int) -> None:
        """Charge rupiah to this child (rolled up at finish)."""
        if rupiah < 0:
            raise ValueError("cost must not be negative")
        with self.lock:
            self.record.cost_rp += rupiah


class Runner(Protocol):
    """Runs the child body. Called once per launch, by the lifecycle."""

    def __call__(self, record: ChildRecord, run_child: Callable[[], None]) -> None: ...


class ThreadRunner:
    """Default runner: the child body runs on one daemon thread."""

    def __call__(self, record: ChildRecord, run_child: Callable[[], None]) -> None:
        thread = threading.Thread(
            target=run_child,
            daemon=True,
            name=f"subagent-{record.subagent_id}",
        )
        thread.start()
