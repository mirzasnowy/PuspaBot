"""Outcome watcher (Kosmos K8): late failures, detector, verifier.

No live provider, no network, no database: the watcher is
in-memory, the writer and the learning loop are fakes (or a
fresh in-memory loop), and the detector reads synthetic ledger
events. Only modules with a clean import chain on the scrubbed
main are imported here, so these tests run with --noconftest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from puspa.memory.learning_loop import LearningLoop
from puspa.sentinel.detectors import (
    DETECTORS,
    detect_outcome_failures,
    outcome_failure_issue,
    run_detectors,
)
from puspa.sentinel.outcome_watcher import (
    DEFAULT_RECHECK_DELAY,
    CheckResult,
    OutcomeWatcher,
)
from puspa.sentinel.scanner import Sentinel
from puspa.sentinel.thresholds import SentinelThresholds
from puspa.sentinel.verifier import verify_issue

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
THRESHOLDS = SentinelThresholds()


def _outcome(**over):
    outcome = {
        "outcome_id": "evt-outcome-1",
        "task": "pay the electricity bill",
        "predicted": "bill unpaid",
        "observed": "paid",
        "user_id": "user-1",
        "task_id": "task-1",
    }
    outcome.update(over)
    return outcome


def _late_event(
    event_id, original_id, minutes_ago, *, task_id="task-1", fresh="unpaid", original="paid"
):
    return {
        "id": event_id,
        "type": "evidence_recorded",
        "task_id": task_id,
        "created_at": NOW - timedelta(minutes=minutes_ago),
        "content": {
            "kind": "outcome_record",
            "outcome_type": "late_failure",
            "user_id": "user-1",
            "summary": f"Outcome {original_id} failed after it was marked done",
            "evidence": [{"kind": "recheck", "outcome_id": original_id}],
            "details": {
                "original_outcome_id": original_id,
                "original_observed": original,
                "fresh_observed": fresh,
                "checked_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
            },
        },
    }


def _plain_event(event_id, minutes_ago, *, outcome_type="task_done"):
    return {
        "id": event_id,
        "type": "evidence_recorded",
        "task_id": "task-1",
        "created_at": NOW - timedelta(minutes=minutes_ago),
        "content": {
            "kind": "outcome_record",
            "outcome_type": outcome_type,
            "user_id": "user-1",
            "summary": "a normal outcome record",
            "evidence": [{"kind": "step", "step_id": "step-1"}],
            "details": {},
        },
    }


class _FakeWriter:
    """A ledger writer that only records what it was asked to write."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        record = {"outcome_id": f"evt-late-{len(self.calls)}"}
        record.update(kwargs)
        return record


class _FakeLoop:
    """A learning loop that only records what it was fed."""

    def __init__(self):
        self.calls: list[dict] = []

    def record_misprediction(self, record, *, condition="", evidence_event_id=None):
        self.calls.append(
            {"record": dict(record), "condition": condition, "evidence_event_id": evidence_event_id}
        )
        return {"task": record["task"], "quarantined": False}


# ---------------------------------------------------------------------------
# watch(): validation
# ---------------------------------------------------------------------------


def test_watch_requires_all_outcome_keys():
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=_FakeLoop())
    for key in ("outcome_id", "task", "predicted", "observed", "user_id", "task_id"):
        outcome = _outcome()
        del outcome[key]
        with pytest.raises(ValueError, match="missing keys"):
            watcher.watch(outcome, recheck=lambda: "paid")


def test_watch_rejects_a_non_callable_recheck():
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=_FakeLoop())
    with pytest.raises(ValueError, match="recheck must be callable"):
        watcher.watch(_outcome(), recheck="paid")


def test_watch_rejects_a_non_positive_delay():
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=_FakeLoop())
    with pytest.raises(ValueError, match="delay must be positive"):
        watcher.watch(_outcome(), recheck=lambda: "paid", delay=timedelta(0))


def test_watch_defaults_to_a_one_day_delay_and_lists_pending_in_due_order():
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=_FakeLoop())
    first = watcher.watch(_outcome(outcome_id="evt-1"), recheck=lambda: "paid", now=NOW)
    watcher.watch(
        _outcome(outcome_id="evt-2"), recheck=lambda: "paid", delay=timedelta(hours=1), now=NOW
    )
    assert first.due_at == NOW + DEFAULT_RECHECK_DELAY
    assert [item.outcome_id for item in watcher.pending()] == ["evt-2", "evt-1"]
    assert len(watcher) == 2


def test_watching_the_same_outcome_again_replaces_the_old_watch():
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=_FakeLoop())
    watcher.watch(_outcome(), recheck=lambda: "paid", delay=timedelta(days=1), now=NOW)
    watcher.watch(_outcome(), recheck=lambda: "unpaid", delay=timedelta(hours=2), now=NOW)
    assert len(watcher) == 1
    assert watcher.pending()[0].due_at == NOW + timedelta(hours=2)


# ---------------------------------------------------------------------------
# check_due(): ok / late failure / unverifiable
# ---------------------------------------------------------------------------


def test_check_due_before_the_delay_checks_nothing():
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=_FakeLoop())
    watcher.watch(_outcome(), recheck=lambda: "paid", now=NOW)
    assert watcher.check_due(NOW + timedelta(hours=1)) == []
    assert len(watcher) == 1


def test_check_due_ok_when_the_fresh_value_matches_the_record():
    writer = _FakeWriter()
    loop = _FakeLoop()
    watcher = OutcomeWatcher(record_outcome=writer, learning_loop=loop)
    watcher.watch(_outcome(), recheck=lambda: "paid", now=NOW)
    results = watcher.check_due(NOW + timedelta(days=1))
    assert len(results) == 1
    assert results[0].status == "ok"
    assert results[0].fresh_observed == "paid"
    assert writer.calls == []
    assert loop.calls == []
    assert len(watcher) == 0


def test_check_due_late_failure_writes_a_record_feeds_the_loop_and_raises_an_issue():
    writer = _FakeWriter()
    loop = _FakeLoop()
    watcher = OutcomeWatcher(record_outcome=writer, learning_loop=loop)
    watcher.watch(_outcome(), recheck=lambda: "unpaid", now=NOW)
    results = watcher.check_due(NOW + timedelta(days=1))
    assert len(results) == 1
    result = results[0]
    assert result.status == "late_failure"
    assert result.fresh_observed == "unpaid"
    assert len(writer.calls) == 1
    written = writer.calls[0]
    assert written["outcome_type"] == "late_failure"
    assert written["task_id"] == "task-1"
    assert written["user_id"] == "user-1"
    assert written["evidence"][0]["kind"] == "recheck"
    assert written["details"]["original_outcome_id"] == "evt-outcome-1"
    assert written["details"]["original_observed"] == "paid"
    assert written["details"]["fresh_observed"] == "unpaid"
    assert len(loop.calls) == 1
    fed = loop.calls[0]
    assert fed["record"]["task"] == "pay the electricity bill"
    assert fed["record"]["predicted"] == "paid"
    assert fed["record"]["observed"] == "unpaid"
    assert fed["evidence_event_id"] == "evt-outcome-1"
    assert result.outcome_record is not None
    assert result.misprediction == {"task": "pay the electricity bill", "quarantined": False}
    issue = result.issue
    assert issue is not None
    assert issue["detector"] == "outcome_failure"
    assert issue["key"] == "outcome_failure:evt-outcome-1"
    assert issue["task_id"] == "task-1"
    assert len(watcher) == 0


def test_check_due_without_a_writer_still_feeds_the_loop_but_builds_no_issue():
    loop = _FakeLoop()
    watcher = OutcomeWatcher(learning_loop=loop)
    watcher.watch(_outcome(), recheck=lambda: "unpaid", now=NOW)
    (result,) = watcher.check_due(NOW + timedelta(days=1))
    assert result.status == "late_failure"
    assert result.outcome_record is None
    assert result.issue is None
    assert len(loop.calls) == 1


def test_check_due_a_failing_recheck_is_unverifiable_and_never_kills_the_scan():
    writer = _FakeWriter()
    loop = _FakeLoop()
    watcher = OutcomeWatcher(record_outcome=writer, learning_loop=loop)

    def _boom():
        raise RuntimeError("the fresh read is down")

    watcher.watch(_outcome(outcome_id="evt-bad"), recheck=_boom, now=NOW)
    watcher.watch(_outcome(outcome_id="evt-late"), recheck=lambda: "unpaid", now=NOW)
    results = watcher.check_due(NOW + timedelta(days=1))
    assert [item.status for item in results] == ["unverifiable", "late_failure"]
    assert results[0].reason == "recheck failed: RuntimeError"
    assert results[0].fresh_observed is None
    assert len(writer.calls) == 1
    assert len(loop.calls) == 1
    assert len(watcher) == 0


def test_check_due_a_recheck_that_observes_nothing_is_unverifiable():
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=_FakeLoop())
    watcher.watch(_outcome(), recheck=lambda: None, now=NOW)
    (result,) = watcher.check_due(NOW + timedelta(days=1))
    assert result.status == "unverifiable"
    assert result.reason == "recheck observed nothing"


def test_check_due_compares_a_non_string_fresh_value_as_text():
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=_FakeLoop())
    watcher.watch(_outcome(observed="42"), recheck=lambda: 42, now=NOW)
    (result,) = watcher.check_due(NOW + timedelta(days=1))
    assert result.status == "ok"
    assert result.fresh_observed == "42"


def test_late_failure_teaches_a_real_loop(monkeypatch):
    """The watcher feeds a real loop and the loop learns.

    The loop's guardrail classification is stubbed here: on the
    scrubbed main its lazy import of the evidence renderer
    fails (the ``puspa.sandbox`` chain the split extraction
    owns), so the real classifier cannot run yet. The stub
    stands in for the guardrail, which has its own tests; the
    watcher only has to call the loop with the right record.
    """
    monkeypatch.setattr(
        "puspa.memory.learning_loop.classify_experience",
        lambda **kwargs: {
            "verification": "world_observation",
            "quarantined": False,
            "quarantine_reasons": [],
        },
    )
    loop = LearningLoop()
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=loop)
    watcher.watch(_outcome(), recheck=lambda: "unpaid", now=NOW)
    (result,) = watcher.check_due(NOW + timedelta(days=1))
    assert result.misprediction is not None
    assert result.misprediction["task"] == "pay the electricity bill"
    assert result.misprediction.get("quarantined") is not True
    assert loop.predict("pay the electricity bill") == "unpaid"


def test_late_failure_surfaces_the_loop_quarantine_verdict(monkeypatch):
    """When the loop quarantines, the result says so.

    Same stub as above, this time reporting the guardrail's
    orphan-numbers quarantine: the watcher must not hide the
    loop's verdict, and a quarantined record never teaches.
    """
    monkeypatch.setattr(
        "puspa.memory.learning_loop.classify_experience",
        lambda **kwargs: {
            "verification": "quarantined",
            "quarantined": True,
            "quarantine_reasons": ["orphan_numbers"],
        },
    )
    loop = LearningLoop()
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=loop)
    watcher.watch(
        _outcome(outcome_id="evt-price", observed="Rp100.000"),
        recheck=lambda: "Rp120.000",
        now=NOW,
    )
    (result,) = watcher.check_due(NOW + timedelta(days=1))
    assert result.status == "late_failure"
    assert result.misprediction is not None
    assert result.misprediction.get("quarantined") is True
    assert "orphan_numbers" in result.misprediction.get("quarantine_reasons", [])
    assert loop.predict("pay the electricity bill") is None


def test_a_failing_loop_or_writer_never_kills_the_check():
    class _RaisingLoop:
        def record_misprediction(self, *args, **kwargs):
            raise RuntimeError("the loop is down")

    def _raising_writer(**kwargs):
        raise RuntimeError("the ledger is down")

    watcher = OutcomeWatcher(record_outcome=_raising_writer, learning_loop=_RaisingLoop())
    watcher.watch(_outcome(), recheck=lambda: "unpaid", now=NOW)
    (result,) = watcher.check_due(NOW + timedelta(days=1))
    assert result.status == "late_failure"
    assert result.outcome_record is None
    assert result.misprediction is None
    assert result.issue is None
    assert result.fresh_observed == "unpaid"


def test_the_default_loop_feeds_without_an_injected_loop(monkeypatch):
    """The default (module-level) loop is fed when no loop is injected.

    Guardrail classification stubbed as in the teaching test
    above: the stub stands in for the classifier whose lazy
    evidence import is broken on the scrubbed main.
    """
    monkeypatch.setattr(
        "puspa.memory.learning_loop.classify_experience",
        lambda **kwargs: {
            "verification": "world_observation",
            "quarantined": False,
            "quarantine_reasons": [],
        },
    )
    watcher = OutcomeWatcher(record_outcome=_FakeWriter())
    unique_task = "k8 default loop task 9f3c21"
    watcher.watch(_outcome(task=unique_task), recheck=lambda: "unpaid", now=NOW)
    (result,) = watcher.check_due(NOW + timedelta(days=1))
    assert result.status == "late_failure"
    assert result.misprediction is not None


# ---------------------------------------------------------------------------
# Detector: detect_outcome_failures
# ---------------------------------------------------------------------------


def test_detector_finds_a_late_failure_record():
    events = [_late_event("evt-late-1", "evt-outcome-1", minutes_ago=60)]
    issues = detect_outcome_failures(events, thresholds=THRESHOLDS, now=NOW)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["detector"] == "outcome_failure"
    assert issue["key"] == "outcome_failure:evt-outcome-1"
    assert issue["task_id"] == "task-1"
    assert issue["evidence"]["outcome_id"] == "evt-outcome-1"
    assert issue["evidence"]["late_failure_ids"] == ["evt-late-1"]
    assert issue["evidence"]["count"] == 1
    assert issue["evidence"]["fresh_observed"] == "unpaid"
    assert issue["evidence"]["original_observed"] == "paid"


def test_detector_ignores_plain_outcome_records_and_other_evidence():
    events = [
        _plain_event("evt-plain-1", minutes_ago=60),
        _late_event("evt-late-1", "evt-outcome-1", minutes_ago=30),
        {
            "id": "evt-other-1",
            "type": "evidence_recorded",
            "task_id": "task-1",
            "created_at": NOW - timedelta(minutes=10),
            "content": {"kind": "evidence_row", "evidence_id": "ev-1"},
        },
    ]
    issues = detect_outcome_failures(events, thresholds=THRESHOLDS, now=NOW)
    assert [issue["key"] for issue in issues] == ["outcome_failure:evt-outcome-1"]


def test_detector_folds_several_late_failures_for_one_outcome_into_one_issue():
    events = [
        _late_event("evt-late-1", "evt-outcome-1", minutes_ago=300),
        _late_event("evt-late-2", "evt-outcome-1", minutes_ago=60, fresh="still unpaid"),
        _late_event("evt-late-3", "evt-outcome-2", minutes_ago=30),
    ]
    issues = detect_outcome_failures(events, thresholds=THRESHOLDS, now=NOW)
    assert [issue["key"] for issue in issues] == [
        "outcome_failure:evt-outcome-1",
        "outcome_failure:evt-outcome-2",
    ]
    first = issues[0]
    assert first["evidence"]["count"] == 2
    assert sorted(first["evidence"]["late_failure_ids"]) == ["evt-late-1", "evt-late-2"]
    assert first["evidence"]["fresh_observed"] == "still unpaid"
    assert first["first_seen"] < first["last_seen"]


def test_detector_ignores_late_failures_outside_the_lookback():
    events = [_late_event("evt-late-1", "evt-outcome-1", minutes_ago=60 + 8 * 24 * 60)]
    assert detect_outcome_failures(events, thresholds=THRESHOLDS, now=NOW) == []


def test_run_detectors_includes_outcome_failures():
    assert "outcome_failure" in DETECTORS
    events = [_late_event("evt-late-1", "evt-outcome-1", minutes_ago=60)]
    keys = [issue["key"] for issue in run_detectors(events, thresholds=THRESHOLDS, now=NOW)]
    assert keys == ["outcome_failure:evt-outcome-1"]


def test_outcome_failure_issue_built_by_the_watcher_matches_the_detector_shape():
    built = outcome_failure_issue(
        outcome_id="evt-outcome-1",
        task_id="task-1",
        summary="Outcome evt-outcome-1 failed after it was marked done",
        evidence={"outcome_id": "evt-outcome-1", "late_failure_ids": ["evt-late-1"]},
        first_seen=NOW - timedelta(minutes=60),
        last_seen=NOW - timedelta(minutes=60),
    )
    detected = detect_outcome_failures(
        [_late_event("evt-late-1", "evt-outcome-1", minutes_ago=60)],
        thresholds=THRESHOLDS,
        now=NOW,
    )[0]
    assert built["detector"] == detected["detector"]
    assert built["key"] == detected["key"]
    assert built["task_id"] == detected["task_id"]


# ---------------------------------------------------------------------------
# Verifier + scanner: a late failure confirms and stays open
# ---------------------------------------------------------------------------


def test_verifier_confirms_an_outcome_failure_on_fresh_events():
    events = [_late_event("evt-late-1", "evt-outcome-1", minutes_ago=60)]
    issue = detect_outcome_failures(events, thresholds=THRESHOLDS, now=NOW)[0]
    verdict = verify_issue(issue, event_source=lambda: events, now=NOW)
    assert verdict.verdict == "confirmed"
    assert verdict.issue_key == "outcome_failure:evt-outcome-1"


def test_verifier_calls_an_outcome_failure_stale_when_the_record_is_gone():
    events = [_late_event("evt-late-1", "evt-outcome-1", minutes_ago=60)]
    issue = detect_outcome_failures(events, thresholds=THRESHOLDS, now=NOW)[0]
    verdict = verify_issue(issue, event_source=list, now=NOW)
    assert verdict.verdict == "stale"


def test_scanner_confirms_a_late_failure_and_keeps_it_open(tmp_path):
    events = [_late_event("evt-late-1", "evt-outcome-1", minutes_ago=60)]
    sentinel = Sentinel(
        event_source=lambda: events,
        state_path=tmp_path / "sentinel.json",
        thresholds=THRESHOLDS,
        clock=lambda: NOW,
    )
    report = sentinel.scan(force=True)
    assert report["status"] == "scanned"
    assert "outcome_failure:evt-outcome-1" in report["confirmed"]
    again = sentinel.scan(force=True)
    assert "outcome_failure:evt-outcome-1" in again["confirmed"]


def test_check_result_carries_the_three_effects_together():
    watcher = OutcomeWatcher(record_outcome=_FakeWriter(), learning_loop=_FakeLoop())
    watched = watcher.watch(_outcome(), recheck=lambda: "unpaid", now=NOW)
    result = CheckResult(
        status="late_failure",
        watched=watched,
        fresh_observed="unpaid",
        outcome_record={"outcome_id": "evt-late-1"},
        misprediction={"task": watched.task},
        issue={"key": "outcome_failure:evt-outcome-1"},
    )
    assert result.status == "late_failure"
    assert result.fresh_observed == "unpaid"
    assert result.outcome_record == {"outcome_id": "evt-late-1"}
    assert result.misprediction == {"task": watched.task}
    assert result.issue == {"key": "outcome_failure:evt-outcome-1"}
