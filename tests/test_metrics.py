"""Tes metrik moat gerbang 8 (§3.6) + pustaka alur (§3.8 revisi 7)."""

import psycopg
import pytest

from puspa.graphs import service as graf
from puspa.graphs.identity import add_product, link_listing
from puspa.moat import library, metrics


@pytest.fixture
def agent_kedua(test_dsn):
    """Local override of the shared fixture: a factory that creates a
    second agent for a given user. (The shared conftest fixture calls
    create_agent("agen-kedua") with no user_id/name, but create_agent's
    signature is (user_id, name, ...), so it errors at setup. Tests
    here call agent_kedua(user_id) and use the returned agent_id.)"""
    from puspa.runtime.agents import create_agent

    return lambda user_id: create_agent(user_id, "agen-kedua", dsn=test_dsn)


def test_coverage_matching_automatic(make_user, test_dsn):
    user = make_user()
    product = add_product(brand="Samsung", model_number="SM-S921B", category="phone", dsn=test_dsn)
    for i, marketplace in enumerate(("Tokopedia", "Shopee", "Blibli")):
        tautan = link_listing(product_id=product["id"], user_id=user["user_id"],
                                 marketplace=marketplace,
                                 listing_url=f"https://www.{marketplace.lower()}.com/x{i}",
                                 dsn=test_dsn)
        if marketplace == "Blibli":
            from puspa.graphs.identity import confirm_link

            confirm_link(tautan["id"], user_id=user["user_id"], accepted=True, dsn=test_dsn)
    result = metrics.automatic_match_coverage(dsn=test_dsn)
    assert result["percent"] == 66.7
    assert result["status"] == "passed"


def test_coverage_without_data_pending(test_dsn):
    result = metrics.automatic_match_coverage(dsn=test_dsn)
    assert result["status"] == "pending" and result["percent"] is None


def test_procedure_verified_and_reused(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    for provider in metrics.PROVIDER_MVP:
        procedure = graf.write_procedure(user_id=user["user_id"], service=provider,
                                       step=[{"step": "read"}],
                                       agent_id=user["agent_id"], dsn=test_dsn)
        for agent in (user["agent_id"], agen2["agent_id"], user["agent_id"]):
            graf.record_run(procedure["id"], user["user_id"], True, agent_id=agent, dsn=test_dsn)
        graf.record_reuse(procedure_id=procedure["id"], user_id=user["user_id"],
                               agent_id=agen2["agent_id"], task_id="tugas-reuse",
                               dsn=test_dsn)
    result = metrics.verified_procedures_reused(user["user_id"], dsn=test_dsn)
    assert result["status"] == "passed"
    assert all(v["verified"] and v["reused"]
               for v in result["per_provider"].values())


def test_pending_token_ratio_without_test_period(test_dsn):
    result = metrics.comparison_token_ratio(dsn=test_dsn)
    assert result["status"] == "pending" and result["rasio"] is None


def test_token_ratio_counts_weekly(make_user, test_dsn):
    from datetime import datetime, timedelta, timezone

    user = make_user()
    now = datetime.now(timezone.utc)
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            for i in range(4):
                cur.execute(
                    "INSERT INTO token_metrics (task_id, user_id, type, token, dicatat)"
                    " VALUES (%s, %s, 'compare_prices', 1000, %s)",
                    (f"t-awal-{i}", user["user_id"], now - timedelta(days=10)))
            for i in range(4):
                cur.execute(
                    "INSERT INTO token_metrics (task_id, user_id, type, token, dicatat)"
                    " VALUES (%s, %s, 'compare_prices', 700, %s)",
                    (f"t-akhir-{i}", user["user_id"], now - timedelta(days=1)))
        conn.commit()
    result = metrics.comparison_token_ratio(dsn=test_dsn)
    assert result["rasio"] == 0.7
    assert result["status"] == "passed"


def test_reliability_all_sources_measured(make_user, test_dsn):
    user = make_user()
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO source_readings (user_id, sumber, category, vonis)"
                " VALUES (%s, 'Shopee', 'phone', 'Verified')", (user["user_id"],))
        conn.commit()
    result = metrics.reliability_all_sources(dsn=test_dsn)
    assert result["status"] == "passed"
    assert "Shopee x phone" in result["sources"]
    all_procedures = metrics.measure_all(user["user_id"], dsn=test_dsn)
    # Metrik alur aktif Fase 4 TIDAK diklaim di Fase 3.
    assert all_procedures["workflow_active_user_test"]["status"] == "pending"


def test_workflow_library_and_block_requests(make_user, test_dsn):
    user = make_user()
    result = library.record_workflow_success(user_id=user["user_id"], name="Tagihan bulanan",
                                      block=["check_bill", "send_notification"], dsn=test_dsn)
    assert result["successful_runs"] == 1
    library.record_workflow_success(user_id=user["user_id"], name="Tagihan bulanan",
                              block=["check_bill", "send_notification"], dsn=test_dsn)
    library.record_block_request(user_id=user["user_id"], block="check_balance_qris",
                                  reason="diminta user, belum ada", dsn=test_dsn)
    pemakaian = library.block_usage(dsn=test_dsn)
    block = next(p for p in pemakaian if p["block"] == "check_bill")
    assert block["used"] == 2
