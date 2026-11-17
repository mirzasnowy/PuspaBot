"""Tes gerbang Fase 3 (integrasi): wedge + graf + canary + metrik.

Satu alur ujung-ke-ujung di atas fixture (tanpa jaringan, tanpa nomor
pribadi Nero): bibit katalog -> bandingkan harga -> graf terisi ->
cek tagihan 4 provider -> prosedur terverifikasi -> canary mendeteksi
2 dari 2 suntikan -> metrik moat terukur.
"""

import httpx
import re

from puspa.billing import billing
from puspa.billing.fixture import DATA_TEST, SitusTest
from puspa.graphs import service as graf
from puspa.graphs.identity import price_terkini
from puspa.moat import metrics
from puspa.pricing import catalog, tos
from puspa.pricing.compare import compare_price
from puspa.pricing.readers import ResultRead

URLS = {
    "Tokopedia": "https://www.tokopedia.com/toko/galaxy-s24",
    "Shopee": "https://shopee.co.id/galaxy-s24",
    "Blibli": "https://www.blibli.com/p/galaxy-s24",
}
HARGA = {"Tokopedia": 12999000.0, "Shopee": 12499000.0, "Blibli": 12750000.0}


class PembacaPalsu:
    def __init__(self, price):
        self.price = price

    def read(self, url):
        pasar = "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
        return ResultRead(success=True, marketplace=pasar, listing_url=url,
                         price=self.price[pasar], shipping=15000, store_rating=4.9,
                         name="Samsung Galaxy S24", text_excerpt="[DATA: Galaxy S24]")


def _read_ulang(url):
    pasar = "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
    return {"value": HARGA[pasar], "name": "Samsung Galaxy S24"}


def test_gerbang_fase3_ujung_to_ujung(make_user, agent_kedua, test_dsn):
    user = make_user()
    agen2 = agent_kedua(user["user_id"])

    # 1. Bibit katalog + hasil cek ToS tercatat.
    bibit = catalog.content_bibit(dsn=test_dsn)
    assert bibit["product_unik"] == 13
    assert len(tos.record_result_check(dsn=test_dsn)) == 7

    # 2. Wedge bandingkan harga (fixture) -> graf terisi.
    result = compare_price(
        user_id=user["user_id"], agent_id=user["agent_id"], model_number="SM-S921B",
        brand="Samsung", category="phone", listing_urls=URLS,
        pembaca={p: PembacaPalsu(HARGA) for p in URLS},
        reread=_read_ulang, token=1200, mode="fixture", dsn=test_dsn)
    assert result["success"] is True and result["termurah"] == "Shopee"
    assert len(price_terkini(result["product"]["id"], dsn=test_dsn)) == 3

    # 3. Wedge tagihan 4 provider (fixture) -> prosedur lahir.
    with SitusTest() as situs:
        def read_situs(url):
            provider = url.split("fixture://")[1].split("/")[0]
            return httpx.get(situs.url(provider), timeout=5).text

        def read_ulang_bill(url):
            provider = url.split("fixture://")[1].split("/")[0] if "fixture://" in url else "pln"
            text = httpx.get(situs.url(provider), timeout=5).text
            amount = float(re.search(r"Rp\s*([\d.]+)", text).group(1).replace(".", ""))
            return {"value": amount, "name": f"tagihan {provider}"}

        for provider, number in (("PLN", "123456789012"), ("PDAM", "987654321"),
                                ("airtime", "081234567890"), ("paket data", "081234567890")):
            check = billing.check_bill(
                user_id=user["user_id"], agent_id=user["agent_id"], provider=provider,
                number_pelanggan=number, read_situs=read_situs,
                reread=read_ulang_bill, task_id=f"tugas-{provider}",
                mode="fixture", dsn=test_dsn)
            assert check["success"] is True, provider
            assert check["amount"] == DATA_TEST[provider.lower()]["amount"]

    # 4. Tiga jalan sukses oleh 2 agen -> keempat prosedur terverifikasi
    #    + dipakai ulang agen lain.
    for provider in metrics.PROVIDER_MVP:
        prosedur = next(p for p in graf.items_prosedur(user["user_id"], dsn=test_dsn)
                        if p["service"] == provider)
        for agent in (user["agent_id"], agen2["agent_id"], user["agent_id"]):
            graf.record_jalan(prosedur["id"], user["user_id"], True, agent_id=agent, dsn=test_dsn)
        graf.record_use_ulang(prosedur_id=prosedur["id"], user_id=user["user_id"],
                               agent_id=agen2["agent_id"], task_id="tugas-reuse",
                               dsn=test_dsn)
    prosedur_result = metrics.prosedur_verified_and_dipakai_ulang(user["user_id"], dsn=test_dsn)
    assert prosedur_result["status"] == "passed"

    # 5. Metrik moat terukur SEBELUM canary merusak PLN (sebagian lolos,
    #    sebagian tertunda jujur).
    semua = metrics.ukur_all(user["user_id"], dsn=test_dsn)
    assert semua["cakupan_pencocokan_automatic"]["persen"] == 100.0
    assert semua["prosedur_verified_dipakai_ulang"]["status"] == "passed"
    assert semua["keandalan_sumber"]["status"] == "passed"
    assert semua["rasio_token"]["status"] == "tertunda"
    assert semua["workflow_active_user_test"]["status"] == "tertunda"

    # 6. Canary mendeteksi 2 dari 2 suntikan di situs uji.
    prosedur_pln = next(p for p in graf.items_prosedur(user["user_id"], dsn=test_dsn)
                        if p["service"] == "PLN")

    def canary_check(_prosedur):
        with SitusTest() as situs:
            text = httpx.get(situs.url("pln"), timeout=5).text
        amount = float(re.search(r"Rp\s*([\d.]+)", text).group(1).replace(".", ""))
        return {"success": amount == DATA_TEST["pln"]["amount"]}

    terdeteksi = 0
    try:
        SitusTest.change("pln", amount=999999)
        for _ in range(2):
            result_canary = graf.jalankan_canary(
                prosedur_id=prosedur_pln["id"], user_id=user["user_id"],
                agent_id=user["agent_id"], jalankan=canary_check, dsn=test_dsn)
            if not result_canary["success"]:
                terdeteksi += 1
    finally:
        SitusTest.pulihkan("pln")
    assert terdeteksi == 2
    # Canary yang gagal 2x membuat prosedur PLN menjadi rusak — perilaku
    # yang benar (§3.3): prosedur rusak tidak dijalankan lagi.
    assert graf.take_prosedur(prosedur_pln["id"], user["user_id"], dsn=test_dsn)["status"] == "broken"
