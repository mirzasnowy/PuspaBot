"""Tes watchlist + sampler (§3.8) dan cache bukti bersama (§3.8)."""

import psycopg
import pytest

from puspa.graphs.identity import add_product
from puspa.pricing import cache as cache_evidence
from puspa.pricing import watchlist


def _product(make_user, test_dsn, model_number="SM-S921B"):
    user = make_user()
    product = add_product(brand="Samsung", model_number=model_number,
                           category="phone", dsn=test_dsn)
    return user, product


def test_watchlist_add_and_items(make_user, test_dsn):
    user, product = _product(make_user, test_dsn)
    result = watchlist.add_watchlist(user_id=user["user_id"], product_id=product["id"],
                                       price_threshold=12000000, dsn=test_dsn)
    assert result["active"] is True
    items = watchlist.items_watchlist(user["user_id"], dsn=test_dsn)
    assert len(items) == 1 and items[0]["price_threshold"] == 12000000.0


def test_sampler_menghormati_anggaran_20_per_day(make_user, test_dsn):
    user, product = _product(make_user, test_dsn)
    watchlist.add_watchlist(user_id=user["user_id"], product_id=product["id"], dsn=test_dsn)
    assert watchlist.sisa_anggaran_read(user["user_id"], dsn=test_dsn) == 20
    result = watchlist.jalankan_sampler(
        user_id=user["user_id"], agent_id=user["agent_id"],
        read_price=lambda b: {"price": 12499000, "evidence_id": "bukti-x",
                              "tautan_id": "t-x"},
        dsn=test_dsn)
    assert result["dibaca"] == [product["id"]]
    assert result["sisa_anggaran"] == 19
    # Habiskan anggaran -> sampler melewati barang, bukan membaca diam-diam.
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE read_budgets SET amount = 20 WHERE user_id = %s",
                (user["user_id"],))
        conn.commit()
    result = watchlist.jalankan_sampler(
        user_id=user["user_id"], agent_id=user["agent_id"],
        read_price=lambda b: {"price": 1, "evidence_id": "b", "tautan_id": "t"},
        dsn=test_dsn)
    assert result["dibaca"] == []
    assert result["dilewati"][0]["reason"] == "anggaran_read_daily_habis"


def test_sampler_alert_price_turun_di_bawah_ambang(make_user, test_dsn):
    user, product = _product(make_user, test_dsn)
    watchlist.add_watchlist(user_id=user["user_id"], product_id=product["id"],
                               price_threshold=13000000, dsn=test_dsn)
    result = watchlist.jalankan_sampler(
        user_id=user["user_id"], agent_id=user["agent_id"],
        read_price=lambda b: {"price": 12499000, "evidence_id": "bukti-x",
                              "tautan_id": "t-x"},
        dsn=test_dsn)
    assert len(result["alert"]) == 1
    assert result["alert"][0]["price"] == 12499000


def test_cache_evidence_ttl_30_minute_and_statistics(test_dsn):
    cache_evidence.save("Tokopedia:https://x", sumber="Tokopedia",
                       value={"price": 1000}, evidence_id="bukti-1", dsn=test_dsn)
    assert cache_evidence.take("Tokopedia:https://x", dsn=test_dsn)["evidence_id"] == "bukti-1"
    assert cache_evidence.take("Tokopedia:https://tidak-ada", dsn=test_dsn) is None
    # Kedaluwarsa -> None (pemanggil wajib baca ulang sumber).
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE evidence_cache SET expires_at = now() - INTERVAL '1 hour'"
                " WHERE key = 'Tokopedia:https://x'")
        conn.commit()
    assert cache_evidence.take("Tokopedia:https://x", dsn=test_dsn) is None
    statistics = cache_evidence.statistics_hit(dsn=test_dsn)
    assert statistics["total_hit"] == 1
    assert statistics["entri_cache"] == 1
