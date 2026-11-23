"""Tests for E-11 (c): jti + short token lifetime + durable revocation, and the stop package.

(c) Every permission token carries a ``jti`` and lives minutes, not
hours; revocations live in Postgres (migration 0020), not in one
process's memory; the Gate checks the durable list on every call.
"Stop" is one timed package: revoke-all for the agent, refresh
proved dead, all live subagents interrupted — target under 5
seconds for N subagents (gate-11 evidence).
"""

from __future__ import annotations

import time

from puspa.db import connect as _db_connect
from puspa.db import is_sqlite_dsn as _is_sqlite_dsn
import pytest
from puspa.db import errors as pg_errors

from puspa.ledger import Ledger
from puspa.policy import (
    STOP_TARGET_SECONDS,
    Gate,
    GateDenied,
    RevocationList,
    StopError,
    TokenError,
    issue_child,
    issue_short_lived_token,
    issue_token,
    jti_of,
    stop_agent,
    verify_token,
)
from puspa.policy.tokens import _issued, _revoked, decode_payload
from puspa.subagents import SubagentLifecycle, SubagentState
from puspa.subagents.host import build_child_token_payload, build_parent_token_payload
from puspa.tools.registry import ToolRegistry
from puspa.tools.toolsets import default_toolsets

AGENT_ID = "agent-e11-stop"
USER_ID = "user-1"


@pytest.fixture(autouse=True)
def _reset_revocation():
    """Isolation: in-memory revocation state is process-global."""
    _revoked.clear()
    _issued.clear()
    yield
    _revoked.clear()
    _issued.clear()


@pytest.fixture
def revocations(test_dsn):
    """Durable revocation list on the test database, emptied per test."""
    store = RevocationList(test_dsn)
    store.create_schema()
    with _db_connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE token_revocations, agent_revocations")
    yield store
    with _db_connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE token_revocations, agent_revocations")


def _payload(**overrides):
    now = int(time.time())
    payload = {
        "user_id": USER_ID,
        "agent_id": AGENT_ID,
        "subagent_id": None,
        "tools": ["gmail.kirim", "calendar.create_event"],
        "domains": ["tokopedia.com"],
        "connectors": {"gmail": ["read", "send"], "calendar": ["read"]},
        "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 1000000},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }
    payload.update(overrides)
    return payload


def _gate(ephemeral_keys, revocations=None):
    _sk, pk = ephemeral_keys
    events = []

    def log(event_type, content):
        events.append((event_type, content))

    return Gate(log=log, spending=None, public_key=pk, revocations=revocations)


def _waiting_child(ctx):
    """A child that works until a cancel boundary stops it."""
    while True:
        ctx.boundary()
        time.sleep(0.01)


def make_lifecycle(**kwargs):
    registry = ToolRegistry()
    registry.register("plugin_echo", "Echo.", {}, lambda: "echo")
    registry.register("run_code", "Run code.", {}, lambda: "ok")
    return SubagentLifecycle(registry=registry, toolsets=default_toolsets(), **kwargs)


def launch(lifecycle, child_fn, **kwargs):
    kwargs.setdefault("user_id", USER_ID)
    kwargs.setdefault("agent_id", AGENT_ID)
    kwargs.setdefault("task_id", "task-e11-stop")
    kwargs.setdefault("goal", "do the thing")
    kwargs.setdefault("child_fn", child_fn)
    return lifecycle.launch(**kwargs)


def _wait_running(lifecycle, handles, timeout_s=10.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if all(lifecycle.status(h)["state"] == SubagentState.RUNNING.value for h in handles):
            return
        time.sleep(0.02)
    raise AssertionError("children did not reach RUNNING in time")


# --- jti ----------------------------------------------------------------------


def test_issue_token_stamps_jti(ephemeral_keys):
    sk, pk = ephemeral_keys
    payload = _payload()
    token = issue_token(payload, sk)
    stamped = decode_payload(token)
    assert stamped["jti"] and len(stamped["jti"]) == 32
    assert int(stamped["jti"], 16) >= 0  # hex
    assert jti_of(stamped) == stamped["jti"]
    assert verify_token(token, pk)["jti"] == stamped["jti"]
    # The caller's payload is untouched: no jti was added to it.
    assert "jti" not in payload
    # Two issues of the same payload get different jtis.
    other = decode_payload(issue_token(_payload(), sk))
    assert other["jti"] != stamped["jti"]


def test_issue_token_keeps_explicit_jti_and_rejects_empty(ephemeral_keys):
    sk, _pk = ephemeral_keys
    token = issue_token(_payload(jti="a" * 32), sk)
    assert decode_payload(token)["jti"] == "a" * 32
    with pytest.raises(TokenError):
        issue_token(_payload(jti=""), sk)


# --- short lifetime -----------------------------------------------------------


def test_short_lived_token_lives_minutes(ephemeral_keys):
    sk, pk = ephemeral_keys
    payload = _payload()
    token = issue_short_lived_token(payload, sk)
    stamped = verify_token(token, pk)
    assert stamped["exp"] - stamped["iat"] == 300
    assert stamped["jti"]
    # Caller payload untouched (its hour-long exp is still there).
    assert payload["exp"] - payload["iat"] == 3600
    token60 = issue_short_lived_token(_payload(), sk, ttl_seconds=60)
    stamped60 = verify_token(token60, pk)
    assert stamped60["exp"] - stamped60["iat"] == 60
    assert verify_token(issue_short_lived_token(_payload(), sk, ttl_seconds=900), pk)


def test_short_lived_token_refuses_hours(ephemeral_keys):
    sk, _pk = ephemeral_keys
    with pytest.raises(TokenError):
        issue_short_lived_token(_payload(), sk, ttl_seconds=901)
    with pytest.raises(TokenError):
        issue_short_lived_token(_payload(), sk, ttl_seconds=0)


def test_child_token_payload_is_short_lived_and_has_jti():
    child = build_child_token_payload(
        parent_token=None,
        user_id=USER_ID,
        agent_id=AGENT_ID,
        subagent_id="sub-1",
        tool_names=frozenset({"plugin_echo"}),
        task_id="task-e11-stop",
    )
    now = int(time.time())
    assert 0 < child["exp"] - now <= 300
    assert child["jti"]
    assert child["iat"] <= now
    parent = build_parent_token_payload(
        user_id=USER_ID,
        agent_id=AGENT_ID,
        tool_names=frozenset({"plugin_echo"}),
        task_id="task-e11-stop",
    )
    assert 0 < parent["exp"] - now <= 300
    assert parent["jti"]


# --- durable revocation list --------------------------------------------------


def test_revoke_jti_and_agent_cutoff(revocations):
    jti = "b" * 32
    assert revocations.is_revoked_jti(jti) is False
    revocations.revoke_jti(jti, AGENT_ID, reason="test")
    assert revocations.is_revoked_jti(jti) is True
    # Idempotent: revoking twice records once.
    revocations.revoke_jti(jti, AGENT_ID, reason="test")
    assert revocations.is_revoked_jti(jti) is True
    # A payload carrying that jti is revoked; another agent's is not.
    assert revocations.is_revoked_payload({"jti": jti, "agent_id": AGENT_ID, "iat": 1})
    assert not revocations.is_revoked_payload({"jti": "c" * 32, "agent_id": AGENT_ID, "iat": 1})
    # Agent cutoff: tokens issued at or before it die, later ones live.
    cutoff = revocations.revoke_all_for_agent(AGENT_ID, actor="op", reason="stop")
    assert revocations.agent_revoked_at(AGENT_ID) == cutoff
    assert revocations.agent_revoked_at("other-agent") is None
    at = int(cutoff.timestamp())
    assert revocations.is_revoked_payload({"agent_id": AGENT_ID, "iat": at})
    assert revocations.is_revoked_payload({"agent_id": AGENT_ID, "iat": at - 100})
    assert not revocations.is_revoked_payload({"agent_id": AGENT_ID, "iat": at + 5})
    assert not revocations.is_revoked_payload({"agent_id": "other-agent", "iat": at})


def test_revocation_tables_are_read_only_for_the_agent_role(revocations, test_dsn):
    if _is_sqlite_dsn(test_dsn):
        pytest.skip("Postgres role enforcement has no SQLite equivalent (D-SQLite-5: ownership is enforced in code, not by roles)")
    revocations.revoke_jti("d" * 32, AGENT_ID, reason="test")
    # The agent role can read the lists (the Gate does, on every call).
    reader = RevocationList(test_dsn, read_role="puspa_app")
    assert reader.is_revoked_jti("d" * 32) is True
    # ... but cannot write them: a stopped agent cannot un-stop itself.
    conn = _db_connect(test_dsn, autocommit=True)
    conn.execute("SET ROLE puspa_app")
    try:
        with pytest.raises(pg_errors.InsufficientPrivilege):
            conn.execute("INSERT INTO token_revocations (jti, agent_id) VALUES ('e', 'x')")
        with pytest.raises(pg_errors.InsufficientPrivilege):
            conn.execute("DELETE FROM agent_revocations WHERE agent_id = %s", (AGENT_ID,))
    finally:
        conn.close()
    # Writes still go through the owner connection, even for a
    # RevocationList that reads as the agent role.
    reader.revoke_jti("e" * 32, AGENT_ID, reason="test")
    assert revocations.is_revoked_jti("e" * 32) is True


# --- Gate checks the durable list on every call -------------------------------


def test_gate_refuses_durably_revoked_token(ephemeral_keys, revocations):
    sk, _pk = ephemeral_keys
    gate = _gate(ephemeral_keys, revocations=revocations)
    token = issue_short_lived_token(_payload(), sk)
    req = gate.request_approval(
        user_id=USER_ID,
        agent_id=AGENT_ID,
        token=token,
        kind="send",
        content={"recipient": "a@contoh.id", "title": "Halo", "content": "tes"},
    )
    gate.approve(req.request_id, USER_ID)
    # Stop lands between approval and execution: execution must fail.
    revocations.revoke_all_for_agent(AGENT_ID, actor="op", reason="stop")
    calls = []
    with pytest.raises(GateDenied):
        gate.execute(req.request_id, executor=calls.append)
    assert calls == []
    # And a fresh approval request with the same token fails at once.
    with pytest.raises(GateDenied):
        gate.request_approval(
            user_id=USER_ID,
            agent_id=AGENT_ID,
            token=token,
            kind="send",
            content={"recipient": "a@contoh.id", "title": "Halo", "content": "tes"},
        )


def test_verify_and_refresh_fail_after_stop(ephemeral_keys, revocations):
    sk, pk = ephemeral_keys
    parent = issue_short_lived_token(_payload(), sk)
    assert verify_token(parent, pk, revocations=revocations)
    child_payload = _payload(
        subagent_id="sub-1",
        tools=["gmail.kirim"],
        exp=int(time.time()) + 120,
        parent=None,
    )
    child = issue_child(parent, child_payload, sk, pk, revocations=revocations)
    assert verify_token(child, pk, revocations=revocations)
    revocations.revoke_all_for_agent(AGENT_ID, actor="op", reason="stop")
    with pytest.raises(TokenError):
        verify_token(parent, pk, revocations=revocations)
    # Refresh is dead: a child of the revoked parent is never issued.
    with pytest.raises(TokenError):
        issue_child(parent, child_payload, sk, pk, revocations=revocations)
    # A token issued after the stop (iat past the cutoff) still verifies.
    cutoff = revocations.agent_revoked_at(AGENT_ID)
    assert cutoff is not None
    fresh = issue_token(
        _payload(iat=int(cutoff.timestamp()) + 5, exp=int(cutoff.timestamp()) + 305),
        sk,
    )
    assert verify_token(fresh, pk, revocations=revocations)


# --- lifecycle: cancel all of one agent ---------------------------------------


class CapturingRunner:
    """Runner that captures the child body instead of starting it."""

    def __init__(self):
        self.bodies = []

    def __call__(self, record, run_child):
        self.bodies.append(run_child)


def test_cancel_all_for_agent_only_touches_that_agent():
    lifecycle = make_lifecycle(runner=CapturingRunner())
    handles_a = [
        launch(lifecycle, lambda ctx: "x", agent_id="agent-a", task_id=f"task-{i}")
        for i in range(2)
    ]
    handle_b = launch(lifecycle, lambda ctx: "x", agent_id="agent-b", task_id="task-b")
    assert sorted(lifecycle.live_subagent_ids("agent-a"))
    stopped = lifecycle.cancel_all_for_agent("agent-a")
    assert len(stopped) == 2
    assert {entry["state"] for entry in stopped} == {"CANCELLED"}
    assert lifecycle.live_subagent_ids("agent-a") == []
    assert lifecycle.live_subagent_ids("agent-b") != []
    assert lifecycle.status(handle_b)["state"] == SubagentState.PENDING.value
    # Second stop finds nothing left to interrupt.
    assert lifecycle.cancel_all_for_agent("agent-a") == []
    for handle in handles_a:
        assert lifecycle.status(handle)["state"] == SubagentState.CANCELLED.value


# --- stop package, timed ------------------------------------------------------


def test_stop_package_stops_agent_within_target(ephemeral_keys, revocations, test_dsn, capsys):
    sk, pk = ephemeral_keys
    lifecycle = make_lifecycle()
    handles = [launch(lifecycle, _waiting_child) for _ in range(4)]
    _wait_running(lifecycle, handles)
    assert len(lifecycle.live_subagent_ids(AGENT_ID)) == 4
    probe = issue_short_lived_token(_payload(), sk)
    ledger = Ledger(test_dsn)
    report = stop_agent(
        agent_id=AGENT_ID,
        revocations=revocations,
        actor="operator",
        reason="gate-11 stop test",
        lifecycle=lifecycle,
        public_key=pk,
        probe_token=probe,
        ledger=ledger,
        task_id="task-e11-stop",
    )
    print(
        f"stop package: {len(report.interrupted_subagents)} subagents in "
        f"{report.elapsed_seconds:.3f}s (target under {STOP_TARGET_SECONDS}s)"
    )
    assert report.refresh_blocked is True
    assert report.refresh_proof == "probe-refused"
    assert report.lifecycle_wired is True
    assert report.ledger_recorded is True
    assert len(report.interrupted_subagents) == 4
    assert {entry["state"] for entry in report.interrupted_subagents} <= {
        "CANCEL_REQUESTED",
        "CANCELLED",
    }
    assert report.elapsed_seconds < STOP_TARGET_SECONDS
    # Every child reaches CANCELLED at its next boundary.
    for handle in handles:
        assert lifecycle.wait(handle, timeout_s=5) == SubagentState.CANCELLED
    assert lifecycle.live_subagent_ids(AGENT_ID) == []
    # The Gate refuses the pre-stop token from now on.
    gate = _gate(ephemeral_keys, revocations=revocations)
    with pytest.raises(GateDenied):
        gate.request_approval(
            user_id=USER_ID,
            agent_id=AGENT_ID,
            token=probe,
            kind="send",
            content={"recipient": "a@contoh.id", "title": "Halo", "content": "tes"},
        )
    # The stop is in the ledger as a task_stopped event.
    events = ledger.events("task-e11-stop", event_type="task_stopped")
    assert events and events[-1]["content"]["stop"] == "agent_stopped"
    assert events[-1]["content"]["interrupted_subagents"] == 4
    out = capsys.readouterr().out
    assert "stop package:" in out


def test_stop_is_scoped_to_one_agent(ephemeral_keys, revocations):
    lifecycle = make_lifecycle()
    try:
        handles_a = [
            launch(lifecycle, _waiting_child, agent_id="agent-a", task_id="task-a")
            for _ in range(3)
        ]
        handles_b = [
            launch(lifecycle, _waiting_child, agent_id="agent-b", task_id="task-b")
            for _ in range(3)
        ]
        _wait_running(lifecycle, handles_a + handles_b)
        report = stop_agent(
            agent_id="agent-a",
            revocations=revocations,
            actor="operator",
            reason="scoped stop",
            lifecycle=lifecycle,
        )
        assert report.refresh_proof == "cutoff-only"
        assert len(report.interrupted_subagents) == 3
        for handle in handles_a:
            assert lifecycle.wait(handle, timeout_s=5) == SubagentState.CANCELLED
        # Cancel is cooperative: only once the children reach their
        # next boundary are they terminal (and no longer "live").
        assert len(lifecycle.live_subagent_ids("agent-a")) == 0
        assert len(lifecycle.live_subagent_ids("agent-b")) == 3
        for handle in handles_b:
            assert lifecycle.status(handle)["state"] == SubagentState.RUNNING.value
    finally:
        lifecycle.shutdown()


def test_stop_without_lifecycle_and_bad_arguments(revocations):
    report = stop_agent(
        agent_id=AGENT_ID,
        revocations=revocations,
        actor="operator",
        reason="no children to interrupt",
    )
    assert report.lifecycle_wired is False
    assert report.interrupted_subagents == []
    assert report.refresh_proof == "cutoff-only"
    with pytest.raises(StopError):
        stop_agent(agent_id="", revocations=revocations, actor="op", reason="x")
    with pytest.raises(StopError):
        stop_agent(
            agent_id=AGENT_ID,
            revocations=revocations,
            actor="op",
            reason="x",
            probe_token="deadbeef",
        )
    with pytest.raises(StopError):
        stop_agent(
            agent_id=AGENT_ID,
            revocations=revocations,
            actor="op",
            reason="x",
            lifecycle=object(),
        )
