"""Database paths of the Brain phase that had no Postgres coverage yet.

Covers, against the real `puspa_test` database:
- `procedural.decay_stale_procedures` (verified procedure untouched
  for 90 days decays back to trial).
- `procedural.propose_conflicting_procedure` (a conflicting step is
  stored as a separate trial candidate, never an overwrite).
- `episodic.search_episodes_dual` (dual-level retrieval on Postgres
  plus pgvector).
- `graphs.identity.search_products_dual` (dual-level product search
  on Postgres, with price evidence attached).
- `consolidate.consolidate_idle` with `reflect=True` (idle
  consolidation also proposes profile inferences).
"""

import uuid

import psycopg
import pytest

from puspa.graphs.identity import (
    add_product,
    link_listing,
    record_price,
    search_products_dual,
)
from puspa.memory import consolidate, episodic, procedural, profile


def _make_verified_procedure(user_id, service, step, test_dsn):
    """Drive a procedure to verified: 3 runs by 2 different agents."""
    proc = procedural.record_successful_task(
        user_id, service, step, agent_id="agent-a", dsn=test_dsn)
    assert proc["status"] == "trial"
    proc = procedural.mark_run(
        proc["id"], user_id, True, agent_id="agent-b", dsn=test_dsn)
    proc = procedural.mark_run(
        proc["id"], user_id, True, agent_id="agent-a", dsn=test_dsn)
    assert proc["status"] == "verified"
    assert proc["successful_runs"] == 3
    return proc


def _backdate_procedure(procedure_id, days, test_dsn):
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "UPDATE procedures SET updated_at = now() - make_interval(days => %s) "
            "WHERE id = %s",
            (days, procedure_id),
        )
        conn.commit()


def test_decay_stale_procedures_decays_only_stale_verified(make_user, test_dsn):
    u = make_user()
    stale = _make_verified_procedure(
        u["user_id"], "check_pln",
        [{"task": "check bill", "method": "open provider site"}],
        test_dsn,
    )
    fresh = _make_verified_procedure(
        u["user_id"], "check_pdam",
        [{"task": "check bill", "method": "open provider site"}],
        test_dsn,
    )
    _backdate_procedure(stale["id"], 100, test_dsn)

    decayed = procedural.decay_stale_procedures(u["user_id"], dsn=test_dsn)

    assert [d["id"] for d in decayed] == [stale["id"]]
    assert decayed[0]["status"] == "trial"
    assert decayed[0]["successful_runs"] == 0
    assert procedural.get_procedure(fresh["id"], u["user_id"], dsn=test_dsn)["status"] == "verified"


def test_decay_stale_procedures_ignores_trial(make_user, test_dsn):
    u = make_user()
    proc = procedural.record_successful_task(
        u["user_id"], "check_pln",
        [{"task": "check bill", "method": "open provider site"}],
        dsn=test_dsn,
    )
    _backdate_procedure(proc["id"], 100, test_dsn)
    assert procedural.decay_stale_procedures(u["user_id"], dsn=test_dsn) == []
    assert procedural.get_procedure(proc["id"], u["user_id"], dsn=test_dsn)["status"] == "trial"


def test_propose_conflicting_procedure_keeps_old_and_reports_conflict(make_user, test_dsn):
    u = make_user()
    old_step = [
        {"task": "check price", "method": "open tokopedia"},
        {"task": "compare", "method": "list three stores"},
    ]
    old = procedural.record_successful_task(
        u["user_id"], "compare_price", old_step, dsn=test_dsn)
    new_step = [
        {"task": "check price", "method": "open tokopedia"},
        {"task": "compare", "method": "ask the user"},
    ]

    result = procedural.propose_conflicting_procedure(
        u["user_id"], "compare_price", new_step, dsn=test_dsn)

    assert result["status"] == "proposed"
    assert result["conflicts_with"] == old["id"]
    assert result["procedure"]["id"] != old["id"]
    assert result["procedure"]["status"] == "trial"
    untouched = procedural.get_procedure(old["id"], u["user_id"], dsn=test_dsn)
    assert untouched["step"] == old_step


def test_propose_conflicting_procedure_without_conflict_is_recorded(make_user, test_dsn):
    u = make_user()
    procedural.record_successful_task(
        u["user_id"], "compare_price",
        [{"task": "check price", "method": "open tokopedia"}],
        dsn=test_dsn,
    )
    result = procedural.propose_conflicting_procedure(
        u["user_id"], "compare_price",
        [{"task": "pay bill", "method": "open provider site"}],
        dsn=test_dsn,
    )
    assert result["status"] == "recorded"
    assert result["conflicts_with"] is None


def test_search_episodes_dual_ranks_entity_match_first(make_user, test_dsn):
    u = make_user()
    target = episodic.record_episode(
        u["user_id"], "SM-A546E Samsung phone price check, cheapest store found",
        "task_completed", dsn=test_dsn,
    )
    episodic.record_episode(
        u["user_id"], "bought coffee beans at the market this morning",
        "user_message", dsn=test_dsn,
    )

    results = episodic.search_episodes_dual(
        u["user_id"], "SM-A546E price", dsn=test_dsn)

    assert results
    assert results[0]["id"] == target["id"]
    assert results[0]["level"] == "entity"
    assert results[0]["evidence_id"] == target["id"]
    assert results[0]["score"] > 0


def test_search_episodes_dual_rejects_empty_query(make_user, test_dsn):
    u = make_user()
    with pytest.raises(ValueError):
        episodic.search_episodes_dual(u["user_id"], "", dsn=test_dsn)


def test_search_products_dual_entity_match_carries_price_evidence(make_user, test_dsn):
    u = make_user()
    product = add_product(
        brand="Samsung", series="Galaxy S24", model_number="SM-S921B",
        category="phone", dsn=test_dsn,
    )
    link = link_listing(
        product_id=product["id"], user_id=u["user_id"],
        marketplace="Tokopedia",
        listing_url="https://www.tokopedia.com/toko/galaxy-s24",
        dsn=test_dsn,
    )
    evidence_id = str(uuid.uuid4())
    record_price(
        link_id=link["id"], user_id=u["user_id"], price=12999000,
        evidence_id=evidence_id, dsn=test_dsn,
    )

    result = search_products_dual("SM-S921B", dsn=test_dsn)

    assert len(result["entity_matches"]) == 1
    match = result["entity_matches"][0]
    assert match["product"]["id"] == product["id"]
    assert match["level"] == "entity"
    assert len(match["prices"]) == 1
    assert match["prices"][0]["price"] == 12999000.0
    assert match["prices"][0]["evidence_id"] == evidence_id
    assert evidence_id in match["evidence_ids"]


def test_search_products_dual_concept_match_by_series(make_user, test_dsn):
    u = make_user()
    product = add_product(
        brand="Samsung", series="Galaxy S24", model_number="SM-S921B",
        category="phone", dsn=test_dsn,
    )

    result = search_products_dual("Galaxy S24", dsn=test_dsn)

    assert result["entity_matches"] == []
    assert len(result["concept_matches"]) == 1
    assert result["concept_matches"][0]["product"]["id"] == product["id"]
    assert result["concept_matches"][0]["prices"] == []


def test_search_products_dual_rejects_empty_query(test_dsn):
    with pytest.raises(ValueError):
        search_products_dual("", dsn=test_dsn)


def test_consolidate_idle_with_reflect_proposes_reliable_service(make_user, test_dsn):
    u = make_user()
    task_id = str(uuid.uuid4())
    for _ in range(2):
        episodic.record_episode(
            u["user_id"], "check_pln: successful, bill read",
            "task_completed", task_id=task_id,
            event_id=str(uuid.uuid4()), dsn=test_dsn,
        )
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "UPDATE episodic SET created_at = now() - interval '45 days' "
            "WHERE user_id = %s",
            (u["user_id"],),
        )
        conn.commit()

    result = consolidate.consolidate_idle(
        u["user_id"], older_than_days=30, reflect=True, dsn=test_dsn)

    assert result["summaries_created"] == 1
    assert result["proposals_created"] == 1
    proposals = profile.list_proposals(u["user_id"], dsn=test_dsn)
    assert len(proposals) == 1
    assert proposals[0]["key"] == "reliable_service"
    assert proposals[0]["value"] == "check_pln"
    assert proposals[0]["source"] == "inferred"
    assert proposals[0]["status"] == "proposed"


def test_consolidate_idle_without_reflect_creates_no_proposals(make_user, test_dsn):
    u = make_user()
    task_id = str(uuid.uuid4())
    for _ in range(2):
        episodic.record_episode(
            u["user_id"], "check_pln: successful, bill read",
            "task_completed", task_id=task_id,
            event_id=str(uuid.uuid4()), dsn=test_dsn,
        )
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "UPDATE episodic SET created_at = now() - interval '45 days' "
            "WHERE user_id = %s",
            (u["user_id"],),
        )
        conn.commit()

    result = consolidate.consolidate_idle(u["user_id"], older_than_days=30, dsn=test_dsn)

    assert result["summaries_created"] == 1
    assert result["proposals_created"] == 0
    assert profile.list_proposals(u["user_id"], dsn=test_dsn) == []
