"""Run the scheduler tick on a timer, with no human present.

This is the process that makes scheduled work actually happen:
it calls :func:`puspa.scheduler.scheduler.tick` every minute
(or the interval given), passing a turn loop, so due triggers
fire and their jobs run. System jobs (backup, consolidation,
dream, sentinel scan, trust-root and revocation refresh,
ontology review) execute directly; user routines run through
the turn loop, the same loop as a chat turn.

Run it with ``python -m puspa.scheduler.serve``. It runs until
it is stopped (Ctrl-C, or the stop event in tests). One failing
tick never kills the loop: the error is reported through the
sink and the loop waits for the next interval.

With no turn loop passed in, a default :class:`TurnLoop` is
built, using the live model adapter (the router's OpenRouter
caller). Tests pass a fake loop instead, so no live model call
ever happens in a test.
"""

from __future__ import annotations

import argparse
import contextlib
import signal
import sys
import threading

from .scheduler import tick

__all__ = ["main", "run_scheduler_loop"]

#: How long to wait between ticks by default (one minute).
DEFAULT_INTERVAL_SECONDS = 60.0


def run_scheduler_loop(
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    stop_event: threading.Event | None = None,
    max_ticks: int | None = None,
    turn_loop=None,
    sink=None,
    dsn=None,
    budget=None,
    kill_switch=None,
) -> int:
    """Call :func:`tick` on a timer until stopped.

    Returns the number of ticks that ran. ``max_ticks`` caps the
    run (used by tests; a live run passes none). A tick that
    raises is reported through ``sink`` as
    ``scheduler_tick_failed`` and the loop carries on. With
    ``turn_loop`` left as None, the default live turn loop is
    built lazily, on the first tick only.
    """
    stop = stop_event if stop_event is not None else threading.Event()
    loop = turn_loop
    ticks = 0
    while not stop.is_set():
        if max_ticks is not None and ticks >= max_ticks:
            break
        if loop is None:
            from puspa.turns.loop import TurnLoop

            # COW-08: the loop gets the DSN so a routine turn for a
            # user runs through that user's routed chat provider
            # (no DSN means the old default adapter, as before).
            loop = TurnLoop(dsn=dsn)
        try:
            runs = tick(
                sink=sink,
                dsn=dsn,
                budget=budget,
                kill_switch=kill_switch,
                turn_loop=loop,
            )
        except Exception as exc:
            if sink is not None:
                with contextlib.suppress(Exception):
                    sink(
                        "scheduler_tick_failed",
                        {"error": f"{type(exc).__name__}: {exc}"},
                    )
            runs = []
        if sink is not None:
            # Job outcomes ride on the tick's returned runs (the
            # tick's own sink stays reserved for trigger ledger
            # events); report each one here, best-effort.
            for run in runs or []:
                outcome = (run or {}).get("job") or {}
                status = outcome.get("status")
                if status == "completed":
                    event = "scheduled_job_completed"
                elif status == "failed":
                    event = "scheduled_job_failed"
                else:
                    event = "scheduled_job_skipped"
                with contextlib.suppress(Exception):
                    sink(event, dict(outcome))
        ticks += 1
        stop.wait(timeout=max(0.0, float(interval_seconds)))
    return ticks


def main(argv: list[str] | None = None) -> int:
    """Command entry point: ``python -m puspa.scheduler.serve``."""
    parser = argparse.ArgumentParser(prog="puspa.scheduler.serve")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help="seconds between ticks (default 60)",
    )
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: PUSPA_TEST_DSN)")
    args = parser.parse_args(argv)
    stop = threading.Event()

    def _stop(*_args) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGTERM, _stop)
    print(
        f"scheduler loop running (tick every {args.interval:g}s); Ctrl-C to stop.",
        flush=True,
    )
    run_scheduler_loop(
        interval_seconds=args.interval,
        stop_event=stop,
        dsn=args.dsn,
        sink=lambda event_type, content: print(
            f"[{event_type}] {content}", file=sys.stderr, flush=True
        ),
    )
    print("scheduler loop stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
