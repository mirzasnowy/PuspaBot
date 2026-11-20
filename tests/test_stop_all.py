"""Tests for the P-02 stop-all service (``puspa.pwa.stop_all``).

Runs against the real Postgres test database: the durable
revocation list, the ledger, and the pool. Covers stopping
every agent of one user (revoke-all + interrupted children +
one ledger event per agent under a shared stop-all task id),
stopping pool subagents, a user with no agents, and the
5-second target.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from puspa.policy import RevocationList, StopError
from puspa.pwa.stop_all import STOP_ALL_TARGET_SECONDS, stop_all_for_user
from puspa.runtime.subagents import SubagentPool


@pytest.fixture
def revocations(test_dsn):
    """Durable revocation list on the test database, emptied per test."""
    store = RevocationList(test_dsn)
    store.create_schema()
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE token_revocations, agent_revocations")
    yield store
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE token_revocations, agent_revocations")


class _FakeLifecycle:
    """A lifecycle that cancels every child it is asked about."""

    def __init__(self):
        self.cancelled: list[str] = []

    def cancel_all_for_agent(self, agent_id):
        self.cancelled.append(agent_id)
        return [{"subagent_id": "sub-1", "state": "cancelled"}]


def _spawn(pool, agent_id, task_id, token_payloads):
    return pool.spawn(
        agent_id=agent_id,
        task_id=task_id,
        permission_class="Pembaca",
        agent_token=token_payloads["agents"],
        subagent_token=token_payloads["sub"],
    )


# ---------------------------------------------------------------------------
# Pool: stop every subagent of one agent
# ---------------------------------------------------------------------------


def test_pool_stop_all_for_agent_stops_active_and_queued(token_payloads):
    pool = SubagentPool()
    _spawn(pool, "agent-1", "task-1", token_payloads)
    _spawn(pool, "agent-1", "task-2", token_payloads)
    _spawn(pool, "agent-2", "task-1", token_payloads)
    result = pool.stop_all_for_agent("agent-1")
    assert result["stopped"] == 2
    assert result["tasks"] == ["task-1", "task-2"]
    assert pool.count_active() == 1
    assert pool.active_per_agent("agent-1") == []


def test_pool_stop_all_for_agent_with_nothing_running_is_a_quiet_zero():
    pool = SubagentPool()
    result = pool.stop_all_for_agent("agent-1")
    assert result["stopped"] == 0
    assert result["tasks"] == []


# ---------------------------------------------------------------------------
# Service: stop everything for one user
# ---------------------------------------------------------------------------


def test_stop_all_stops_the_users_agents_and_records_each_one(
    make_user, test_dsn, ledger, revocations
):
    user = make_user()
    lifecycle = _FakeLifecycle()
    report = stop_all_for_user(
        user["user_id"],
        dsn=test_dsn,
        revocations=revocations,
        lifecycle=lifecycle,
        ledger=ledger,
        actor=user["user_id"],
    )
    assert report.elapsed_seconds < STOP_ALL_TARGET_SECONDS
    assert len(report.agents_stopped) == 1
    entry = report.agents_stopped[0]
    assert entry["agent_id"] == user["agent_id"]
    assert entry["interrupted_subagents"] == 1
    assert entry["refresh_blocked"] is True
    assert report.lifecycle_wired is True
    assert report.ledger_recorded is True
    assert report.task_id.startswith("stop-all-")
    assert lifecycle.cancelled == [user["agent_id"]]
    # One task_stopped event per agent, under the shared task id.
    events = ledger.events(report.task_id, event_type="task_stopped")
    assert len(events) == 1
    assert events[0]["agent_id"] == user["agent_id"]
    assert events[0]["content"]["stop"] == "agent_stopped"
    # The revoke-all is durable: a pre-stop token of the agent is dead.
    assert revocations.is_revoked_payload(
        {"agent_id": user["agent_id"], "iat": 0, "exp": 9999999999}
    )
    assert report.to_dict()["audit_trail_url"] == "/api/proof/audit-trail"


def test_stop_all_also_stops_pool_subagents(
    make_user, test_dsn, ledger, revocations, token_payloads
):
    user = make_user()
    pool = SubagentPool()
    _spawn(pool, user["agent_id"], "task-1", token_payloads)
    _spawn(pool, user["agent_id"], "task-2", token_payloads)
    report = stop_all_for_user(
        user["user_id"],
        dsn=test_dsn,
        revocations=revocations,
        pool=pool,
        ledger=ledger,
        actor=user["user_id"],
    )
    assert report.pool_wired is True
    assert report.subagents_stopped == 2
    assert pool.count_active() == 0


def test_stop_all_leaves_other_users_agents_alone(make_user, test_dsn, ledger, revocations):
    user = make_user()
    other = make_user()
    report = stop_all_for_user(
        user["user_id"],
        dsn=test_dsn,
        revocations=revocations,
        ledger=ledger,
        actor=user["user_id"],
    )
    assert [entry["agent_id"] for entry in report.agents_stopped] == [user["agent_id"]]
    # The other user's agent has no revocation row.
    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agent_revocations WHERE agent_id = %s",
            (other["agent_id"],),
        )
        assert cur.fetchone()[0] == 0


def test_stop_all_for_a_user_with_no_agents_stops_nothing(test_dsn, revocations):
    user_id = str(uuid.uuid4())
    report = stop_all_for_user(
        user_id,
        dsn=test_dsn,
        revocations=revocations,
        actor=user_id,
    )
    assert report.agents_stopped == []
    assert report.subagents_stopped == 0
    assert report.ledger_recorded is False
    assert report.elapsed_seconds < STOP_ALL_TARGET_SECONDS


def test_stop_all_rejects_bad_arguments(test_dsn, revocations):
    with pytest.raises(StopError, match="user_id must be"):
        stop_all_for_user("", dsn=test_dsn, revocations=revocations)
    with pytest.raises(StopError, match="actor must be"):
        stop_all_for_user("user-1", dsn=test_dsn, revocations=revocations, actor="")
    with pytest.raises(StopError, match="no cancel_all_for_agent"):
        stop_all_for_user("user-1", dsn=test_dsn, revocations=revocations, lifecycle=object())


def test_stop_all_without_a_ledger_still_stops_but_records_nothing(
    make_user, test_dsn, ledger, revocations
):
    user = make_user()
    report = stop_all_for_user(
        user["user_id"], dsn=test_dsn, revocations=revocations, actor=user["user_id"]
    )
    assert len(report.agents_stopped) == 1
    assert report.ledger_recorded is False
    assert ledger.events(report.task_id, event_type="task_stopped") == []
