"""Idle-time self-evolution review pass (COW-02).

Layers, matching the wiring:

- Pure pattern finding (no database): three successes for one
  service are a pattern; two are not; failures never count.
- Idle detection (live Postgres test database): a session
  active ten minutes ago is not idle, one quiet for two hours
  is, and one with no activity at all counts as idle.
- The pass itself: a routine session produces zero changes; a
  repeated success becomes a trial skill (and is not
  re-promoted on a second pass); an unfinished stale task gets
  one follow-up, never a second; every applied change carries
  a ledger event id, and every such id names a real ledger
  event of the right type.
- Scheduler + CLI: the nightly trigger runs the pass through
  ``run_scheduled_job``, and ``puspa self-evolve`` runs it
  from the command line.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from puspa.db import connect as _db_connect, dict_row as _db_dict_row
import pytest

from puspa.cli import main as cli_main
from puspa.ledger import Ledger
from puspa.memory import procedural
from puspa.memory.self_evolution import (
    DEFAULT_IDLE_SECONDS,
    find_repeated_successes,
    is_idle,
    run_review_pass,
)
from puspa.scheduler.scheduler import run_scheduled_job, schedule_self_evolution

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def _episode(content, type="task_completed", minutes_ago=120, event_id=None, task_id=None, at=None):
    return {
        "type": type,
        "content": content,
        "event_id": event_id or str(uuid.uuid4()),
        "created_at": at if at is not None else NOW - timedelta(minutes=minutes_ago),
        "task_id": task_id,
    }


def _insert_episodes(user_id, episodes, test_dsn):
    with _db_connect(test_dsn) as conn, conn.cursor() as cur:
        for ep in episodes:
            cur.execute(
                "INSERT INTO episodic (user_id, task_id, type, content, event_id, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    user_id,
                    ep["task_id"],
                    ep["type"],
                    ep["content"],
                    ep["event_id"],
                    ep["created_at"],
                ),
            )
        conn.commit()


def _insert_task(user_id, agent_id, task_id, purpose, days_ago, test_dsn):
    with _db_connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tasks (id, user_id, agent_id, purpose, status, created_at) "
            "VALUES (%s, %s, %s, %s, 'accepted', %s)",
            (task_id, user_id, agent_id, purpose, NOW - timedelta(days=days_ago)),
        )
        conn.commit()


def _ledger_events(test_dsn, event_type=None):
    with _db_connect(test_dsn, row_factory=_db_dict_row) as conn, conn.cursor() as cur:
        if event_type is None:
            cur.execute("SELECT id, type, content FROM events ORDER BY created_at ASC")
        else:
            cur.execute(
                "SELECT id, type, content FROM events WHERE type = %s ORDER BY created_at ASC",
                (event_type,),
            )
        return [
            {"id": str(r["id"]), "type": r["type"], "content": r["content"]} for r in cur.fetchall()
        ]


# -- pure pattern finding -----------------------------------------------


def test_three_successes_are_a_pattern():
    episodes = [
        _episode("price-check: successful comparison for beras"),
        _episode("price-check: successful comparison for gula"),
        _episode("price-check: successful comparison for minyak"),
    ]
    patterns = find_repeated_successes(episodes)
    assert len(patterns) == 1
    assert patterns[0]["service"] == "price-check"
    assert patterns[0]["runs"] == 3
    assert len(patterns[0]["evidence_event_ids"]) == 3


def test_two_successes_are_not_a_pattern():
    episodes = [
        _episode("price-check: successful comparison for beras"),
        _episode("price-check: successful comparison for gula"),
    ]
    assert find_repeated_successes(episodes) == []


def test_failures_never_count_toward_a_pattern():
    episodes = [
        _episode("price-check: successful comparison for beras"),
        _episode("price-check: failed comparison for gula"),
        _episode("price-check: failed comparison for minyak"),
    ]
    assert find_repeated_successes(episodes) == []


def test_each_service_is_its_own_pattern():
    episodes = [
        _episode("price-check: successful comparison for beras"),
        _episode("price-check: successful comparison for gula"),
        _episode("price-check: successful comparison for minyak"),
        _episode("check-pln: successful bill check"),
        _episode("check-pln: successful bill check"),
        _episode("check-pln: successful bill check"),
    ]
    patterns = find_repeated_successes(episodes)
    assert sorted(p["service"] for p in patterns) == ["check-pln", "price-check"]


# -- idle detection -------------------------------------------------------


def test_session_active_ten_minutes_ago_is_not_idle(make_user, test_dsn):
    user = make_user()
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user["user_id"],
        [_episode("price-check: successful comparison", minutes_ago=10, task_id=session_id)],
        test_dsn,
    )
    assert not is_idle(session_id, idle_seconds=DEFAULT_IDLE_SECONDS, dsn=test_dsn, now=NOW)


def test_session_quiet_two_hours_is_idle(make_user, test_dsn):
    user = make_user()
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user["user_id"],
        [_episode("price-check: successful comparison", minutes_ago=120, task_id=session_id)],
        test_dsn,
    )
    assert is_idle(session_id, idle_seconds=DEFAULT_IDLE_SECONDS, dsn=test_dsn, now=NOW)


def test_session_with_no_activity_counts_as_idle(test_dsn):
    assert is_idle(str(uuid.uuid4()), dsn=test_dsn, now=NOW)


def test_session_id_must_be_a_task_uuid():
    with pytest.raises(ValueError, match="session id must be a task UUID"):
        is_idle("not-a-uuid", dsn="dbname=puspa_test")


# -- the pass --------------------------------------------------------------


def test_below_threshold_produces_no_review(make_user, test_dsn):
    user = make_user()
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user["user_id"],
        [
            _episode(
                "price-check: successful comparison for beras", minutes_ago=10, task_id=session_id
            ),
            _episode(
                "price-check: successful comparison for gula", minutes_ago=10, task_id=session_id
            ),
            _episode(
                "price-check: successful comparison for minyak", minutes_ago=10, task_id=session_id
            ),
        ],
        test_dsn,
    )
    result = run_review_pass(str(user["user_id"]), session_id=session_id, dsn=test_dsn, now=NOW)
    assert result["ran"] is False
    assert result["reason"] == "not_idle"
    assert result["changes"] == []
    assert result["ledger_events"] == []
    assert procedural.list_procedures(str(user["user_id"]), dsn=test_dsn) == []


def test_routine_session_produces_zero_changes(make_user, test_dsn):
    user = make_user()
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user["user_id"],
        [
            _episode("price-check: successful comparison for beras", task_id=session_id),
            _episode("check-pln: failed bill check", task_id=session_id),
        ],
        test_dsn,
    )
    result = run_review_pass(str(user["user_id"]), session_id=session_id, dsn=test_dsn, now=NOW)
    assert result["ran"] is True
    assert result["changes"] == []
    assert result["skills_proposed"] == []
    assert result["memories_proposed"] == 0
    assert result["tasks_followed_up"] == []
    assert _ledger_events(test_dsn) == []


def test_repeated_success_becomes_a_trial_skill(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user_id,
        [
            _episode("price-check: successful comparison for beras", task_id=session_id),
            _episode("price-check: successful comparison for gula", task_id=session_id),
            _episode("price-check: successful comparison for minyak", task_id=session_id),
        ],
        test_dsn,
    )
    result = run_review_pass(user_id, session_id=session_id, dsn=test_dsn, now=NOW)
    assert result["ran"] is True
    assert len(result["skills_proposed"]) == 1
    skill = result["skills_proposed"][0]
    assert skill["service"] == "price-check"
    assert skill["runs"] == 3
    procedures = procedural.list_procedures(user_id, dsn=test_dsn)
    assert len(procedures) == 1
    assert procedures[0]["service"] == "price-check"
    assert procedures[0]["status"] == "trial"
    assert str(procedures[0]["id"]) == skill["procedure_id"]
    proposed = _ledger_events(test_dsn, "module_proposed")
    assert len(proposed) == 1
    assert proposed[0]["id"] == skill["ledger_event_id"]
    assert proposed[0]["content"]["service"] == "price-check"
    assert proposed[0]["content"]["job"] == "self_evolution"


def test_second_pass_does_not_repromote_the_same_pattern(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user_id,
        [
            _episode("price-check: successful comparison for beras", task_id=session_id),
            _episode("price-check: successful comparison for gula", task_id=session_id),
            _episode("price-check: successful comparison for minyak", task_id=session_id),
        ],
        test_dsn,
    )
    first = run_review_pass(user_id, session_id=session_id, dsn=test_dsn, now=NOW)
    assert len(first["skills_proposed"]) == 1
    second = run_review_pass(user_id, session_id=session_id, dsn=test_dsn, now=NOW)
    # The first pass's own ledger bookkeeping does not count as
    # session activity, so the second pass still runs — it just
    # finds the pattern already promoted.
    assert second["ran"] is True
    assert second["skills_proposed"] == []
    assert second["skills_skipped"] == ["price-check"]
    assert len(procedural.list_procedures(user_id, dsn=test_dsn)) == 1
    assert len(_ledger_events(test_dsn, "module_proposed")) == 1


def test_repeated_preference_becomes_a_memory_proposal(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user_id,
        [
            _episode(
                "I always prefer oat milk in my coffee", type="user_message", task_id=session_id
            ),
            _episode(
                "I always prefer oat milk in my coffee", type="user_message", task_id=session_id
            ),
        ],
        test_dsn,
    )
    result = run_review_pass(user_id, session_id=session_id, dsn=test_dsn, now=NOW)
    assert result["memories_proposed"] >= 1
    with _db_connect(test_dsn, row_factory=_db_dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT key, value, status FROM profile_facts WHERE user_id = %s",
            (user_id,),
        )
        facts = cur.fetchall()
    assert len(facts) >= 1
    assert all(fact["status"] == "proposed" for fact in facts)
    assert any(fact["key"] == "user_preference" for fact in facts)


def test_unfinished_stale_task_gets_followed_up_once(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    task_id = str(uuid.uuid4())
    _insert_task(user_id, user["agent_id"], task_id, "Compare rice prices", 2, test_dsn)
    result = run_review_pass(user_id, dsn=test_dsn, now=NOW)
    assert len(result["tasks_followed_up"]) == 1
    followed = result["tasks_followed_up"][0]
    assert followed["task_id"] == task_id
    assert followed["purpose"] == "Compare rice prices"
    proposed = _ledger_events(test_dsn, "workflow_proposed")
    assert len(proposed) == 1
    assert proposed[0]["id"] == followed["ledger_event_id"]
    assert proposed[0]["content"]["task_id"] == task_id
    # A second pass never follows the same task up again.
    second = run_review_pass(user_id, dsn=test_dsn, now=NOW)
    assert second["tasks_followed_up"] == []
    assert len(_ledger_events(test_dsn, "workflow_proposed")) == 1


def test_finished_task_is_not_followed_up(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    task_id = str(uuid.uuid4())
    _insert_task(user_id, user["agent_id"], task_id, "Compare rice prices", 2, test_dsn)
    Ledger(test_dsn).append_event(
        task_id=task_id,
        agent_id="puspa",
        event_type="task_completed",
        content={"task_id": task_id, "note": "done"},
    )
    result = run_review_pass(user_id, dsn=test_dsn, now=NOW)
    assert result["tasks_followed_up"] == []
    assert _ledger_events(test_dsn, "workflow_proposed") == []


def test_fresh_unfinished_task_is_not_followed_up(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    task_id = str(uuid.uuid4())
    _insert_task(user_id, user["agent_id"], task_id, "Just started", 0, test_dsn)
    result = run_review_pass(user_id, dsn=test_dsn, now=NOW)
    assert result["tasks_followed_up"] == []


def test_every_applied_change_has_a_ledger_entry(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user_id,
        [
            _episode("price-check: successful comparison for beras", task_id=session_id),
            _episode("price-check: successful comparison for gula", task_id=session_id),
            _episode("price-check: successful comparison for minyak", task_id=session_id),
        ],
        test_dsn,
    )
    task_id = str(uuid.uuid4())
    _insert_task(user_id, user["agent_id"], task_id, "Compare rice prices", 2, test_dsn)
    result = run_review_pass(user_id, session_id=session_id, dsn=test_dsn, now=NOW)
    assert result["changes"], "expected at least one applied change"
    events_by_id = {event["id"]: event for event in _ledger_events(test_dsn)}
    for change in result["changes"]:
        event_id = change["ledger_event_id"]
        assert event_id in events_by_id, f"change {change['kind']} has no ledger event"
        assert events_by_id[event_id]["content"]["job"] == "self_evolution"
    assert sorted(result["ledger_events"]) == sorted(
        change["ledger_event_id"] for change in result["changes"]
    )


def test_follow_up_callable_is_called_with_the_task(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    task_id = str(uuid.uuid4())
    _insert_task(user_id, user["agent_id"], task_id, "Compare rice prices", 2, test_dsn)
    resumed: list[dict] = []
    result = run_review_pass(user_id, dsn=test_dsn, now=NOW, follow_up=resumed.append)
    assert result["tasks_followed_up"][0]["resumed"] is None
    assert resumed == [
        {
            "task_id": task_id,
            "purpose": "Compare rice prices",
            "status": "accepted",
            "created_at": NOW - timedelta(days=2),
        }
    ]


# -- scheduler + CLI --------------------------------------------------------


def test_scheduler_trigger_runs_the_pass(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user_id,
        [
            _episode("price-check: successful comparison for beras", task_id=session_id),
            _episode("price-check: successful comparison for gula", task_id=session_id),
            _episode("price-check: successful comparison for minyak", task_id=session_id),
        ],
        test_dsn,
    )
    trigger = schedule_self_evolution(user_id, dsn=test_dsn)
    assert trigger.event_type == "self_evolution"
    result = run_scheduled_job(
        {"trigger_id": trigger.id, "task_id": "task-self-evolve-1"}, dsn=test_dsn
    )
    assert result["job"] == "self_evolution"
    assert result["result"]["ran"] is True
    assert len(result["result"]["skills_proposed"]) == 1
    summary = _ledger_events(test_dsn, "task_completed")
    assert any(event["content"].get("job") == "self_evolution" for event in summary)


def test_cli_self_evolve_runs_the_pass(make_user, test_dsn, monkeypatch, capsys):
    user = make_user()
    user_id = str(user["user_id"])
    session_id = str(uuid.uuid4())
    _insert_episodes(
        user_id,
        [
            _episode("price-check: successful comparison for beras", task_id=session_id),
            _episode("price-check: successful comparison for gula", task_id=session_id),
            _episode("price-check: successful comparison for minyak", task_id=session_id),
        ],
        test_dsn,
    )
    monkeypatch.setenv("PUSPA_TEST_DSN", test_dsn)
    code = cli_main(["self-evolve", "--user", user_id, "--session", session_id, "--force"])
    assert code == 0
    out = capsys.readouterr().out
    report, _ = json.JSONDecoder().raw_decode(out)
    assert report["ran"] is True
    assert len(report["skills_proposed"]) == 1
    assert "1 trial skill(s)" in out


def test_cli_self_evolve_reports_not_idle(make_user, test_dsn, monkeypatch, capsys):
    user = make_user()
    user_id = str(user["user_id"])
    session_id = str(uuid.uuid4())
    # The CLI runs on the real clock, so the activity must be
    # recent in real time, not relative to the fixed test NOW.
    _insert_episodes(
        user_id,
        [
            _episode(
                "price-check: successful comparison",
                task_id=session_id,
                at=datetime.now(UTC) - timedelta(minutes=10),
            )
        ],
        test_dsn,
    )
    monkeypatch.setenv("PUSPA_TEST_DSN", test_dsn)
    code = cli_main(["self-evolve", "--user", user_id, "--session", session_id])
    assert code == 0
    out = capsys.readouterr().out
    report, _ = json.JSONDecoder().raw_decode(out)
    assert report["ran"] is False
    assert report["reason"] == "not_idle"
    assert "not idle yet" in out
