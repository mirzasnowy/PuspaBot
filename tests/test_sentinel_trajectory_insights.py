"""Tests for the sentinel trajectory runner, session insights,
and the scheduler wiring.

Trajectory + insights tests are in-memory (fake harness, fake
token counter, fake cost model, a throwaway state.db built from
the real G26 schema). The scheduler tests fake the trigger
table and the scan job, the same way
``tests/test_trust_root_refresh.py`` does — no test here touches
the live database or the network.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from puspa.evidence.eval_harness import EvalResult, EvalTask
from puspa.sentinel.insights import (
    format_usd,
    insights_from_events,
    insights_from_state_db,
)
from puspa.sentinel.trajectory import (
    BatchTrajectoryRunner,
    Trajectory,
    load_prompt_dataset,
    redact_trajectory,
)

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)


def _event(event_id, event_type, task_id, minutes_ago, content=None):
    return {
        "id": event_id,
        "created_at": NOW - timedelta(minutes=minutes_ago),
        "task_id": task_id,
        "agent_id": "agent-1",
        "subagent_id": None,
        "type": event_type,
        "content": content or {},
        "parent_id": None,
        "previous_hash": "GENESIS",
        "hash": "x",
    }


def _word_counter(text):
    return len(text.split()), "word-counter"


# ---------------------------------------------------------------- format_usd


def test_format_usd_keeps_four_decimals_below_one_cent():
    assert format_usd(0.0023) == "$0.0023"
    assert format_usd(0.0006) == "$0.0006"
    assert format_usd(0.0) == "$0.0000"
    assert format_usd(0.01) == "$0.01"
    assert format_usd(12.5) == "$12.50"


# ---------------------------------------------------------------- trajectory


class _FakeHarness:
    def run_task(self, task):
        return EvalResult(
            task_id=task.task_id,
            name=task.name,
            passed=True,
            score=1.0,
            final_state={
                "tools_used": ["read_price"],
                "tool_outputs": {"read_price": {"value": 100}},
            },
        )


def _task():
    return EvalTask(task_id="eval-a", name="a", prompt="Compare prices.", cassette_name="x")


def test_batch_runner_records_trajectory_and_summary(tmp_path):
    runner = BatchTrajectoryRunner(harness=_FakeHarness(), output_dir=tmp_path)
    summary = runner.run([_task()])
    assert summary["ran"] == 1
    assert summary["skipped"] == 0
    assert summary["passed"] == 1
    assert summary["failed"] == 0
    assert summary["tool_usage"] == {"read_price": 1}
    lines = (tmp_path / "trajectories.jsonl").read_text().splitlines()
    assert len(lines) == 1
    trajectory = Trajectory.from_dict(json.loads(lines[0]))
    assert trajectory.task_id == "eval-a"
    assert trajectory.steps[0].role == "human"
    assert trajectory.steps[0].content == "Compare prices."
    assert trajectory.steps[1].tool_calls == [{"tool": "read_price"}]
    assert trajectory.steps[2].role == "tool"
    assert json.loads(trajectory.steps[2].content) == {"value": 100}
    assert trajectory.passed is True
    summary_file = json.loads((tmp_path / "batch-summary.json").read_text())
    assert summary_file["ran"] == 1


def test_batch_runner_resume_skips_recorded_tasks(tmp_path):
    runner = BatchTrajectoryRunner(harness=_FakeHarness(), output_dir=tmp_path)
    runner.run([_task()])
    summary = runner.run([_task()], resume=True)
    assert summary == {
        "ran": 0,
        "skipped": 1,
        "passed": 0,
        "failed": 0,
        "tool_usage": {},
        "output_dir": str(tmp_path),
        "trajectories_file": "trajectories.jsonl",
    }
    assert len((tmp_path / "trajectories.jsonl").read_text().splitlines()) == 1


def test_batch_runner_redacts_secrets_before_saving(tmp_path):
    runner = BatchTrajectoryRunner(harness=_FakeHarness(), output_dir=tmp_path)
    runner.record_prompt_run(
        task_id="prompt-1",
        prompt="Do it.",
        agent_result={
            "messages": [
                {
                    "role": "assistant",
                    "content": "my key is sk-abcdefghij1234567890",
                    "tool_calls": [],
                }
            ],
            "final_state": {},
            "passed": True,
            "score": 1.0,
        },
    )
    raw = (tmp_path / "trajectories.jsonl").read_text()
    assert "sk-abcdefghij1234567890" not in raw
    assert "«redacted»" in raw


def test_redact_trajectory_keeps_shape():
    from puspa.sentinel.trajectory import TrajectoryStep

    trajectory = Trajectory(
        task_id="t1",
        steps=[TrajectoryStep(role="assistant", content="key sk-abcdefghij1234567890")],
        passed=True,
        score=1.0,
    )
    redacted = redact_trajectory(trajectory)
    assert redacted.task_id == "t1"
    assert redacted.passed is True
    assert "sk-abcdefghij1234567890" not in redacted.steps[0].content
    assert "«redacted»" in redacted.steps[0].content


def test_trajectory_sharegpt_export():
    from puspa.sentinel.trajectory import TrajectoryStep

    trajectory = Trajectory(
        task_id="t1",
        steps=[
            TrajectoryStep(role="human", content="Compare."),
            TrajectoryStep(role="assistant", content="", tool_calls=[{"tool": "read_price"}]),
            TrajectoryStep(role="tool", content='{"value": 100}'),
            TrajectoryStep(role="assistant", content="Done."),
        ],
        passed=True,
        score=1.0,
    )
    turns = trajectory.to_sharegpt()
    assert [turn["from"] for turn in turns] == ["human", "gpt", "function", "gpt"]
    assert turns[1]["function_call"] == [{"tool": "read_price"}]


def test_aggregate_tool_usage_counts_across_trajectories():
    from puspa.sentinel.trajectory import TrajectoryStep

    first = Trajectory(
        task_id="a",
        steps=[TrajectoryStep(role="assistant", tool_calls=[{"tool": "read_price"}])],
    )
    second = Trajectory(
        task_id="b",
        steps=[
            TrajectoryStep(role="assistant", tool_calls=[{"tool": "read_price"}]),
            TrajectoryStep(role="assistant", tool_calls=[{"tool": "read_bill"}]),
        ],
    )
    assert BatchTrajectoryRunner.aggregate_tool_usage([first, second]) == {
        "read_bill": 1,
        "read_price": 2,
    }


def test_load_prompt_dataset(tmp_path):
    path = tmp_path / "prompts.jsonl"
    path.write_text(
        '{"prompt": "one", "task_id": "p1"}\n\n{"prompt": "two", "metadata": {"k": "v"}}\n',
        encoding="utf-8",
    )
    records = load_prompt_dataset(path)
    assert [record["prompt"] for record in records] == ["one", "two"]
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"task_id": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_prompt_dataset(bad)
    broken = tmp_path / "broken.jsonl"
    broken.write_text("{nope\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_prompt_dataset(broken)


# ---------------------------------------------------------------- insights


def test_insights_from_events_counts_tokens_tools_and_recorded_cost():
    events = [
        _event("e1", "task_created", "task-1", minutes_ago=70),
        _event("e2", "user_message", "task-1", minutes_ago=60, content={"content": "hello world"}),
        _event("e3", "tool_called", "task-1", minutes_ago=55, content={"tool": "read_price"}),
        _event(
            "e4",
            "tool_completed",
            "task-1",
            minutes_ago=54,
            content={"tool": "read_price", "status": "ok", "cost_rp": 37},
        ),
        _event(
            "e5", "agent_message", "task-1", minutes_ago=50, content={"content": "hi there friend"}
        ),
    ]
    insights = insights_from_events(events, scope="task-1", count_tokens=_word_counter)
    assert insights.tokens_in == 2
    assert insights.tokens_out == 3
    assert insights.tokens_total == 5
    assert insights.counter_name == "word-counter"
    assert insights.tool_usage == {"read_price": 1}
    assert insights.message_counts == {"user_message": 1, "agent_message": 1}
    assert insights.activity_by_day == {"2026-11-18": 5}
    assert insights.cost_rp == 37
    assert insights.cost_usd == pytest.approx(0.0023)
    assert insights.cost_label == "$0.0023"
    assert insights.cost_model == "ledger cost_rp (recorded)"


def test_insights_from_events_estimates_cost_when_none_recorded():
    events = [
        _event("e1", "user_message", "task-1", minutes_ago=60, content={"content": "hello world"}),
        _event("e2", "agent_message", "task-1", minutes_ago=50, content={"content": "hi"}),
    ]
    insights = insights_from_events(
        events,
        count_tokens=_word_counter,
        cost_model=lambda i, o: (160, "fake-model"),
    )
    assert insights.cost_rp == 160
    assert insights.cost_usd == pytest.approx(0.01)
    assert insights.cost_label == "$0.01"
    assert insights.cost_model == "estimate via fake-model"


def test_insights_from_state_db_reads_sessions_and_messages(tmp_path):
    from puspa.sessions.schema import SCHEMA_STATEMENTS

    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, model) "
            "VALUES ('s1', '', '2026-11-18T10:00:00', '2026-11-18T10:02:00', 'llama-3.1-8b')"
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at, seq) "
            "VALUES ('s1', 'user', 'hello world', '2026-11-18T10:00:00', 1)"
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at, seq) "
            "VALUES ('s1', 'assistant', 'hi there friend', '2026-11-18T10:01:00', 2)"
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at, seq) "
            "VALUES ('s1', 'tool', '', '2026-11-18T10:02:00', 3)"
        )
        conn.commit()
    insights = insights_from_state_db(
        db_path, count_tokens=_word_counter, cost_model=lambda i, o: (160, "fake-model")
    )
    assert insights.sessions == 1
    assert insights.tokens_in == 2
    assert insights.tokens_out == 3
    assert insights.tool_usage == {"<tool messages>": 1}
    assert insights.message_counts == {"user": 1, "assistant": 1, "tool": 1}
    assert insights.activity_by_day == {"2026-11-18": 3}
    assert insights.model_breakdown["llama-3.1-8b"]["events"] == 1
    assert insights.cost_label == "$0.01"


# ---------------------------------------------------------------- scheduler wiring


def test_scheduler_registers_daily_sentinel_trigger(monkeypatch):
    from puspa.scheduler import scheduler

    seen = {}

    def _fake_create_trigger(
        user_id, event_type, next_trigger_at, schedule="", workflow="", dsn=None
    ):
        seen.update(
            {
                "user_id": user_id,
                "event_type": event_type,
                "next_trigger_at": next_trigger_at,
                "schedule": schedule,
            }
        )
        return scheduler.Trigger(
            id="trigger-test",
            user_id=user_id,
            event_type=event_type,
            schedule=schedule,
            next_trigger_at=next_trigger_at,
        )

    monkeypatch.setattr(scheduler, "create_trigger", _fake_create_trigger)
    trigger = scheduler.schedule_sentinel_scan("user-1")
    assert trigger.event_type == "sentinel_scan"
    assert seen["event_type"] == "sentinel_scan"
    assert seen["schedule"] == "daily 03:00"
    assert seen["next_trigger_at"] > NOW
    assert trigger.event_type in scheduler.TRIGGER_TYPES


def test_scheduler_dispatches_sentinel_scan_job(monkeypatch):
    from puspa.scheduler import scheduler

    class _FakeCursor:
        def __init__(self, row):
            self._row = row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return self._row

    class _FakeConn:
        def __init__(self, row):
            self._row = row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return _FakeCursor(self._row)

    seen = {}
    monkeypatch.setattr(
        scheduler, "_connect", lambda dsn=None: _FakeConn(("sentinel_scan", "user-1"))
    )

    def _fake_scan(*, user_id=None, dsn=None, state_dir=None, force=False):
        seen["user_id"] = user_id
        return {"status": "scanned", "added": []}

    monkeypatch.setattr("puspa.sentinel.scanner.run_sentinel_scan", _fake_scan)
    result = scheduler.run_scheduled_job({"trigger_id": "t1", "task_id": "task-1"})
    assert result == {"job": "sentinel_scan", "result": {"status": "scanned", "added": []}}
    assert seen["user_id"] == "user-1"
