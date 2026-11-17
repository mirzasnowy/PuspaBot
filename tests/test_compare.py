"""Tes wedge bandingkan harga (§4.1 revisi 7) + kredit + Skor Keandalan.

Semua pembacaan memakai Pembaca palsu (tanpa jaringan) + Pemeriksa
Fase 2 yang membaca ulang lewat callable. Mode "fixture" dipakai
untuk menguji logika wedge; mode "live" dipakai untuk menguji gerbang
ToS (pembaca TIDAK dipanggil sama sekali).
"""

import pytest

from puspa.graphs.identity import price_terkini, add_product
from puspa.pricing import cache as cache_evidence
from puspa.pricing import credits as credit
from puspa.pricing.compare import compare_price, check_detail
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
        pasar = "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
        return ResultRead(success=True, marketplace=pasar, listing_url=url,
                         price=self.price[pasar], shipping=15000, store_rating=4.9,
                         name=self.name, text_excerpt="[DATA: Galaxy S24 Rp12.499.000]")


def _read_ulang_correct(url):
    pasar = "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
    return {"value": HARGA[pasar], "name": "Samsung Galaxy S24"}


def _siapkan_product(make_user, test_dsn):
    user = make_user()
    add_product(brand="Samsung", series="Galaxy S24", model_number="SM-S921B",
                  category="phone", variant={"storage": "256GB"}, dsn=test_dsn)
    return user


def test_compare_fixture_3_marketplace(make_user, test_dsn):
    user = _siapkan_product(make_user, test_dsn)
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"], agent_id=user["agent_id"], model_number="SM-S921B",
        brand="Samsung", category="phone", listing_urls=URLS, pembaca=pembaca,
        reread=_read_ulang_correct, token=1200, mode="fixture", dsn=test_dsn)
    assert result["success"] is True
    assert result["termurah"] == "Shopee"
    assert result["selisih"] == 500000.0
    for pasar in URLS:
        assert result["result"][pasar]["vonis"] == "Verified"
        assert result["result"][pasar]["evidence_id"]
    # Graf terisi: harga terkini dari wedge.
    terkini = price_terkini(result["product"]["id"], dsn=test_dsn)
    assert len(terkini) == 3


def test_compare_ragu_ask_user_bukan_tebak(make_user, test_dsn):
    user = make_user()
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"], agent_id=user["agent_id"], model_number="SM-S999Z",
        brand="Samsung", category="phone", listing_urls=URLS, pembaca=pembaca,
        reread=_read_ulang_correct, mode="fixture", dsn=test_dsn)
    assert result["perlu_ask"] is True
    assert all(p.dipanggil == 0 for p in pembaca.values())


def test_compare_di_luar_scope_rejected(make_user, test_dsn):
    user = make_user()
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"], agent_id=user["agent_id"], model_number="AIRMAX90",
        category="fashion", listing_urls=URLS, pembaca=pembaca,
        reread=_read_ulang_correct, mode="fixture", dsn=test_dsn)
    assert result["success"] is False and result["reason"] == "di_luar_scope"
    assert "di luar scope" in result["message"]
    assert all(p.dipanggil == 0 for p in pembaca.values())


def test_compare_live_marketplace_ditahan_gerbang_tos(make_user, test_dsn):
    user = _siapkan_product(make_user, test_dsn)
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"], agent_id=user["agent_id"], model_number="SM-S921B",
        brand="Samsung", category="phone", listing_urls=URLS, pembaca=pembaca,
        reread=_read_ulang_correct, mode="live", dsn=test_dsn)
    assert result["success"] is True
    # TIDAK ADA upaya baca live sama sekali.
    assert all(p.dipanggil == 0 for p in pembaca.values())
    for pasar in URLS:
        assert result["result"][pasar]["not_can_dicek"] is True
        assert result["result"][pasar]["reason"] == "syarat_service_pending_keputusan_nero"
    assert result["termurah"] is None


def test_satu_marketplace_failed_sisanya_tetap_disajikan(make_user, test_dsn):
    """Aturan gagal terlihat (§4.1): satu marketplace tidak bisa dibaca
    -> bilang tidak bisa cek + tetap sajikan sisanya."""
    user = _siapkan_product(make_user, test_dsn)

    class PembacaFailed:
        def read(self, url):
            return ResultRead(success=False, marketplace="Blibli", listing_url=url,
                             reason="sumber_memblokir_http_403")

    pembaca = {"Tokopedia": PembacaPalsu(HARGA), "Shopee": PembacaPalsu(HARGA),
               "Blibli": PembacaFailed()}
    result = compare_price(
        user_id=user["user_id"], agent_id=user["agent_id"], model_number="SM-S921B",
        brand="Samsung", category="phone", listing_urls=URLS, pembaca=pembaca,
        reread=_read_ulang_correct, mode="fixture", dsn=test_dsn)
    assert result["success"] is True
    assert result["termurah"] == "Shopee"
    assert result["result"]["Blibli"]["not_can_dicek"] is True
    assert "tidak bisa dicek" in result["result"]["Blibli"]["message"]


def test_pemeriksa_menangkap_price_wrong_yang_disuntik(make_user, test_dsn):
    user = _siapkan_product(make_user, test_dsn)
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}

    def read_ulang_wrong(url):
        # Pembaca bilang 12.499.000, baca ulang menemukan 9.999.000.
        return {"value": 9999000.0, "name": "Samsung Galaxy S24"}

    result = compare_price(
        user_id=user["user_id"], agent_id=user["agent_id"], model_number="SM-S921B",
        brand="Samsung", category="phone", listing_urls=URLS, pembaca=pembaca,
        reread=read_ulang_wrong, mode="fixture", dsn=test_dsn)
    assert all(result["result"][p]["vonis"] != "Verified" for p in URLS)


def test_pemeriksa_not_use_cache_evidence(make_user, test_dsn):
    """Cache dipakai Pembaca; Pemeriksa membaca ulang independen —
    dibuktikan: baca_ulang tetap dipanggil walau cache terisi."""
    user = _siapkan_product(make_user, test_dsn)
    cache_evidence.save("Shopee:https://shopee.co.id/galaxy-s24", sumber="Shopee",
                       value={"price": 1.0, "name": "old"}, evidence_id="bukti-lama",
                       dsn=test_dsn)
    panggilan = {"n": 0}

    def reread(url):
        panggilan["n"] += 1
        return _read_ulang_correct(url)

    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"], agent_id=user["agent_id"], model_number="SM-S921B",
        brand="Samsung", category="phone", listing_urls=URLS, pembaca=pembaca,
        reread=reread, mode="fixture", dsn=test_dsn)
    assert panggilan["n"] == 3
    assert result["result"]["Shopee"]["vonis"] == "Verified"


def test_skor_low_menurunkan_vonis(make_user, test_dsn):
    """Sumber < 80% Terverifikasi -> hasil maksimal Belum pasti (§3.4)."""
    import psycopg

    user = _siapkan_product(make_user, test_dsn)
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            for i in range(10):
                vonis = "Verified" if i < 5 else "Cannot be determined"
                cur.execute(
                    "INSERT INTO source_readings (user_id, sumber, category, vonis)"
                    " VALUES (%s, 'Shopee', 'phone', %s)", (user["user_id"], vonis))
        conn.commit()
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    result = compare_price(
        user_id=user["user_id"], agent_id=user["agent_id"], model_number="SM-S921B",
        brand="Samsung", category="phone", listing_urls=URLS, pembaca=pembaca,
        reread=_read_ulang_correct, mode="fixture", dsn=test_dsn)
    assert result["result"]["Shopee"]["vonis"] == "Uncertain"
    assert result["result"]["Tokopedia"]["vonis"] == "Verified"


def test_check_detail_1_credit_and_credit_habis(make_user, test_dsn):
    user = _siapkan_product(make_user, test_dsn)
    product_id = None
    from puspa.graphs.identity import search_product

    product_id = search_product("SM-S921B", dsn=test_dsn)["id"]
    assert credit.sisa_credit(user["user_id"], dsn=test_dsn) == 15
    detail = check_detail(user_id=user["user_id"], product_id=product_id, dsn=test_dsn)
    assert detail["sisa_credit"] == 14
    for _ in range(14):
        credit.use_credit(user["user_id"], dsn=test_dsn)
    assert credit.sisa_credit(user["user_id"], dsn=test_dsn) == 0
    with pytest.raises(credit.CreditError):
        credit.use_credit(user["user_id"], dsn=test_dsn)
