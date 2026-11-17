"""Tes metrik moat gerbang 8 (§3.6) + pustaka alur (§3.8 revisi 7)."""

import psycopg
import pytest

from puspa.graphs import service as graf
from puspa.graphs.identity import add_product, tautkan_listing
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


def test_cakupan_pencocokan_automatic(make_user, test_dsn):
    user = make_user()
    product = add_product(brand="Samsung", model_number="SM-S921B", category="phone", dsn=test_dsn)
    for i, pasar in enumerate(("Tokopedia", "Shopee", "Blibli")):
        tautan = tautkan_listing(product_id=product["id"], user_id=user["user_id"],
                                 marketplace=pasar,
                                 listing_url=f"https://www.{pasar.lower()}.com/x{i}",
                                 dsn=test_dsn)
        if pasar == "Blibli":
            from puspa.graphs.identity import konfirmasi_tautan

            konfirmasi_tautan(tautan["id"], user_id=user["user_id"], accepted=True, dsn=test_dsn)
    result = metrics.cakupan_pencocokan_automatic(dsn=test_dsn)
    assert result["persen"] == 66.7
    assert result["status"] == "passed"


def test_cakupan_without_data_tertunda(test_dsn):
    result = metrics.cakupan_pencocokan_automatic(dsn=test_dsn)
    assert result["status"] == "tertunda" and result["persen"] is None


def test_prosedur_verified_and_dipakai_ulang(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])
    for provider in metrics.PROVIDER_MVP:
        prosedur = graf.tulis_prosedur(user_id=user["user_id"], service=provider,
                                       step=[{"step": "read"}],
                                       agent_id=user["agent_id"], dsn=test_dsn)
        for agent in (user["agent_id"], agen2["agent_id"], user["agent_id"]):
            graf.record_jalan(prosedur["id"], user["user_id"], True, agent_id=agent, dsn=test_dsn)
        graf.record_use_ulang(prosedur_id=prosedur["id"], user_id=user["user_id"],
                               agent_id=agen2["agent_id"], task_id="tugas-reuse",
                               dsn=test_dsn)
    result = metrics.prosedur_verified_and_dipakai_ulang(user["user_id"], dsn=test_dsn)
    assert result["status"] == "passed"
    assert all(v["verified"] and v["dipakai_ulang"]
               for v in result["per_provider"].values())


def test_rasio_token_tertunda_without_masa_test(test_dsn):
    result = metrics.rasio_token_perbandingan(dsn=test_dsn)
    assert result["status"] == "tertunda" and result["rasio"] is None


def test_rasio_token_menghitung_week(make_user, test_dsn):
    from datetime import datetime, timedelta, timezone

    user = make_user()
    sekarang = datetime.now(timezone.utc)
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            for i in range(4):
                cur.execute(
                    "INSERT INTO token_metrics (task_id, user_id, type, token, dicatat)"
                    " VALUES (%s, %s, 'compare_prices', 1000, %s)",
                    (f"t-awal-{i}", user["user_id"], sekarang - timedelta(days=10)))
            for i in range(4):
                cur.execute(
                    "INSERT INTO token_metrics (task_id, user_id, type, token, dicatat)"
                    " VALUES (%s, %s, 'compare_prices', 700, %s)",
                    (f"t-akhir-{i}", user["user_id"], sekarang - timedelta(days=1)))
        conn.commit()
    result = metrics.rasio_token_perbandingan(dsn=test_dsn)
    assert result["rasio"] == 0.7
    assert result["status"] == "passed"


def test_keandalan_all_sumber_and_ukur_all(make_user, test_dsn):
    user = make_user()
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO source_readings (user_id, sumber, category, vonis)"
                " VALUES (%s, 'Shopee', 'phone', 'Verified')", (user["user_id"],))
        conn.commit()
    result = metrics.keandalan_all_sumber(dsn=test_dsn)
    assert result["status"] == "passed"
    assert "Shopee x phone" in result["sumber"]
    semua = metrics.ukur_all(user["user_id"], dsn=test_dsn)
    # Metrik alur aktif Fase 4 TIDAK diklaim di Fase 3.
    assert semua["workflow_active_user_test"]["status"] == "tertunda"


def test_pustaka_workflow_and_permintaan_block(make_user, test_dsn):
    user = make_user()
    result = library.record_workflow_success(user_id=user["user_id"], name="Tagihan bulanan",
                                      block=["check_bill", "send_notification"], dsn=test_dsn)
    assert result["successful_runs"] == 1
    library.record_workflow_success(user_id=user["user_id"], name="Tagihan bulanan",
                              block=["check_bill", "send_notification"], dsn=test_dsn)
    library.record_permintaan_block(user_id=user["user_id"], block="check_balance_qris",
                                  reason="diminta user, belum ada", dsn=test_dsn)
    pemakaian = library.pemakaian_block(dsn=test_dsn)
    block = next(p for p in pemakaian if p["block"] == "check_bill")
    assert block["dipakai"] == 2
