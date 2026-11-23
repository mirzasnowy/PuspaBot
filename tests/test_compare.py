"""Tes wedge bandingkan harga (§4.1 revisi 7) + kredit + Skor Keandalan.

Semua pembacaan memakai Pembaca palsu (tanpa jaringan) + Pemeriksa
Fase 2 yang membaca ulang lewat callable. Mode "fixture" dipakai
untuk menguji logika wedge; mode "live" dipakai untuk menguji gerbang
ToS (pembaca TIDAK dipanggil sama sekali).
"""

import pytest

from puspa.graphs.identity import add_product, current_prices
from puspa.pricing import cache as cache_evidence
from puspa.pricing import credits as credit
from puspa.pricing.compare import check_detail, compare_price
from puspa.pricing.readers import ResultRead

URLS = {
    "Tokopedia": "https://www.tokopedia.com/toko/galaxy-s24",
    "Shopee": "https://shopee.co.id/galaxy-s24",
    "Blibli": "https://www.blibli.com/p/galaxy-s24",
}
HARGA = {"Tokopedia": 12999000.0, "Shopee": 12499000.0, "Blibli": 12750000.0}


class PembacaPalsu:
    """Pembaca fake: kembalikan harga tetap per marketplace."""

    def __init__(self, price, name="Samsung Galaxy S24"):
        self.price = price
        self.name = name
        self.dipanggil = 0

    def read(self, url):
        self.dipanggil += 1
        marketplace = (
            "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
        )
        return ResultRead(
            success=True,
            marketplace=marketplace,
            listing_url=url,
            price=self.price[marketplace],
            shipping=15000,
            store_rating=4.9,
            name=self.name,
            text_excerpt="[DATA: Galaxy S24 Rp12.499.000]",
        )


def _read_ulang_correct(url):
    marketplace = "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
    return {"value": HARGA[marketplace], "name": "Samsung Galaxy S24"}


def _siapkan_product(make_user, test_dsn):
    user = make_user()
    add_product(
        brand="Samsung",
        series="Galaxy S24",
        model_number="SM-S921B",
        category="phone",
        variant={"storage": "256GB"},
        dsn=test_dsn,
    )
    return user


def test_compare_fixture_3_marketplace(make_user, test_dsn):
    user = _siapkan_product(make_user, test_dsn)
    reader = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        model_number="SM-S921B",
        brand="Samsung",
        category="phone",
        listing_urls=URLS,
        reader=reader,
        reread=_read_ulang_correct,
        token=1200,
        mode="fixture",
        dsn=test_dsn,
    )
    assert result["success"] is True
    assert result["cheapest"] == "Shopee"
    assert result["spread"] == 500000.0
    for marketplace in URLS:
        assert result["result"][marketplace]["verdict"] == "Verified"
        assert result["result"][marketplace]["evidence_id"]
    # Graf terisi: harga terkini dari wedge.
    terkini = current_prices(result["product"]["id"], dsn=test_dsn)
    assert len(terkini) == 3


def test_compare_when_uncertain_asks_user_not_guess(make_user, test_dsn):
    user = make_user()
    reader = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        model_number="SM-S999Z",
        brand="Samsung",
        category="phone",
        listing_urls=URLS,
        reader=reader,
        reread=_read_ulang_correct,
        mode="fixture",
        dsn=test_dsn,
    )
    assert result["perlu_ask"] is True
    assert all(p.dipanggil == 0 for p in reader.values())


def test_compare_outside_scope_rejected(make_user, test_dsn):
    user = make_user()
    reader = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        model_number="AIRMAX90",
        category="fashion",
        listing_urls=URLS,
        reader=reader,
        reread=_read_ulang_correct,
        mode="fixture",
        dsn=test_dsn,
    )
    assert result["success"] is False and result["reason"] == "di_luar_scope"
    assert "di luar scope" in result["message"]
    assert all(p.dipanggil == 0 for p in reader.values())


def test_compare_live_marketplace_held_by_tos_gate(make_user, test_dsn):
    user = _siapkan_product(make_user, test_dsn)
    reader = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        model_number="SM-S921B",
        brand="Samsung",
        category="phone",
        listing_urls=URLS,
        reader=reader,
        reread=_read_ulang_correct,
        mode="live",
        dsn=test_dsn,
    )
    assert result["success"] is True
    # TIDAK ADA upaya baca live sama sekali.
    assert all(p.dipanggil == 0 for p in reader.values())
    for marketplace in URLS:
        assert result["result"][marketplace]["not_can_dicek"] is True
        assert result["result"][marketplace]["reason"] == "syarat_service_pending_keputusan_nero"
    assert result["cheapest"] is None


def test_one_marketplace_failed_rest_still_presented(make_user, test_dsn):
    """Aturan gagal terlihat (§4.1): satu marketplace tidak bisa dibaca
    -> bilang tidak bisa cek + tetap sajikan sisanya."""
    user = _siapkan_product(make_user, test_dsn)

    class PembacaFailed:
        def read(self, url):
            return ResultRead(
                success=False,
                marketplace="Blibli",
                listing_url=url,
                reason="sumber_memblokir_http_403",
            )

    reader = {
        "Tokopedia": PembacaPalsu(HARGA),
        "Shopee": PembacaPalsu(HARGA),
        "Blibli": PembacaFailed(),
    }
    result = compare_price(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        model_number="SM-S921B",
        brand="Samsung",
        category="phone",
        listing_urls=URLS,
        reader=reader,
        reread=_read_ulang_correct,
        mode="fixture",
        dsn=test_dsn,
    )
    assert result["success"] is True
    assert result["cheapest"] == "Shopee"
    assert result["result"]["Blibli"]["not_can_dicek"] is True
    assert "tidak bisa dicek" in result["result"]["Blibli"]["message"]


def test_verifier_catches_injected_wrong_price(make_user, test_dsn):
    user = _siapkan_product(make_user, test_dsn)
    reader = {p: PembacaPalsu(HARGA) for p in URLS}

    def read_ulang_wrong(url):
        # Pembaca bilang 12.499.000, baca ulang menemukan 9.999.000.
        return {"value": 9999000.0, "name": "Samsung Galaxy S24"}

    result = compare_price(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        model_number="SM-S921B",
        brand="Samsung",
        category="phone",
        listing_urls=URLS,
        reader=reader,
        reread=read_ulang_wrong,
        mode="fixture",
        dsn=test_dsn,
    )
    assert all(result["result"][p]["verdict"] != "Verified" for p in URLS)


def test_verifier_does_not_use_evidence_cache(make_user, test_dsn):
    """Cache dipakai Pembaca; Pemeriksa membaca ulang independen —
    dibuktikan: baca_ulang tetap dipanggil walau cache terisi."""
    user = _siapkan_product(make_user, test_dsn)
    cache_evidence.save(
        "Shopee:https://shopee.co.id/galaxy-s24",
        source="Shopee",
        value={"price": 1.0, "name": "old"},
        evidence_id="bukti-lama",
        dsn=test_dsn,
    )
    panggilan = {"n": 0}

    def reread(url):
        panggilan["n"] += 1
        return _read_ulang_correct(url)

    reader = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        model_number="SM-S921B",
        brand="Samsung",
        category="phone",
        listing_urls=URLS,
        reader=reader,
        reread=reread,
        mode="fixture",
        dsn=test_dsn,
    )
    assert panggilan["n"] == 3
    assert result["result"]["Shopee"]["verdict"] == "Verified"


def test_score_low_lowers_verdict(make_user, test_dsn):
    """Sumber < 80% Terverifikasi -> hasil maksimal Belum pasti (§3.4)."""
    from puspa.db import connect as _db_connect

    user = _siapkan_product(make_user, test_dsn)
    with _db_connect(test_dsn) as conn:
        with conn.cursor() as cur:
            for i in range(10):
                verdict = "Verified" if i < 5 else "Cannot be determined"
                cur.execute(
                    "INSERT INTO source_readings (user_id, sumber, category, vonis)"
                    " VALUES (%s, 'Shopee', 'phone', %s)",
                    (user["user_id"], verdict),
                )
        conn.commit()
    reader = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        model_number="SM-S921B",
        brand="Samsung",
        category="phone",
        listing_urls=URLS,
        reader=reader,
        reread=_read_ulang_correct,
        mode="fixture",
        dsn=test_dsn,
    )
    assert result["result"]["Shopee"]["verdict"] == "Uncertain"
    assert result["result"]["Tokopedia"]["verdict"] == "Verified"


def test_check_detail_1_credit_and_credit_exhausted(make_user, test_dsn):
    user = _siapkan_product(make_user, test_dsn)
    product_id = None
    from puspa.graphs.identity import search_product

    product_id = search_product("SM-S921B", dsn=test_dsn)["id"]
    assert credit.remaining_credit(user["user_id"], dsn=test_dsn) == 15
    detail = check_detail(user_id=user["user_id"], product_id=product_id, dsn=test_dsn)
    assert detail["remaining_credit"] == 14
    for _ in range(14):
        credit.use_credit(user["user_id"], dsn=test_dsn)
    assert credit.remaining_credit(user["user_id"], dsn=test_dsn) == 0
    with pytest.raises(credit.CreditError):
        credit.use_credit(user["user_id"], dsn=test_dsn)
