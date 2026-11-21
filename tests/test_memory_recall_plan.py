"""Tests for recall-first recall_plan().

Unit tests inject every store (facts list, fake ledger, in-memory
agent models), so they run without Postgres. Database tests seed
Postgres only, then build a fresh plan with no in-memory state:
that is the cold-session proof.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from puspa.intent.inference import DeterministicInferrer
from puspa.intent.turn_wiring import ActionSpec, IntentTurnAdvisor
from puspa.memory.recall_plan import (
    LAYER_ORDER,
    recall_first_infer_intent,
    recall_plan,
)
from puspa.world.agents import AgentGoalStore

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
USER = "user-recall-plan"


def _fact(fid, key, content, source="user_stated"):
    return {
        "id": fid,
        "user_id": USER,
        "key": key,
        "content": content,
        "status": "active",
        "valid_from": NOW,
        "valid_until": None,
        "superseded_by": None,
        "source": source,
        "confidence": None,
        "source_detail": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _pinned_facts():
    return [
        _fact("c1", "city", "Lives in Karawang, not Tangerang", "user_confirmed"),
        _fact("c2", "timezone", "Uses WIB, not WITA", "user_confirmed"),
        _fact("m1", "commitment:beta", "Ship the Puspa beta to 10 users"),
        _fact("g1", "goal:remote-work", "Land remote work paying USD 3000 a month"),
        _fact("g2", "goal:health", "Sleep well every night"),
    ]


class StubEmbedder:
    """Embedder returning caller-chosen vectors per exact text."""

    dim = 4

    def __init__(self, vectors):
        self._vectors = vectors

    def embed(self, text):
        return list(self._vectors[text])


class FakeLedger:
    """Ledger stub: events per task id."""

    def __init__(self, by_task):
        self._by_task = by_task

    def events(self, task_id):
        return [{"id": event_id} for event_id in self._by_task.get(task_id, [])]


class FailingLedger:
    """Ledger stub whose read always fails."""

    def events(self, task_id):
        raise RuntimeError("ledger is down")


def _agent_store():
    store = AgentGoalStore()
    store.observe(
        {
            "contact_id": "budi",
            "user_involved": True,
            "asked_for": ["review work"],
            "day": 1,
            "channel": "chat",
        }
    )
    store.observe(
        {
            "contact_id": "sari",
            "user_involved": True,
            "asked_for": ["send report"],
            "day": 1,
            "channel": "chat",
        }
    )
    return store


@pytest.mark.unit
def test_cold_session_recalls_every_pinned_item():
    plan = recall_plan(
        USER, "hello", facts=_pinned_facts(), ledger=FakeLedger({}), agent_models=_agent_store()
    )
    assert plan.counts["pinned"] == 5
    assert [item["key"] for item in plan.pinned] == [
        "city",
        "timezone",
        "commitment:beta",
        "goal:remote-work",
        "goal:health",
    ]
    assert [item["section"] for item in plan.pinned] == [
        "Corrections",
        "Corrections",
        "Commitments",
        "Standing Goals",
        "Standing Goals",
    ]


@pytest.mark.unit
def test_recall_runs_before_intent_inference():
    trace: list[str] = []
    distribution, plan = recall_first_infer_intent(
        "I am tired and need rest",
        USER,
        facts=_pinned_facts(),
        ledger=FakeLedger({}),
        agent_models=_agent_store(),
        trace=trace,
    )
    assert trace == ["pinned", "semantic", "episodes", "persons", "intent"]
    assert plan.load_order == LAYER_ORDER
    assert distribution.desire.top is not None
    assert distribution.desire.top.label == "rest"


@pytest.mark.unit
def test_semantic_top_k_included_when_present():
    embedder = StubEmbedder(
        {
            "remote work": [1.0, 0.0, 0.0, 0.0],
            "Land remote work paying USD 3000 a month": [1.0, 0.0, 0.0, 0.0],
            "Sleep well every night": [0.0, 1.0, 0.0, 0.0],
        }
    )
    facts = [_pinned_facts()[3], _pinned_facts()[4]]
    plan = recall_plan(USER, "remote work", facts=facts, embedder=embedder, ledger=FakeLedger({}))
    assert [hit["fact"]["key"] for hit in plan.semantic] == ["goal:remote-work"]
    assert plan.intent_context().profile["goal:remote-work"] == (
        "Land remote work paying USD 3000 a month"
    )


@pytest.mark.unit
def test_plan_works_when_every_layer_is_empty():
    plan = recall_plan(
        USER, "hello", facts=[], ledger=FakeLedger({}), episodes=[], agent_models=AgentGoalStore()
    )
    assert plan.counts == {"pinned": 0, "semantic": 0, "episodes": 0, "persons": 0}
    assert plan.load_order == LAYER_ORDER
    assert plan.layer_errors == {}
    distribution, _plan = recall_first_infer_intent(
        "I am tired",
        USER,
        facts=[],
        ledger=FakeLedger({}),
        episodes=[],
        agent_models=AgentGoalStore(),
    )
    assert distribution.desire.top is not None
    assert distribution.desire.top.label == "rest"


@pytest.mark.unit
def test_only_ledger_linked_episodes_are_included():
    episodes = [
        {
            "id": "ep1",
            "task_id": "t1",
            "event_id": "e1",
            "content": "Remote work contract signed",
            "created_at": NOW,
        },
        {
            "id": "ep2",
            "task_id": "t1",
            "event_id": "e2",
            "content": "Remote work contract draft",
            "created_at": NOW,
        },
        {
            "id": "ep3",
            "task_id": "t2",
            "event_id": None,
            "content": "Budi asked for a review",
            "created_at": NOW,
        },
    ]
    plan = recall_plan(
        USER,
        "remote work contract",
        facts=[],
        ledger=FakeLedger({"t1": ["e1"], "t2": ["e9"]}),
        episodes=episodes,
        agent_models=AgentGoalStore(),
    )
    assert [ep["id"] for ep in plan.episodes] == ["ep1", "ep3"]
    assert all(ep["evidence_id"] in ("ep1", "ep3") for ep in plan.episodes)


@pytest.mark.unit
def test_person_models_only_for_named_contacts():
    facts = [*_pinned_facts(), _fact("p1", "person:budi", "Budi is a friend")]
    plan = recall_plan(
        USER,
        "What did Budi ask for?",
        facts=facts,
        ledger=FakeLedger({}),
        agent_models=_agent_store(),
    )
    assert [person["contact_id"] for person in plan.persons] == ["budi"]
    assert plan.persons[0]["active_goals"] == ["review work"]


@pytest.mark.unit
def test_advisor_runs_recall_before_intent():
    order: list[str] = []

    class RecordingInferrer:
        def infer(self, message, context):
            order.append("intent")
            return DeterministicInferrer().infer(message, context)

    def plan_provider(message):
        order.append("recall")
        return recall_plan(
            USER, message, facts=_pinned_facts(), ledger=FakeLedger({}), agent_models=_agent_store()
        )

    advisor = IntentTurnAdvisor(
        inferrer=RecordingInferrer(),
        action_specs={"pay_bill": ActionSpec(reversible=True, stakes="low")},
        plan_provider=plan_provider,
    )
    decision = advisor.advise("pay my pln bill", "pay_bill", {"amount": 100000})
    assert order == ["recall", "intent"]
    assert decision is not None
    assert advisor.last_plan is not None
    assert advisor.last_plan.counts["pinned"] == 5


@pytest.mark.unit
def test_failing_layer_is_recorded_not_fatal():
    plan = recall_plan(
        USER,
        "hello",
        facts=_pinned_facts(),
        ledger=FailingLedger(),
        agent_models=_agent_store(),
        episodes=[{"id": "ep1", "task_id": "t1", "event_id": "e1", "content": "hi"}],
    )
    assert plan.counts["pinned"] == 5
    assert plan.episodes == []
    assert plan.layer_errors["episodes"] == "ledger is down"


def test_db_cold_session_recalls_all_pinned_items(test_dsn, make_user):
    from puspa.memory.facts import PostgresFactStore, record_fact

    user = make_user()
    store = PostgresFactStore(dsn=test_dsn)
    for key, content, source in [
        ("city", "Lives in Karawang, not Tangerang", "user_confirmed"),
        ("commitment:beta", "Ship the Puspa beta to 10 users", "user_stated"),
        ("goal:remote-work", "Remote work pays USD 3000", "user_stated"),
    ]:
        assert record_fact(user["user_id"], key, content, source=source, store=store)["stored"]
    plan = recall_plan(user["user_id"], "remote work", dsn=test_dsn)
    assert [item["key"] for item in plan.pinned] == [
        "city",
        "commitment:beta",
        "goal:remote-work",
    ]
    assert plan.layer_errors == {}


def test_db_full_plan_cold_session(test_dsn, make_user, ledger):
    from puspa.memory.episodic import record_episode
    from puspa.memory.facts import PostgresFactStore, record_fact
    from puspa.world import store as world_store

    user = make_user()
    user_id, agent_id = user["user_id"], user["agent_id"]
    fact_store = PostgresFactStore(dsn=test_dsn)
    for key, content, source in [
        ("goal:remote-work", "Remote work pays USD 3000", "user_stated"),
        ("person:budi", "Budi is a friend", "user_stated"),
    ]:
        assert record_fact(user_id, key, content, source=source, store=fact_store)["stored"]
    event = ledger.append_event(
        task_id=str(uuid.uuid4()),
        agent_id=agent_id,
        event_type="task_completed",
        content={"summary": "review done"},
    )
    record_episode(
        user_id,
        "Budi asked for a review of the remote work contract",
        "task_completed",
        task_id=event["task_id"],
        event_id=event["id"],
        dsn=test_dsn,
    )
    models = _agent_store()
    assert world_store.save_agent_models(user_id, agent_id, models, dsn=test_dsn) == 2
    trace: list[str] = []
    distribution, plan = recall_first_infer_intent(
        "Remote work pays USD 3000",
        user_id,
        agent_id=agent_id,
        dsn=test_dsn,
        ledger=ledger,
        trace=trace,
    )
    assert trace == ["pinned", "semantic", "episodes", "persons", "intent"]
    assert [item["key"] for item in plan.pinned] == ["goal:remote-work"]
    assert [hit["fact"]["key"] for hit in plan.semantic] == ["goal:remote-work"]
    assert [ep["content"] for ep in plan.episodes] == [
        "Budi asked for a review of the remote work contract"
    ]
    assert [person["contact_id"] for person in plan.persons] == ["budi"]
    assert distribution.goal.top is not None
    assert distribution.goal.top.label == "Remote work pays USD 3000"
