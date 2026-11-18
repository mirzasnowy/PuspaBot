"""Tests for failure recovery (recovery.py). In-memory."""

from puspa.scheduler.recovery import (
    MAX_REQUEUE,
    handle_failure,
    should_requeue,
)


def test_requeue_allowed_exactly_once():
    assert MAX_REQUEUE == 1
    assert should_requeue(0) is True
    assert should_requeue(1) is False
    assert should_requeue(2) is False


def test_first_failure_requeues_and_does_not_notify():
    requeued = []
    notified = []
    events = []
    record = handle_failure(
        job_id="cron-1",
        run_id="run-1",
        error="boom",
        attempts=0,
        sink=lambda t, c: events.append((t, c)),
        notify_owner=notified.append,
        requeue=lambda j, r: requeued.append((j, r)),
    )
    assert record.requeued is True
    assert record.owner_notified is False
    assert requeued == [("cron-1", "run-1")]
    assert notified == []
    assert [t for t, _ in events] == ["run_failed", "run_requeued"]


def test_second_failure_is_terminal_records_fact_and_notifies():
    notified = []
    events = []
    requeued = []
    record = handle_failure(
        job_id="cron-1",
        run_id="run-1",
        error="boom again",
        attempts=1,
        sink=lambda t, c: events.append((t, c)),
        notify_owner=notified.append,
        requeue=lambda j, r: requeued.append((j, r)),
    )
    assert record.requeued is False
    assert record.owner_notified is True
    assert requeued == []
    assert len(notified) == 1
    assert notified[0]["job_id"] == "cron-1"
    assert notified[0]["error"] == "boom again"
    assert [t for t, _ in events] == ["run_failed"]
    assert record.to_dict()["attempts"] == 1


def test_recovery_works_without_optional_callables():
    record = handle_failure(job_id="cron-1", run_id="run-1", error="boom", attempts=0)
    assert record.requeued is True
    record = handle_failure(job_id="cron-1", run_id="run-1", error="boom", attempts=1)
    assert record.requeued is False
    assert record.owner_notified is False
