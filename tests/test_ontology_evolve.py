"""Automatic ontology repair (Kosmos K17, V2-22).

The pure planning, scoring and shadow functions need no
database. The end-to-end cycle needs the live Postgres test
database with the Puspa schema: when it is not there, the
cycle tests skip instead of failing. The cycle tests use
their own fixtures (like the K9 tests in
``test_sleep_consolidation.py``) and clean up only their own
rows, never a whole table, because other workers may run
against the same database at the same time.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from puspa.db import Error as _db_Error
from puspa.db import connect as _db_connect
from puspa.db import is_sqlite_dsn as _db_is_sqlite_dsn
from puspa.memory.ontology_evolve import (
    OP_NEEDS_EVIDENCE,
    OP_REJECT_STALE_PROPOSAL,
    OP_RETIRE_HYPOTHESIS,
    OP_SUPERSEDE_CONFLICT,
    RISK_DEFERRED,
    RISK_LOW,
    RISK_SHADOW,
    apply_repairs_to_copy,
    compile_ontology,
    list_repair_tools,
    plan_repairs,
    run_ontology_evolution,
    run_shadow,
    score_ontology,
    should_promote,
    undo_repair,
)

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)

DEFAULT_DSN = None

_DB_TABLES = (
    "profile_facts",
    "causal_hypotheses",
    "events",
    "agents",
    "users",
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
    strength=0.7,
):
    return {
        "id": hyp_id,
        "cause": cause,
        "effect": effect,
        "effect_value": None,
        "condition": condition,
        "strength": strength,
        "evidence": [],
        "created_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=30),
        "valid_from": NOW - timedelta(days=1),
        "valid_to": None,
        "status": "active",
        "supporting": supporting,
        "contradicting": contradicting,
    }


# -- pure: ontology model -------------------------------------------------


def test_compile_ontology_groups_values_relations_and_proposals():
    model = compile_ontology(
        facts=[
            _fact("f1", "city", "Jakarta", event_id="e1"),
            _fact("f2", "city", "Bandung", event_id="e2"),
            _fact("f3", "hobby", "chess", source="inferred", status="proposed", days_ago=100),
            _fact("f4", "snack", "chips", status="replaced"),
        ],
        hypotheses=[_hypothesis("h1", supporting=2)],
        now=NOW,
    )
    assert model["entities"]["city"]["values"] == ["Bandung", "Jakarta"]
    assert model["entities"]["city"]["evidence_event_ids"] == ["e1", "e2"]
    assert "snack" not in model["entities"]
    assert [r["hypothesis_id"] for r in model["relations"]] == ["h1"]
    assert [p["fact_id"] for p in model["proposals"]] == ["f3"]


def test_compile_ontology_is_deterministic():
    facts = [
        _fact("f2", "city", "Bandung"),
        _fact("f1", "city", "Jakarta"),
    ]
    first = compile_ontology(facts=facts, hypotheses=[], now=NOW)
    second = compile_ontology(facts=list(reversed(facts)), hypotheses=[], now=NOW)
    assert first == second


# -- pure: scoring --------------------------------------------------------


def test_score_of_a_clean_ontology_is_full_marks():
    result = score_ontology(
        facts=[_fact("f1", "city", "Jakarta", event_id="e1")],
        hypotheses=[_hypothesis("h1", supporting=2)],
        now=NOW,
    )
    assert result["score"] == 110.0
    assert result["entity_coverage"] == 1.0
    assert result["hypothesis_health"] == 1.0
    assert result["counts"]["total"] == 0


def test_score_penalises_each_finding_kind():
    result = score_ontology(
        facts=[
            _fact("f1", "city", "Jakarta"),
            _fact("f2", "city", "Bandung"),
            _fact("f3", "hobby", "chess", source="inferred", status="proposed", days_ago=100),
        ],
        hypotheses=[_hypothesis("h1", supporting=1, contradicting=1)],
        now=NOW,
    )
    assert result["counts"] == {
        "contradictory_fact": 1,
        "stale_fact": 1,
        "hidden_variable": 1,
        "sampling_bias": 0,
        "total": 3,
    }
    # Coverage 0/2 keys resolve (city has two values, hobby is a
    # proposal, not an entity); health 0/1 live hypotheses are
    # supported-and-uncontested.
    assert result["entity_coverage"] == 0.0
    assert result["hypothesis_health"] == 0.0
    assert result["score"] == -25.0 - 5.0 - 15.0


def test_score_rejects_a_naive_now():
    with pytest.raises(ValueError):
        score_ontology(facts=[], hypotheses=[], now=datetime(2026, 11, 22, 12, 0))


def test_should_promote_matches_or_beats_only():
    assert should_promote(10.0, 10.0) is True
    assert should_promote(10.0, 10.5) is True
    assert should_promote(10.0, 9.5) is False


# -- pure: repair planning ------------------------------------------------


def test_plan_rejects_a_stale_proposal_as_low_risk():
    repairs = plan_repairs(
        facts=[_fact("f1", "hobby", "chess", source="inferred", status="proposed", days_ago=100)],
        hypotheses=[],
        now=NOW,
    )
    assert len(repairs) == 1
    repair = repairs[0]
    assert repair["op"] == OP_REJECT_STALE_PROPOSAL
    assert repair["risk"] == RISK_LOW
    assert repair["target"]["fact_id"] == "f1"
    assert repair["undo"]["facts"] == [{"fact_id": "f1", "status": "proposed", "valid_until": None}]


def test_plan_supersedes_a_conflict_keeping_the_newest_value():
    repairs = plan_repairs(
        facts=[
            _fact("f1", "city", "Jakarta", days_ago=5),
            _fact("f2", "city", "Bandung", days_ago=1),
        ],
        hypotheses=[],
        now=NOW,
    )
    assert len(repairs) == 1
    repair = repairs[0]
    assert repair["op"] == OP_SUPERSEDE_CONFLICT
    assert repair["risk"] == RISK_SHADOW
    assert repair["target"]["keep_fact_id"] == "f2"
    assert repair["target"]["supersede_fact_ids"] == ["f1"]
    assert repair["undo"]["facts"] == [{"fact_id": "f1", "status": "active", "valid_until": None}]


def test_plan_retires_a_contested_hypothesis_and_defers_a_mostly_true_one():
    repairs = plan_repairs(
        facts=[],
        hypotheses=[
            _hypothesis("h1", cause="coffee", effect="awake", supporting=1, contradicting=1),
            _hypothesis("h2", cause="tea", effect="awake", supporting=3, contradicting=1),
        ],
        now=NOW,
    )
    by_id = {r["target"].get("hypothesis_id"): r for r in repairs}
    assert by_id["h1"]["op"] == OP_RETIRE_HYPOTHESIS
    assert by_id["h1"]["risk"] == RISK_SHADOW
    assert by_id["h1"]["undo"]["hypotheses"] == [
        {"hypothesis_id": "h1", "status": "active", "valid_to": None}
    ]
    assert by_id["h2"]["op"] == OP_NEEDS_EVIDENCE
    assert by_id["h2"]["risk"] == RISK_DEFERRED


def test_plan_retires_the_weaker_of_two_competing_claims():
    repairs = plan_repairs(
        facts=[],
        hypotheses=[
            _hypothesis("h1", cause="rain", effect="traffic", supporting=3),
            _hypothesis("h2", cause="rain", effect="flood", supporting=1),
        ],
        now=NOW,
    )
    assert len(repairs) == 1
    assert repairs[0]["op"] == OP_RETIRE_HYPOTHESIS
    assert repairs[0]["target"]["hypothesis_id"] == "h2"


def test_plan_defers_sampling_bias_because_no_repair_can_invent_evidence():
    facts = [
        _fact(f"f{i}", "snack", "chips", source="inferred", event_id="e1") for i in range(1, 6)
    ]
    repairs = plan_repairs(facts=facts, hypotheses=[], now=NOW)
    assert len(repairs) == 1
    assert repairs[0]["op"] == OP_NEEDS_EVIDENCE
    assert repairs[0]["risk"] == RISK_DEFERRED


def test_plan_of_a_clean_ontology_is_empty():
    assert (
        plan_repairs(
            facts=[_fact("f1", "city", "Jakarta")],
            hypotheses=[_hypothesis("h1", supporting=2)],
            now=NOW,
        )
        == []
    )


# -- pure: shadow run -----------------------------------------------------


def test_shadow_promotes_when_repairs_raise_the_score():
    facts = [
        _fact("f1", "city", "Jakarta", days_ago=5),
        _fact("f2", "city", "Bandung", days_ago=1),
        _fact("f3", "hobby", "chess", source="inferred", status="proposed", days_ago=100),
    ]
    hypotheses = [_hypothesis("h1", cause="coffee", effect="awake", supporting=1, contradicting=1)]
    repairs = plan_repairs(facts=facts, hypotheses=hypotheses, now=NOW)
    result = run_shadow(facts=facts, hypotheses=hypotheses, repairs=repairs, now=NOW)
    assert result["promote"] is True
    assert result["delta"] > 0
    assert result["shadow"]["counts"]["total"] == 0
    assert result["repairs_considered"] == len(repairs)


def test_shadow_with_no_repairs_matches_and_promotes():
    facts = [_fact("f1", "city", "Jakarta")]
    result = run_shadow(facts=facts, hypotheses=[], repairs=[], now=NOW)
    assert result["promote"] is True
    assert result["delta"] == 0


def test_apply_repairs_to_copy_leaves_the_inputs_untouched():
    facts = [_fact("f1", "city", "Jakarta", days_ago=5), _fact("f2", "city", "Bandung")]
    repairs = plan_repairs(facts=facts, hypotheses=[], now=NOW)
    new_facts, _ = apply_repairs_to_copy(facts, [], repairs, now=NOW)
    assert facts[0]["status"] == "active"
    assert new_facts[0]["status"] == "replaced"
    assert new_facts[1]["status"] == "active"


# -- pure: dry run + tool exposure ----------------------------------------


def test_dry_run_plans_and_shadows_but_applies_nothing():
    result = run_ontology_evolution(
        "user-1",
        facts=[
            _fact("f1", "city", "Jakarta", days_ago=5),
            _fact("f2", "city", "Bandung", days_ago=1),
        ],
        hypotheses=[],
        now=NOW,
    )
    assert result["status"] == "ok"
    assert result["applied"] == []
    assert result["deferred"] == []
    assert result["skipped"] == []
    assert result["ledger_event_ids"] == []
    assert result["shadow"]["promote"] is True


def test_undo_without_a_store_is_a_dry_run():
    result = undo_repair("user-1", {"repair_id": "repair-x", "op": "reject_stale_proposal"})
    assert result["status"] == "dry_run"
    assert result["ledger_event_id"] is None


def test_repair_tools_are_four_unique_definitions():
    tools = list_repair_tools()
    assert [t["name"] for t in tools] == [
        "ontology.reject_stale_proposal",
        "ontology.supersede_conflict",
        "ontology.retire_hypothesis",
        "ontology.undo_repair",
    ]
    for tool in tools:
        assert tool["parameters"]["required"]
        assert set(tool["parameters"]["required"]) <= set(tool["parameters"]["properties"])


# -- end to end on the real database --------------------------------------


def _dsn():
    return os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


def _db_ready(dsn):
    try:
        with _db_connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            for table in _DB_TABLES:
                if _db_is_sqlite_dsn(dsn):
                    cur.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = %s",
                        (table,),
                    )
                else:
                    cur.execute(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = %s",
                        (table,),
                    )
                if cur.fetchone() is None:
                    return False
        return True
    except _db_Error:
        return False


class TestOntologyEvolveEndToEnd:
    """One full repair cycle on the real database, then an undo."""

    @pytest.fixture(autouse=True)
    def _user(self):
        dsn = _dsn()
        if not _db_ready(dsn):
            pytest.skip("no Postgres test database with the Puspa schema")
        self.dsn = dsn
        self.user_id = str(uuid.uuid4())
        self.agent_id = f"agen-{uuid.uuid4().hex[:12]}"
        self.task_ids = []
        with _db_connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'k17-test-no-login')",
                (self.user_id, f"k17-{uuid.uuid4().hex[:8]}@contoh.id"),
            )
            cur.execute(
                "INSERT INTO agents (agent_id, user_id) VALUES (%s, %s)",
                (self.agent_id, self.user_id),
            )
            conn.commit()
        yield
        with _db_connect(dsn) as conn, conn.cursor() as cur:
            for table in ("profile_facts", "causal_hypotheses"):
                cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (self.user_id,))
            cur.execute("DELETE FROM agents WHERE user_id = %s", (self.user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (self.user_id,))
            for task_id in self.task_ids:
                cur.execute("DELETE FROM events WHERE task_id = %s", (task_id,))
            conn.commit()

    def _seed_messy_ontology(self):
        """Two active values for one key, one stale proposal, one
        contested hypothesis, one competing pair. Returns the ids
        the test needs to verify afterwards."""
        from puspa.world.causal import CausalHypothesis, CausalLedger
        from puspa.world.store import save_causal_hypotheses

        with _db_connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profile_facts (user_id, agent_id, key, value, source, status, created_at) "
                "VALUES (%s, %s, 'city', 'Jakarta', 'user_stated', 'active', now() - INTERVAL '5 days') "
                "RETURNING id",
                (self.user_id, self.agent_id),
            )
            old_city_id = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO profile_facts (user_id, agent_id, key, value, source, status) "
                "VALUES (%s, %s, 'city', 'Bandung', 'user_stated', 'active') RETURNING id",
                (self.user_id, self.agent_id),
            )
            new_city_id = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO profile_facts (user_id, agent_id, key, value, source, status, created_at) "
                "VALUES (%s, %s, 'hobby', 'chess', 'inferred', 'proposed', now() - INTERVAL '100 days') "
                "RETURNING id",
                (self.user_id, self.agent_id),
            )
            stale_id = str(cur.fetchone()[0])
            conn.commit()
        contested_id = uuid.uuid4().hex
        strong_id = uuid.uuid4().hex
        weak_id = uuid.uuid4().hex
        ledger = CausalLedger.from_dict(
            {
                "hypotheses": [
                    CausalHypothesis(
                        id=contested_id,
                        cause="coffee",
                        effect="awake",
                        condition="",
                        strength=0.7,
                        evidence=[{"kind": "observation", "id": "obs-1"}],
                        expires_at=datetime.now(UTC) + timedelta(days=30),
                        supporting=1,
                        contradicting=1,
                    ).to_dict(),
                    CausalHypothesis(
                        id=strong_id,
                        cause="rain",
                        effect="traffic",
                        condition="",
                        strength=0.8,
                        evidence=[{"kind": "observation", "id": "obs-2"}],
                        expires_at=datetime.now(UTC) + timedelta(days=30),
                        supporting=3,
                        contradicting=0,
                    ).to_dict(),
                    CausalHypothesis(
                        id=weak_id,
                        cause="rain",
                        effect="flood",
                        condition="",
                        strength=0.6,
                        evidence=[{"kind": "observation", "id": "obs-3"}],
                        expires_at=datetime.now(UTC) + timedelta(days=30),
                        supporting=1,
                        contradicting=0,
                    ).to_dict(),
                ]
            }
        )
        save_causal_hypotheses(self.user_id, self.agent_id, ledger, dsn=self.dsn)
        return {
            "old_city_id": old_city_id,
            "new_city_id": new_city_id,
            "stale_id": stale_id,
            "contested_id": contested_id,
            "strong_id": strong_id,
            "weak_id": weak_id,
        }

    def test_full_cycle_repairs_then_undo_restores(self):
        from puspa.ledger.ledger import Ledger
        from puspa.memory import profile
        from puspa.world.store import load_causal_ledger

        ids = self._seed_messy_ontology()
        task_id = f"k17-run-{uuid.uuid4().hex[:12]}"
        self.task_ids.append(task_id)
        # The run evaluates "as of" now: the seeded rows carry the
        # real current time (SQL now(), utcnow() defaults), so a
        # fixed past NOW would retire a hypothesis to a valid_to
        # before its valid_from and fail validation. Capture the
        # run time after seeding instead; the pure-function tests
        # below keep using the fixed NOW.
        run_now = datetime.now(UTC)
        result = run_ontology_evolution(
            self.user_id,
            dsn=self.dsn,
            agent_id=self.agent_id,
            task_id=task_id,
            now=run_now,
        )
        assert result["status"] == "ok"
        assert result["counts"]["total"] == 4
        assert result["shadow"]["promote"] is True
        assert result["shadow"]["delta"] > 0
        assert result["deferred"] == []
        assert result["skipped"] == []
        ops = sorted(r["op"] for r in result["applied"])
        assert ops == [
            OP_REJECT_STALE_PROPOSAL,
            OP_RETIRE_HYPOTHESIS,
            OP_RETIRE_HYPOTHESIS,
            OP_SUPERSEDE_CONFLICT,
        ]
        assert len(result["ledger_event_ids"]) == 4

        # One ledger event per applied repair, each carrying its undo.
        events = Ledger(self.dsn).events(task_id, event_type="task_completed")
        assert len(events) == 4
        assert {e["content"]["job"] for e in events} == {"ontology_repair"}
        assert all(e["content"]["repair"]["undo"] for e in events)

        # The store now holds the repaired ontology: one city value,
        # the stale proposal rejected, both contested claims retired.
        # (Replaced history is only visible with include_history.)
        facts = profile.list_facts(self.user_id, dsn=self.dsn, include_history=True)
        by_id = {f["id"]: f for f in facts}
        assert by_id[ids["new_city_id"]]["status"] == "active"
        assert by_id[ids["old_city_id"]]["status"] == "replaced"
        assert by_id[ids["stale_id"]]["status"] == "rejected"
        ledger = load_causal_ledger(self.user_id, self.agent_id, dsn=self.dsn)
        assert ledger is not None
        assert ledger.get(ids["contested_id"]).status == "contradicted"
        assert ledger.get(ids["weak_id"]).status == "contradicted"
        assert ledger.get(ids["strong_id"]).status == "active"

        # A second run finds nothing left to repair and records that.
        task_id2 = f"k17-run-{uuid.uuid4().hex[:12]}"
        self.task_ids.append(task_id2)
        again = run_ontology_evolution(
            self.user_id,
            dsn=self.dsn,
            agent_id=self.agent_id,
            task_id=task_id2,
            now=run_now,
        )
        assert again["applied"] == []
        assert again["counts"]["total"] == 0
        assert len(again["ledger_event_ids"]) == 1

        # Undo the supersede repair: the old city value is active
        # again, and the undo is itself a ledger event.
        supersede = next(r for r in result["applied"] if r["op"] == OP_SUPERSEDE_CONFLICT)
        undo_task = f"k17-undo-{uuid.uuid4().hex[:12]}"
        self.task_ids.append(undo_task)
        undone = undo_repair(
            self.user_id,
            supersede,
            dsn=self.dsn,
            agent_id=self.agent_id,
            task_id=undo_task,
            now=run_now,
        )
        assert undone["status"] == "ok"
        assert undone["restored"] == {"facts": 1, "hypotheses": 0}
        assert undone["ledger_event_id"] is not None
        facts = profile.list_facts(self.user_id, dsn=self.dsn)
        by_id = {f["id"]: f for f in facts}
        assert by_id[ids["old_city_id"]]["status"] == "active"
        undo_events = Ledger(self.dsn).events(undo_task, event_type="task_completed")
        assert len(undo_events) == 1
        assert undo_events[0]["content"]["job"] == "ontology_repair_undo"

        # Undo the contested-hypothesis retirement too.
        retire = next(
            r
            for r in result["applied"]
            if r["op"] == OP_RETIRE_HYPOTHESIS
            and r["target"]["hypothesis_id"] == ids["contested_id"]
        )
        undo_task2 = f"k17-undo-{uuid.uuid4().hex[:12]}"
        self.task_ids.append(undo_task2)
        undone2 = undo_repair(
            self.user_id,
            retire,
            dsn=self.dsn,
            agent_id=self.agent_id,
            task_id=undo_task2,
            now=NOW,
        )
        assert undone2["restored"] == {"facts": 0, "hypotheses": 1}
        ledger = load_causal_ledger(self.user_id, self.agent_id, dsn=self.dsn)
        assert ledger.get(ids["contested_id"]).status == "active"

    def test_clean_ontology_records_a_no_op_run(self):
        from puspa.ledger.ledger import Ledger

        with _db_connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profile_facts (user_id, agent_id, key, value, source, status) "
                "VALUES (%s, %s, 'city', 'Jakarta', 'user_stated', 'active')",
                (self.user_id, self.agent_id),
            )
            conn.commit()
        task_id = f"k17-run-{uuid.uuid4().hex[:12]}"
        self.task_ids.append(task_id)
        result = run_ontology_evolution(
            self.user_id,
            dsn=self.dsn,
            agent_id=self.agent_id,
            task_id=task_id,
            now=NOW,
        )
        assert result["applied"] == []
        assert result["counts"]["total"] == 0
        assert len(result["ledger_event_ids"]) == 1
        events = Ledger(self.dsn).events(task_id, event_type="task_completed")
        assert len(events) == 1
        assert events[0]["content"]["applied"] == []
