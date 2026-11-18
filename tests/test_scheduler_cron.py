"""Tests for the general cron scheduler (cron.py). In-memory,
except the job store, which uses a tmp file. No live LLM: every
execution injects a stub ``llm_complete``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.scheduler.cron import (
    CronError,
    CronJob,
    CronJobStore,
    cronjob_manage,
    execute_job,
    parse_delay,
    parse_schedule_spec,
    run_due_jobs,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _store(tmp_path):
    return CronJobStore(tmp_path / "jobs.json")


def _stub_llm(text="stub result"):
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return text

    complete.calls = calls
    return complete


# --- schedule formats ---------------------------------------------------------


def test_parse_delay_formats():
    assert parse_delay("30m") == 1800
    assert parse_delay("2h") == 7200
    assert parse_delay("1d") == 86400
    assert parse_delay("90s") == 90
    with pytest.raises(CronError):
        parse_delay("tomorrow")


def test_parse_schedule_spec_four_formats():
    delay = parse_schedule_spec("30m", now=NOW)
    assert delay["type"] == "once"
    assert delay["at"] == (NOW + timedelta(minutes=30)).isoformat()
    interval = parse_schedule_spec("every 2h")
    assert interval == {"type": "interval", "seconds": 7200, "timezone": "UTC"}
    cron = parse_schedule_spec("0 9 * * 1-5")
    assert cron == {"type": "cron", "expression": "0 9 * * 1-5", "timezone": "UTC"}
    once = parse_schedule_spec("2026-11-20T07:00:00+00:00")
    assert once["type"] == "once"
    assert once["at"] == "2026-11-20T07:00:00+00:00"


def test_parse_schedule_spec_accepts_union_dict():
    schedule = parse_schedule_spec({"type": "daily", "time": "07:00", "timezone": "Asia/Jakarta"})
    assert schedule["timezone"] == "Asia/Jakarta"


def test_parse_schedule_spec_rejects_bad_input():
    with pytest.raises(CronError):
        parse_schedule_spec("not a schedule at all !!!")
    with pytest.raises(CronError):
        parse_schedule_spec({"type": "daily", "time": "25:00"})


# --- job model ------------------------------------------------------------------


def test_job_from_record_tolerates_missing_timezone():
    job = CronJob.from_record(
        {
            "id": "cron-old",
            "prompt": "Do the thing.",
            "schedule": {"type": "interval", "seconds": 3600},
        }
    )
    assert job.timezone == "UTC"
    assert job.status == "active"
    assert job.next_run is not None


def test_execute_job_uses_fresh_prompt_without_history():
    job = CronJob(
        id="cron-1",
        prompt="Summarise the bill.",
        schedule={"type": "interval", "seconds": 3600},
        skills=["read_bill"],
        deliver_to="telegram:9",
    )
    complete = _stub_llm("bill is Rp50.000")
    assert execute_job(job, run_id="run-1", llm_complete=complete) == "bill is Rp50.000"
    prompt = complete.calls[0]
    assert "Summarise the bill." in prompt
    assert "read_bill" in prompt
    assert "telegram:9" in prompt


# --- cronjob_manage ---------------------------------------------------------------


def test_manage_create_list_pause_resume_remove(tmp_path):
    store = _store(tmp_path)
    created = cronjob_manage(
        store,
        "create",
        prompt="Check prices daily.",
        schedule="every 2h",
        deliver_to="telegram:1",
        now=NOW,
    )
    job_id = created["job"]["id"]
    assert created["job"]["status"] == "active"
    assert created["job"]["next_run"] == (NOW + timedelta(hours=2)).isoformat()
    assert len(cronjob_manage(store, "list")["jobs"]) == 1
    cronjob_manage(store, "pause", job_id=job_id)
    assert store.get(job_id).status == "paused"
    cronjob_manage(store, "resume", job_id=job_id, now=NOW)
    assert store.get(job_id).status == "active"
    removed = cronjob_manage(store, "remove", job_id=job_id)
    assert removed == {"action": "remove", "job_id": job_id}
    assert cronjob_manage(store, "list")["jobs"] == []


def test_manage_create_validates_timezone_at_the_gate(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(CronError):
        cronjob_manage(store, "create", prompt="x", schedule="every 1h", timezone="Nope/Zone")
    with pytest.raises(CronError):
        cronjob_manage(store, "create", prompt="  ", schedule="every 1h")
    # A one-shot schedule entirely in the past has no next run.
    with pytest.raises(CronError):
        cronjob_manage(store, "create", prompt="x", schedule="2026-11-01T07:00:00+00:00", now=NOW)


def test_manage_update_merges_schedule_and_fields(tmp_path):
    store = _store(tmp_path)
    created = cronjob_manage(store, "create", prompt="Old prompt.", schedule="every 2h", now=NOW)
    job_id = created["job"]["id"]
    updated = cronjob_manage(
        store, "update", job_id=job_id, prompt="New prompt.", schedule="every 30m", now=NOW
    )
    assert updated["job"]["prompt"] == "New prompt."
    assert updated["job"]["schedule"]["seconds"] == 1800
    assert updated["job"]["next_run"] == (NOW + timedelta(minutes=30)).isoformat()


def test_manage_run_executes_immediately(tmp_path):
    store = _store(tmp_path)
    created = cronjob_manage(store, "create", prompt="Say hi.", schedule="every 2h", now=NOW)
    job_id = created["job"]["id"]
    complete = _stub_llm("hi back")
    delivered = []
    result = cronjob_manage(
        store,
        "run",
        job_id=job_id,
        llm_complete=complete,
        deliver=lambda t, x: delivered.append((t, x)),
    )
    assert result["result"] == "hi back"
    assert store.get(job_id).last_result == "hi back"
    assert delivered == []


def test_manage_unknown_job_and_action(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(CronError):
        cronjob_manage(store, "pause", job_id="cron-nope")
    with pytest.raises(CronError):
        cronjob_manage(store, "explode")


# --- run_due_jobs -------------------------------------------------------------------


def test_run_due_jobs_executes_and_delivers(tmp_path):
    store = _store(tmp_path)
    created = cronjob_manage(
        store,
        "create",
        prompt="Check the bill.",
        schedule="every 1h",
        deliver_to="telegram:7",
        now=NOW - timedelta(hours=2),
    )
    job_id = created["job"]["id"]
    delivered = []
    events = []
    outcomes = run_due_jobs(
        store,
        now=NOW,
        llm_complete=_stub_llm("bill due tomorrow"),
        deliver=lambda t, x: delivered.append((t, x)),
        sink=lambda t, c: events.append(t),
    )
    assert outcomes == [
        {
            "job_id": job_id,
            "run_id": outcomes[0]["run_id"],
            "status": "completed",
            "delivery_queued": False,
        }
    ]
    assert delivered == [("telegram:7", "bill due tomorrow")]
    assert events == ["run_completed"]
    job = store.get(job_id)
    assert job.last_result == "bill due tomorrow"
    assert job.next_run == NOW + timedelta(hours=1)


def test_run_due_jobs_skips_not_yet_due_and_paused(tmp_path):
    store = _store(tmp_path)
    cronjob_manage(store, "create", prompt="Later.", schedule="every 1h", now=NOW)
    assert run_due_jobs(store, now=NOW, llm_complete=_stub_llm()) == []
    created = cronjob_manage(
        store, "create", prompt="Paused.", schedule="every 1h", now=NOW - timedelta(hours=1)
    )
    cronjob_manage(store, "pause", job_id=created["job"]["id"])
    assert run_due_jobs(store, now=NOW, llm_complete=_stub_llm()) == []


def test_missed_run_never_executes_and_is_reported(tmp_path):
    store = _store(tmp_path)
    created = cronjob_manage(
        store, "create", prompt="Old job.", schedule="every 1h", now=NOW - timedelta(hours=30)
    )
    job_id = created["job"]["id"]
    complete = _stub_llm()
    events = []
    outcomes = run_due_jobs(
        store, now=NOW, llm_complete=complete, sink=lambda t, c: events.append(t)
    )
    assert outcomes[0]["status"] == "missed"
    assert complete.calls == []  # never executed
    assert events == ["run_missed"]
    job = store.get(job_id)
    assert job.missed_runs == 1
    assert job.next_run == NOW + timedelta(hours=1)


def test_late_run_inside_grace_still_executes(tmp_path):
    store = _store(tmp_path)
    created = cronjob_manage(
        store, "create", prompt="Late job.", schedule="every 1h", now=NOW - timedelta(hours=2)
    )
    job_id = created["job"]["id"]
    # next_run is 1h after creation = 1h ago: late, inside grace.
    outcomes = run_due_jobs(store, now=NOW, llm_complete=_stub_llm("ok"))
    assert outcomes[0]["status"] == "completed"
    assert store.get(job_id).last_result == "ok"


def test_quota_hold_skips_run_and_advances(tmp_path):
    store = _store(tmp_path)
    created = cronjob_manage(
        store, "create", prompt="Held job.", schedule="every 1h", now=NOW - timedelta(hours=2)
    )
    job_id = created["job"]["id"]
    complete = _stub_llm()
    events = []
    outcomes = run_due_jobs(
        store,
        now=NOW,
        llm_complete=complete,
        quota_hold=lambda job: True,
        sink=lambda t, c: events.append(t),
    )
    assert outcomes[0]["status"] == "quota_hold"
    assert complete.calls == []
    assert events == ["quota_hold"]
    assert store.get(job_id).next_run == NOW + timedelta(hours=1)


def test_failed_run_requeues_once_then_notifies_owner(tmp_path):
    store = _store(tmp_path)
    created = cronjob_manage(
        store, "create", prompt="Flaky job.", schedule="every 1h", now=NOW - timedelta(hours=2)
    )
    job_id = created["job"]["id"]

    def boom(prompt):
        raise RuntimeError("model down")

    notified = []
    events = []
    first = run_due_jobs(
        store,
        now=NOW,
        llm_complete=boom,
        notify_owner=notified.append,
        sink=lambda t, c: events.append(t),
    )
    assert first[0]["status"] == "failed"
    assert first[0]["requeued"] is True
    assert notified == []  # first failure: requeue, no notice
    assert "run_requeued" in events
    job = store.get(job_id)
    assert job.next_run == NOW + timedelta(minutes=1)
    second = run_due_jobs(
        store,
        now=NOW + timedelta(minutes=1),
        llm_complete=boom,
        notify_owner=notified.append,
        sink=lambda t, c: events.append(t),
    )
    assert second[0]["status"] == "failed"
    assert second[0]["requeued"] is False
    assert len(notified) == 1
    assert notified[0]["job_id"] == job_id


def test_failed_delivery_is_queued_not_lost(tmp_path):
    store = _store(tmp_path)
    cronjob_manage(
        store,
        "create",
        prompt="Deliver me.",
        schedule="every 1h",
        deliver_to="telegram:7",
        now=NOW - timedelta(hours=2),
    )

    def failing_deliver(target, text):
        raise RuntimeError("channel down")

    outcomes = run_due_jobs(store, now=NOW, llm_complete=_stub_llm("hi"), deliver=failing_deliver)
    assert outcomes[0]["delivery_queued"] is True
    assert len(store.deliveries) == 1
    assert store.deliveries[0]["status"] == "queued"
    sent = []
    remaining = store.flush_deliveries(lambda t, x: sent.append((t, x)))
    assert remaining == []
    assert sent == [("telegram:7", "hi")]
    assert store.deliveries == []


def test_one_shot_job_completes_after_running(tmp_path):
    store = _store(tmp_path)
    created = cronjob_manage(
        store, "create", prompt="Once only.", schedule="30m", now=NOW - timedelta(hours=1)
    )
    job_id = created["job"]["id"]
    outcomes = run_due_jobs(store, now=NOW, llm_complete=_stub_llm("done"))
    assert outcomes[0]["status"] == "completed"
    assert store.get(job_id).status == "completed"
    assert store.get(job_id).next_run is None


def test_atomic_save_survives_reload(tmp_path):
    store = _store(tmp_path)
    cronjob_manage(store, "create", prompt="Persist me.", schedule="every 2h", now=NOW)
    reloaded = CronJobStore(tmp_path / "jobs.json")
    assert len(reloaded.jobs) == 1
    job = next(iter(reloaded.jobs.values()))
    assert job.prompt == "Persist me."
    assert job.schedule["seconds"] == 7200
    # No stray temp file left behind.
    assert not (tmp_path / "jobs.json.tmp").exists()


def test_store_rejects_corrupt_file(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CronError):
        CronJobStore(path)
