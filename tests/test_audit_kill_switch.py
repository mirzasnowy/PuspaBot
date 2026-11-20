"""Tests for K12: audit log outside the agent's reach + kill switch + export.

The enforcement under test is in Postgres, not in a promise: the
application role (``puspa_app``) can INSERT/SELECT the ``events``
table and only SELECT the ``kill_switch`` flag table. Every
tamper attempt below runs as that role (``SET ROLE``), exactly how
the test suite's RLS tests run, and must fail with
``InsufficientPrivilege`` while the hash chain stays valid.

The kill switch is armed/disarmed only through the operator path
(:class:`KillSwitch` on the owner connection); the agent role
cannot flip the flag, and the harness + scheduler refuse to start
any new action while it is active.

Run solo (no ``-n``): the tests share the ``puspa_test_w2``
database and the single flag row.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import errors as pg_errors

from puspa.accounts.vault import put as vault_put
from puspa.harness import CodingHarness, CodingTask, LocalTestExecutor
from puspa.harness.executor import ExecutionResult, Executor
from puspa.harness.loop import STATUS_KILLED
from puspa.ledger import (
    EVENT_TYPES,
    AuditWriter,
    KillSwitch,
    KillSwitchActive,
    KillSwitchError,
    export_user_data,
    is_kill_switch_active,
)
from puspa.memory import episodic, profile, slices
from puspa.scheduler.scheduler import create_trigger, run_trigger, tick

TASK_ID = "task-k12"
AGENT_ID = "agent-k12"
ACTOR = "operator-nero"
REASON = "stop the agent for inspection"

TEST_CODE = '\nassert add(2, 3) == 5\nassert add(-1, 1) == 0\nprint("ALL TESTS PASSED")\n'
BUGGY_IMPL = "def add(a, b):\n    return a - b\n"
FIXED_IMPL = "def add(a, b):\n    return a + b\n"


def _program(impl: str) -> str:
    return impl + TEST_CODE


@pytest.fixture(autouse=True)
def _reset_flag(test_dsn):
    """The flag row is NOT truncated by the shared conftest fixture,
    so this module resets it around every test."""
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM kill_switch")
    yield
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM kill_switch")


@pytest.fixture
def kill_switch(test_dsn):
    return KillSwitch(test_dsn)


@pytest.fixture
def writer(test_dsn):
    return AuditWriter(test_dsn)


def _append(ledger, task_id=TASK_ID, event_type="task_created", content=None):
    return ledger.append_event(
        task_id=task_id,
        agent_id=AGENT_ID,
        event_type=event_type,
        content=content or {"step": 1},
    )


def _as_app_role(test_dsn):
    """A connection running as the agent role (SET ROLE puspa_app)."""
    conn = psycopg.connect(test_dsn, autocommit=True)
    conn.execute("SET ROLE puspa_app")
    return conn


def _harness(tmp_path, *, kill_switch=None, generator=None):
    calls = {"generator": 0, "executor": 0}

    def counting_generator(task, attempts):
        calls["generator"] += 1
        if generator is not None:
            return generator(task, attempts)
        return _program(BUGGY_IMPL)

    class CountingExecutor(Executor):
        def run(self, code: str, *, timeout_s: int = 30) -> ExecutionResult:
            calls["executor"] += 1
            return LocalTestExecutor(tmp_path / "work").run(code, timeout_s=timeout_s)

    harness = CodingHarness(
        executor=CountingExecutor(),
        generator=counting_generator,
        max_attempts=3,
        kill_switch=kill_switch,
    )
    return harness, calls


def _task():
    return CodingTask(
        task_id=TASK_ID,
        user_id="user-k12",
        agent_id=AGENT_ID,
        description="implement add(a, b) returning the sum",
        test_code=TEST_CODE,
    )


# --- audit log outside the agent's reach -----------------------------------


def test_audit_writer_appends_as_app_role_and_chain_verifies(writer):
    event = writer.append_event(
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        event_type="task_created",
        content={"step": 1},
    )
    assert event["type"] == "task_created"
    assert writer.events(TASK_ID)[0]["id"] == event["id"]
    ok, broken = writer.verify_chain(TASK_ID)
    assert ok is True and broken == []


def test_agent_role_cannot_update_or_delete_events(test_dsn, ledger, writer):
    event = _append(ledger)
    with _as_app_role(test_dsn) as conn:
        # SELECT and INSERT are allowed for the application role.
        row = conn.execute("SELECT id FROM events WHERE id = %s", (event["id"],)).fetchone()
        assert row is not None
        # UPDATE and DELETE are refused by Postgres.
        with pytest.raises(pg_errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE events SET content = '{}'::jsonb WHERE id = %s",
                (event["id"],),
            )
        with pytest.raises(pg_errors.InsufficientPrivilege):
            conn.execute("DELETE FROM events WHERE id = %s", (event["id"],))
    # The row is untouched and the chain still verifies.
    ok, _ = ledger.verify_chain(TASK_ID)
    assert ok is True
    assert writer.events(TASK_ID)[0]["content"] == {"step": 1}


def test_tamper_probe_reports_both_writes_blocked(writer):
    event = writer.append_event(
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        event_type="task_created",
        content={"step": 1},
    )
    assert writer.probe_tamper(event["id"]) == {
        "update_blocked": True,
        "delete_blocked": True,
    }
    ok, _ = writer.verify_chain(TASK_ID)
    assert ok is True


def test_agent_role_cannot_touch_kill_switch_table(test_dsn):
    with _as_app_role(test_dsn) as conn:
        # SELECT is allowed: the agent may read the flag.
        conn.execute("SELECT * FROM kill_switch")
        # INSERT, UPDATE and DELETE are all refused.
        with pytest.raises(pg_errors.InsufficientPrivilege):
            conn.execute("INSERT INTO kill_switch (id, active) VALUES (1, TRUE)")
        with pytest.raises(pg_errors.InsufficientPrivilege):
            conn.execute("UPDATE kill_switch SET active = TRUE WHERE id = 1")
        with pytest.raises(pg_errors.InsufficientPrivilege):
            conn.execute("DELETE FROM kill_switch WHERE id = 1")


def test_event_types_still_closed_at_33():
    """K12 adds no 34th event type: activation/deactivation reuse
    existing types (task_stopped / user_message)."""
    assert len(EVENT_TYPES) == 33
    assert "task_stopped" in EVENT_TYPES
    assert "user_message" in EVENT_TYPES


# --- kill switch ------------------------------------------------------------


def test_kill_switch_activate_records_activation_in_ledger(test_dsn, ledger, kill_switch):
    assert kill_switch.is_active() is False
    state = kill_switch.activate(
        actor=ACTOR, reason=REASON, ledger=ledger, task_id=TASK_ID, agent_id=AGENT_ID
    )
    assert state["active"] is True
    assert state["actor"] == ACTOR and state["reason"] == REASON
    assert kill_switch.is_active() is True
    with pytest.raises(KillSwitchActive) as excinfo:
        kill_switch.check()
    assert ACTOR in str(excinfo.value) and REASON in str(excinfo.value)

    events = ledger.events(TASK_ID, event_type="task_stopped")
    assert len(events) == 1
    assert events[0]["content"]["kill_switch"] == "activated"
    assert events[0]["content"]["actor"] == ACTOR
    ok, _ = ledger.verify_chain(TASK_ID)
    assert ok is True


def test_kill_switch_stops_harness_before_first_attempt(tmp_path, ledger, kill_switch):
    kill_switch.activate(
        actor=ACTOR, reason=REASON, ledger=ledger, task_id=TASK_ID, agent_id=AGENT_ID
    )
    harness, calls = _harness(tmp_path, kill_switch=kill_switch)
    outcome = harness.run(_task())
    assert outcome.status == STATUS_KILLED
    assert outcome.reason == "kill switch is active"
    assert calls == {"generator": 0, "executor": 0}
    assert outcome.attempts == []
    event_types = [event["type"] for event in harness.event_log()]
    assert "harness_killed" in event_types
    assert "harness_started" not in event_types


def test_kill_switch_flipped_mid_run_stops_next_attempt(tmp_path):
    """A switch flipped after the first attempt stops the run
    before the second attempt: the generator runs once, never
    twice."""
    state = {"active": False}

    class FlippingSwitch:
        def is_active(self):
            return state["active"]

    def generator(task, attempts):
        state["active"] = True  # armed mid-run, after attempt 1
        return _program(BUGGY_IMPL)

    harness, calls = _harness(tmp_path, kill_switch=FlippingSwitch(), generator=generator)
    outcome = harness.run(_task())
    assert outcome.status == STATUS_KILLED
    assert calls["generator"] == 1
    assert len(outcome.attempts) == 1


def test_harness_runs_normally_without_kill_switch(tmp_path):
    harness, calls = _harness(tmp_path, kill_switch=None)
    outcome = harness.run(_task())
    assert outcome.status != STATUS_KILLED
    assert calls["generator"] == 3  # buggy code, all attempts used


def test_kill_switch_blocks_scheduler_tick_and_trigger(test_dsn, make_user, ledger, kill_switch):
    user = make_user()
    trigger = create_trigger(
        user["user_id"],
        "schedule",
        datetime.now(UTC) - timedelta(minutes=5),
        schedule="daily 07:00",
        dsn=test_dsn,
    )
    kill_switch.activate(
        actor=ACTOR, reason=REASON, ledger=ledger, task_id=TASK_ID, agent_id=AGENT_ID
    )
    seen = []
    result = tick(sink=lambda t, c: seen.append(t), dsn=test_dsn, kill_switch=kill_switch)
    assert result == []
    assert seen == []
    assert run_trigger(trigger, dsn=test_dsn, kill_switch=kill_switch) is None
    with psycopg.connect(test_dsn) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM trigger_runs").fetchone()
    assert rows[0] == 0

    # Disarmed, the same due trigger runs exactly once.
    kill_switch.deactivate(
        actor=ACTOR, reason="inspection done", ledger=ledger, task_id=TASK_ID, agent_id=AGENT_ID
    )
    result = tick(sink=lambda t, c: seen.append(t), dsn=test_dsn, kill_switch=kill_switch)
    assert len(result) == 1 and result[0]["trigger_id"] == trigger.id
    assert seen == ["trigger_fired"]


def test_agent_cannot_deactivate_kill_switch(test_dsn, ledger, kill_switch):
    kill_switch.activate(
        actor=ACTOR, reason=REASON, ledger=ledger, task_id=TASK_ID, agent_id=AGENT_ID
    )
    with _as_app_role(test_dsn) as conn, pytest.raises(pg_errors.InsufficientPrivilege):
        conn.execute("UPDATE kill_switch SET active = FALSE WHERE id = 1")
    assert kill_switch.is_active() is True

    # The operator path disarms it, and the deactivation is recorded.
    kill_switch.deactivate(
        actor=ACTOR, reason="inspection done", ledger=ledger, task_id=TASK_ID, agent_id=AGENT_ID
    )
    assert kill_switch.is_active() is False
    events = ledger.events(TASK_ID, event_type="user_message")
    assert len(events) == 1
    assert events[0]["content"]["kill_switch"] == "deactivated"


def test_kill_switch_flag_flips_first_when_ledger_write_fails(test_dsn, kill_switch):
    """Fail-closed: even when the ledger record cannot be written,
    the agent still stops — and the operator is told the record is
    missing."""

    class BrokenLedger:
        def append_event(self, **kwargs):
            raise RuntimeError("db down")

    with pytest.raises(KillSwitchError, match="ACTIVE but the activation could not be recorded"):
        kill_switch.activate(
            actor=ACTOR,
            reason=REASON,
            ledger=BrokenLedger(),
            task_id=TASK_ID,
            agent_id=AGENT_ID,
        )
    assert kill_switch.is_active() is True


def test_is_kill_switch_active_accepts_shapes():
    assert is_kill_switch_active(None) is False
    assert is_kill_switch_active(lambda: True) is True
    assert is_kill_switch_active(lambda: False) is False

    class Switch:
        def __init__(self, active):
            self._active = active

        def is_active(self):
            return self._active

    assert is_kill_switch_active(Switch(True)) is True
    assert is_kill_switch_active(Switch(False)) is False
    with pytest.raises(TypeError):
        is_kill_switch_active(object())


# --- export ------------------------------------------------------------------


def _seed_user_data(test_dsn, user, ledger):
    """One user with data in every exported section."""
    user_id = str(user["user_id"])
    agent_id = user["agent_id"]
    profile.add_fact(user_id, "city", "Jakarta", "user_stated", dsn=test_dsn)
    profile.add_fact(
        user_id, "nickname", "my api_key=should-be-redacted", "user_stated", dsn=test_dsn
    )
    episodic.record_episode(
        user_id, "bought rice for 12000", "user_message", agent_id=agent_id, dsn=test_dsn
    )
    episodic.record_episode(
        user_id,
        "my password=hunter2-secret, do not share",
        "user_message",
        agent_id=agent_id,
        dsn=test_dsn,
    )
    slices.grant_slice(
        user_id, agent_id, "sub-1", "rice", ["profile"], "token-ref-test", dsn=test_dsn
    )
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO procedures (user_id, service) VALUES (%s, 'pay_bill')",
                (user_id,),
            )
            cur.execute(
                "INSERT INTO summaries (user_id, period_start, period_end, summary) "
                "VALUES (%s, %s, %s, 'weekly summary')",
                (user_id, datetime.now(UTC) - timedelta(days=7), datetime.now(UTC)),
            )
            cur.execute(
                "INSERT INTO style_examples (user_id, text) VALUES (%s, 'short replies')",
                (user_id,),
            )
            cur.execute(
                "INSERT INTO onboarding (user_id, current_step) VALUES (%s, 2)",
                (user_id,),
            )
            cur.execute(
                "INSERT INTO data_consents (user_id, text_version) VALUES (%s, 'v1')",
                (user_id,),
            )
        conn.commit()
    ledger.append_event(
        task_id="task-export-1",
        agent_id=agent_id,
        event_type="task_created",
        content={"goal": "pay the bill"},
    )
    ledger.append_event(
        task_id="task-export-1",
        agent_id=agent_id,
        event_type="task_completed",
        content={"goal": "pay the bill"},
    )


def test_export_contains_all_user_data_without_secrets(
    test_dsn, tmp_path, make_user, ledger, vault_master_key
):
    user = make_user()
    other = make_user()
    _seed_user_data(test_dsn, user, ledger)
    profile.add_fact(str(other["user_id"]), "city", "Bandung", "user_stated", dsn=test_dsn)
    episodic.record_episode(
        str(other["user_id"]), "other user secret line", "user_message", dsn=test_dsn
    )
    vault_put(str(user["user_id"]), "tokopedia_api", "SECRET-VALUE-123", dsn=test_dsn)

    path = tmp_path / "export.json"
    result = export_user_data(str(user["user_id"]), path, dsn=test_dsn)
    assert result.path == path
    assert result.total_rows > 0
    document = json.loads(path.read_text(encoding="utf-8"))

    counts = document["counts"]
    assert counts["profile_facts"] == 2
    assert counts["episodic"] == 2
    assert counts["procedures"] == 1
    assert counts["summaries"] == 1
    assert counts["slice_grants"] == 1
    assert counts["style_examples"] == 1
    assert counts["onboarding"] == 1
    assert counts["data_consents"] == 1
    assert counts["ledger_events"] == 2
    assert counts["vault"] == 1

    raw = path.read_text(encoding="utf-8")
    # No secret VALUES anywhere in the file. (The words
    # "password_hash" / "ciphertext" do appear in the file's own
    # `excluded` list, which names what was left out — so the
    # check below compares the actual stored values, not labels.)
    with psycopg.connect(test_dsn) as conn:
        stored_hash = conn.execute(
            "SELECT password_hash FROM users WHERE id = %s", (str(user["user_id"]),)
        ).fetchone()[0]
        stored_cipher = conn.execute(
            "SELECT ciphertext FROM vault WHERE user_id = %s", (str(user["user_id"]),)
        ).fetchone()[0]
    assert stored_hash not in raw
    assert stored_cipher not in raw
    assert "SECRET-VALUE-123" not in raw
    assert "hunter2-secret" not in raw
    assert "should-be-redacted" not in raw
    assert "token-ref-test" not in raw
    # The other user's data is absent.
    assert "Bandung" not in raw
    assert "other user secret line" not in raw
    # Vault contributes metadata only: the name, never the value.
    assert document["sections"]["vault"][0]["name"] == "tokopedia_api"
    assert all("ciphertext" not in entry for entry in document["sections"]["vault"])
    # Secrets that were typed into memory are redacted, not dropped.
    assert "«redacted»" in raw
    # The exclusion list is written into the file itself.
    assert document["excluded"]
    assert any("vault ciphertext" in line for line in document["excluded"])
    # Ledger events of this user are present, in chain order.
    types = [e["type"] for e in document["sections"]["ledger_events"]]
    assert types == ["task_created", "task_completed"]
    # Slice grants keep the record but lose the token reference.
    assert document["sections"]["slice_grants"][0]["token_ref"] == "«withheld»"


def test_export_unknown_user_raises(tmp_path, test_dsn):
    with pytest.raises(ValueError, match="unknown user"):
        export_user_data(str(uuid.uuid4()), tmp_path / "export.json", dsn=test_dsn)
    assert not (tmp_path / "export.json").exists()


def test_export_is_atomic_on_failure(tmp_path, test_dsn):
    """A failed build leaves no half-written file behind."""
    path = tmp_path / "export.json"
    with pytest.raises(ValueError, match="unknown user"):
        export_user_data(str(uuid.uuid4()), path, dsn=test_dsn)
    assert not list(tmp_path.iterdir())
