"""Daily sleep consolidation + monthly ontology review (Kosmos K9).

The pure review (``review_ontology``) needs no database. The
end-to-end cycle needs the live Postgres test database with
the Puspa schema: when it is not there, the cycle tests skip
instead of failing, so the file stays green in CI where the
schema does not exist. The cycle tests use their own
fixtures — the shared conftest is broken on the scrubbed main
(it imports a deleted private module) — and clean up only
their own rows, never a whole table, because other workers
may run against the same database at the same time.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from puspa.memory.ontology_review import (
    SAMPLING_MIN_FACTS,
    STALE_FACT_DAYS,
    review_ontology,
    run_ontology_review,
)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

_DB_TABLES = (
    "episodic",
    "summaries",
    "procedures",
    "profile_facts",
    "causal_hypotheses",
    "triggers",
    "trigger_runs",
    "events",
    "agents",
)


def _fact(fact_id, key, value, source="user_stated", status="active", days_ago=0, event_id=None):
    return {
        "id": fact_id,
        "key": key,
        "value": value,
        "source": source,
        "status": status,
        "created_at": NOW - timedelta(days=days_ago),
        "event_id": event_id,
    }


def _hypothesis(
    hyp_id,
    cause="rain",
    effect="traffic",
    condition="",
    supporting=0,
    contradicting=0,
):
    return {
        "id": hyp_id,
        "cause": cause,
        "effect": effect,
        "effect_value": None,
        "condition": condition,
        "strength": 0.7,
        "evidence": [],
        "created_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=30),
        "valid_from": NOW - timedelta(days=1),
        "valid_to": None,
        "status": "active",
        "supporting": supporting,
        "contradicting": contradicting,
    }


def test_review_finds_a_contradictory_fact():
    findings = review_ontology(
        facts=[
            _fact("f1", "city", "Jakarta", event_id="e1"),
            _fact("f2", "city", "Bandung", event_id="e2"),
        ],
        hypotheses=[],
        now=NOW,
    )
    assert len(findings) == 1
    (finding,) = findings
    assert finding["kind"] == "contradictory_fact"
    assert finding["severity"] == "warning"
    assert finding["suggestion"]
    assert {e["value"] for e in finding["evidence"]} == {"Jakarta", "Bandung"}


def test_review_ignores_a_replaced_fact():
    findings = review_ontology(
        facts=[
            _fact("f1", "city", "Jakarta"),
            _fact("f2", "city", "Bandung", status="replaced"),
        ],
        hypotheses=[],
        now=NOW,
    )
    assert findings == []


def test_review_flags_a_proposed_fact_never_confirmed():
    findings = review_ontology(
        facts=[_fact("f1", "hobby", "chess", source="inferred", status="proposed", days_ago=100)],
        hypotheses=[],
        now=NOW,
    )
    assert len(findings) == 1
    assert findings[0]["kind"] == "stale_fact"
    assert findings[0]["severity"] == "info"
    assert str(STALE_FACT_DAYS) in findings[0]["summary"]


def test_review_ignores_a_recent_proposal():
    findings = review_ontology(
        facts=[_fact("f1", "hobby", "chess", source="inferred", status="proposed", days_ago=10)],
        hypotheses=[],
        now=NOW,
    )
    assert findings == []


def test_review_flags_mixed_evidence_as_a_hidden_variable():
    findings = review_ontology(
        facts=[],
        hypotheses=[_hypothesis("h1", supporting=2, contradicting=1)],
        now=NOW,
    )
    assert len(findings) == 1
    assert findings[0]["kind"] == "hidden_variable"
    assert findings[0]["evidence"][0]["supporting"] == 2
    assert findings[0]["evidence"][0]["contradicting"] == 1


def test_review_flags_two_live_hypotheses_with_different_effects():
    findings = review_ontology(
        facts=[],
        hypotheses=[
            _hypothesis("h1", effect="traffic"),
            _hypothesis("h2", effect="no_traffic"),
        ],
        now=NOW,
    )
    assert len(findings) == 1
    assert findings[0]["kind"] == "hidden_variable"
    assert {e["effect"] for e in findings[0]["evidence"]} == {"traffic", "no_traffic"}


def test_review_ignores_an_expired_hypothesis():
    expired = _hypothesis("h1", supporting=2, contradicting=1)
    expired["expires_at"] = NOW - timedelta(days=1)
    findings = review_ontology(facts=[], hypotheses=[expired], now=NOW)
    assert findings == []


def test_review_flags_sampling_bias_on_thin_evidence():
    # All five inferred facts agree on one value, and they all
    # point at the same single evidence event: one observation
    # counted five times, not five observations.
    facts = [
        _fact(f"f{i}", "breakfast", "kopi", source="inferred", event_id="e1")
        for i in range(SAMPLING_MIN_FACTS)
    ]
    findings = review_ontology(facts=facts, hypotheses=[], now=NOW)
    assert len(findings) == 1
    assert findings[0]["kind"] == "sampling_bias"
    assert findings[0]["evidence"][0]["count"] == SAMPLING_MIN_FACTS
    assert findings[0]["evidence"][0]["total"] == SAMPLING_MIN_FACTS
    assert findings[0]["evidence"][0]["evidence_event_ids"] == ["e1"]


def test_review_does_not_flag_a_dominant_value_with_enough_evidence():
    facts = [
        _fact(f"f{i}", "breakfast", "kopi", source="inferred", event_id=f"e{i}")
        for i in range(SAMPLING_MIN_FACTS)
    ]
    findings = review_ontology(facts=facts, hypotheses=[], now=NOW)
    assert findings == []


def test_review_ignores_user_stated_facts_for_sampling_bias():
    facts = [
        _fact(f"f{i}", "breakfast", "kopi", source="user_stated", event_id="e1")
        for i in range(SAMPLING_MIN_FACTS + 1)
    ]
    findings = review_ontology(facts=facts, hypotheses=[], now=NOW)
    assert findings == []


def test_review_of_a_clean_ontology_finds_nothing():
    findings = review_ontology(
        facts=[_fact("f1", "city", "Jakarta", event_id="e1")],
        hypotheses=[_hypothesis("h1", supporting=3)],
        now=NOW,
    )
    assert findings == []


def test_review_of_empty_inputs_finds_nothing():
    assert review_ontology(facts=[], hypotheses=[], now=NOW) == []
    assert review_ontology(facts=None, hypotheses=None, now=NOW) == []


def test_review_rejects_a_naive_now():
    with pytest.raises(ValueError):
        review_ontology(facts=[], hypotheses=[], now=datetime(2026, 11, 21, 12, 0))


def test_review_is_deterministic():
    facts = [
        _fact("f1", "city", "Jakarta", event_id="e1"),
        _fact("f2", "city", "Bandung", event_id="e2"),
        _fact("f3", "hobby", "chess", source="inferred", status="proposed", days_ago=100),
    ]
    first = review_ontology(
        facts=facts, hypotheses=[_hypothesis("h1", supporting=1, contradicting=1)], now=NOW
    )
    second = review_ontology(
        facts=list(reversed(facts)),
        hypotheses=[_hypothesis("h1", supporting=1, contradicting=1)],
        now=NOW,
    )
    assert first == second
    assert [f["kind"] for f in first] == sorted(f["kind"] for f in first)


def test_run_review_with_injected_data_records_nothing_without_a_ledger():
    result = run_ontology_review(
        "user-1",
        facts=[
            _fact("f1", "city", "Jakarta", event_id="e1"),
            _fact("f2", "city", "Bandung", event_id="e2"),
        ],
        hypotheses=[],
        now=NOW,
    )
    assert result["status"] == "ok"
    assert result["counts"] == {
        "contradictory_fact": 1,
        "stale_fact": 0,
        "hidden_variable": 0,
        "sampling_bias": 0,
        "total": 1,
    }
    assert result["ledger_event_id"] is None


def _dsn():
    return os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


def _db_ready(dsn):
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            for table in _DB_TABLES:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = %s",
                    (table,),
                )
                if cur.fetchone() is None:
                    return False
        return True
    except psycopg.Error:
        # No database, or the schema is missing: the cycle tests
        # skip. Anything else (a bug in the test itself) must
        # raise, not hide behind a skip.
        return False


class TestSleepConsolidationEndToEnd:
    """One full daily cycle + one monthly review, on the real database."""

    @pytest.fixture(autouse=True)
    def _user(self):
        dsn = _dsn()
        if not _db_ready(dsn):
            pytest.skip("no Postgres test database with the Puspa schema")
        self.dsn = dsn
        self.user_id = str(uuid.uuid4())
        self.agent_id = f"agen-{uuid.uuid4().hex[:12]}"
        self.task_ids = []
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            # The agents row needs a users row first (foreign key).
            # A dummy password hash is fine: this user never logs in.
            cur.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'k9-test-no-login')",
                (self.user_id, f"k9-{uuid.uuid4().hex[:8]}@contoh.id"),
            )
            cur.execute(
                "INSERT INTO agents (agent_id, user_id) VALUES (%s, %s)",
                (self.agent_id, self.user_id),
            )
            conn.commit()
        yield
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            for table in (
                "episodic",
                "summaries",
                "procedures",
                "profile_facts",
                "causal_hypotheses",
            ):
                cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (self.user_id,))
            cur.execute(
                "DELETE FROM trigger_runs WHERE trigger_id IN "
                "(SELECT id FROM triggers WHERE user_id = %s)",
                (self.user_id,),
            )
            cur.execute("DELETE FROM triggers WHERE user_id = %s", (self.user_id,))
            cur.execute("DELETE FROM agents WHERE user_id = %s", (self.user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (self.user_id,))
            for task_id in self.task_ids:
                cur.execute("DELETE FROM events WHERE task_id = %s", (task_id,))
            conn.commit()

    def _run_due_trigger(self, trigger):
        from puspa.scheduler import scheduler

        due = scheduler.list_due_triggers(dsn=self.dsn, now=trigger.next_trigger_at)
        assert any(t.id == trigger.id for t in due)
        run = scheduler.run_trigger(trigger, dsn=self.dsn, now=trigger.next_trigger_at)
        assert run is not None
        self.task_ids.append(run["task_id"])
        return run

    def test_daily_consolidation_cycle_is_scheduled_run_and_recorded(self):
        from puspa.ledger.ledger import Ledger
        from puspa.memory import episodic
        from puspa.scheduler import scheduler

        task_id = str(uuid.uuid4())
        episodic.record_episode(
            self.user_id,
            "billing: check the electricity bill",
            "user_message",
            task_id=task_id,
            event_id=str(uuid.uuid4()),
            dsn=self.dsn,
        )
        episodic.record_episode(
            self.user_id,
            "billing: paid the electricity bill successful",
            "task_completed",
            task_id=task_id,
            event_id=str(uuid.uuid4()),
            dsn=self.dsn,
        )
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE episodic SET created_at = now() - INTERVAL '40 days' WHERE user_id = %s",
                (self.user_id,),
            )
            conn.commit()

        trigger = scheduler.schedule_consolidation(self.user_id, dsn=self.dsn)
        assert trigger.event_type == "consolidation"
        run = self._run_due_trigger(trigger)
        result = scheduler.run_scheduled_job(run, dsn=self.dsn)
        assert result["job"] == "consolidation"
        assert result["result"]["summaries_created"] == 1
        assert result["result"]["procedures_promoted"] == 1
        assert result["result"]["hypotheses_expired"] == 0

        events = Ledger(self.dsn).events(run["task_id"], event_type="task_completed")
        assert len(events) == 1
        content = events[0]["content"]
        assert content["job"] == "consolidation"
        assert content["user_id"] == self.user_id
        assert content["summaries_created"] == 1

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT next_trigger_at FROM triggers WHERE id = %s", (trigger.id,))
            next_at = cur.fetchone()[0]
        assert next_at == trigger.next_trigger_at + timedelta(days=1)

        # A second day finds nothing new to summarise, but still records the run.
        run2 = self._run_due_trigger(_reload_trigger(trigger.id, self.dsn))
        result2 = scheduler.run_scheduled_job(run2, dsn=self.dsn)
        assert result2["result"]["summaries_created"] == 0
        events2 = Ledger(self.dsn).events(run2["task_id"], event_type="task_completed")
        assert len(events2) == 1
        assert events2[0]["content"]["job"] == "consolidation"

    def test_monthly_ontology_review_runs_once_per_month(self):
        from puspa.ledger.ledger import Ledger
        from puspa.scheduler import scheduler
        from puspa.world.causal import CausalHypothesis, CausalLedger
        from puspa.world.store import save_causal_hypotheses

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profile_facts (user_id, agent_id, key, value, source, status) "
                "VALUES (%s, %s, 'city', 'Jakarta', 'user_stated', 'active')",
                (self.user_id, self.agent_id),
            )
            cur.execute(
                "INSERT INTO profile_facts (user_id, agent_id, key, value, source, status) "
                "VALUES (%s, %s, 'city', 'Bandung', 'user_stated', 'active')",
                (self.user_id, self.agent_id),
            )
            cur.execute(
                "INSERT INTO profile_facts (user_id, agent_id, key, value, source, status, created_at) "
                "VALUES (%s, %s, 'hobby', 'chess', 'inferred', 'proposed', now() - INTERVAL '100 days')",
                (self.user_id, self.agent_id),
            )
            conn.commit()
        ledger = CausalLedger.from_dict(
            {
                "hypotheses": [
                    CausalHypothesis(
                        id=str(uuid.uuid4()),
                        cause="rain",
                        effect="traffic",
                        condition="",
                        strength=0.7,
                        evidence=[{"kind": "observation", "id": "obs-1"}],
                        expires_at=datetime.now(UTC) + timedelta(days=30),
                        supporting=2,
                        contradicting=1,
                    ).to_dict()
                ]
            }
        )
        save_causal_hypotheses(self.user_id, self.agent_id, ledger, dsn=self.dsn)

        tomorrow = datetime.now(UTC) + timedelta(days=1)
        trigger = scheduler.schedule_ontology_review(self.user_id, day=tomorrow.day, dsn=self.dsn)
        assert trigger.event_type == "ontology_review"
        run = self._run_due_trigger(trigger)
        result = scheduler.run_scheduled_job(run, dsn=self.dsn)
        assert result["job"] == "ontology_review"
        assert result["result"]["status"] == "ok"
        kinds = {f["kind"] for f in result["result"]["findings"]}
        assert {"contradictory_fact", "stale_fact", "hidden_variable"} <= kinds
        assert result["result"]["ledger_event_id"] is not None

        events = Ledger(self.dsn).events(run["task_id"], event_type="task_completed")
        assert len(events) == 1
        assert events[0]["content"]["job"] == "ontology_review"

        # A second run in the same calendar month is skipped, not re-recorded.
        trigger2 = _reload_trigger(trigger.id, self.dsn)
        run2 = self._run_due_trigger(trigger2)
        result2 = scheduler.run_scheduled_job(run2, dsn=self.dsn)
        assert result2["job"] == "ontology_review"
        assert result2["result"]["status"] == "skipped"
        assert result2["result"]["reason"] == "already reviewed this month"
        assert Ledger(self.dsn).events(run2["task_id"]) == []

    def test_schedule_ontology_review_rejects_a_day_out_of_range(self):
        from puspa.scheduler import scheduler

        with pytest.raises(scheduler.SchedulerError):
            scheduler.schedule_ontology_review(self.user_id, day=31, dsn=self.dsn)


def _reload_trigger(trigger_id, dsn):
    from puspa.scheduler import scheduler

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, type, schedule, workflow, next_trigger_at, active "
            "FROM triggers WHERE id = %s",
            (trigger_id,),
        )
        row = cur.fetchone()
    return scheduler.Trigger(
        id=row[0],
        user_id=str(row[1]),
        event_type=row[2],
        schedule=row[3] or "",
        workflow=row[4] or "",
        next_trigger_at=row[5],
        active=row[6],
    )
