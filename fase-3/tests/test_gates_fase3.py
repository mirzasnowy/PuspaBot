"""Tes gerbang Fase 3 (integrasi): wedge + graf + canary + metrik.

Satu alur ujung-ke-ujung di atas fixture (tanpa jaringan, tanpa nomor
pribadi Nero): bibit katalog -> bandingkan harga -> graf terisi ->
cek tagihan 4 provider -> prosedur terverifikasi -> canary mendeteksi
2 dari 2 suntikan -> metrik moat terukur.
"""

import httpx
import re

from puspa.billing import billing
from puspa.billing.fixture import DATA_UJI, SitusUji
from puspa.graphs import service as graf
from puspa.graphs.identity import harga_terkini
from puspa.moat import metrics
from puspa.pricing import catalog, tos
from puspa.pricing.compare import bandingkan_harga
from puspa.pricing.readers import HasilBaca

URLS = {
    "Tokopedia": "https://www.tokopedia.com/toko/galaxy-s24",
    "Shopee": "https://shopee.co.id/galaxy-s24",
    "Blibli": "https://www.blibli.com/p/galaxy-s24",
}
HARGA = {"Tokopedia": 12999000.0, "Shopee": 12499000.0, "Blibli": 12750000.0}


class PembacaPalsu:
    def __init__(self, harga):
        self.harga = harga

    def baca(self, url):
        pasar = "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
        return HasilBaca(sukses=True, marketplace=pasar, listing_url=url,
                         harga=self.harga[pasar], ongkir=15000, rating_toko=4.9,
                         nama="Samsung Galaxy S24", potongan_teks="[DATA: Galaxy S24]")


def _baca_ulang(url):
    pasar = "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
    return {"nilai": HARGA[pasar], "nama": "Samsung Galaxy S24"}


def test_gerbang_fase3_ujung_ke_ujung(make_user, agen_kedua, test_dsn):
    user = make_user()
    agen2 = agen_kedua(user["user_id"])

    # 1. Bibit katalog + hasil cek ToS tercatat.
    bibit = catalog.isi_bibit(dsn=test_dsn)
    assert bibit["produk_unik"] == 13
    assert len(tos.catat_hasil_cek(dsn=test_dsn)) == 7

    # 2. Wedge bandingkan harga (fixture) -> graf terisi.
    hasil = bandingkan_harga(
        user_id=user["user_id"], agen_id=user["agen_id"], nomor_model="SM-S921B",
        merek="Samsung", kategori="HP", listing_urls=URLS,
        pembaca={p: PembacaPalsu(HARGA) for p in URLS},
        baca_ulang=_baca_ulang, token=1200, mode="fixture", dsn=test_dsn)
    assert hasil["sukses"] is True and hasil["termurah"] == "Shopee"
    assert len(harga_terkini(hasil["produk"]["id"], dsn=test_dsn)) == 3

    # 3. Wedge tagihan 4 provider (fixture) -> prosedur lahir.
    with SitusUji() as situs:
        def baca_situs(url):
            provider = url.split("fixture://")[1].split("/")[0]
            return httpx.get(situs.url(provider), timeout=5).text

        def baca_ulang_tagihan(url):
            provider = url.split("fixture://")[1].split("/")[0] if "fixture://" in url else "pln"
            teks = httpx.get(situs.url(provider), timeout=5).text
            jumlah = float(re.search(r"Rp\s*([\d.]+)", teks).group(1).replace(".", ""))
            return {"nilai": jumlah, "nama": f"tagihan {provider}"}

        for provider, nomor in (("PLN", "123456789012"), ("PDAM", "987654321"),
                                ("pulsa", "081234567890"), ("paket data", "081234567890")):
            cek = billing.cek_tagihan(
                user_id=user["user_id"], agen_id=user["agen_id"], provider=provider,
                nomor_pelanggan=nomor, baca_situs=baca_situs,
                baca_ulang=baca_ulang_tagihan, tugas_id=f"tugas-{provider}",
                mode="fixture", dsn=test_dsn)
            assert cek["sukses"] is True, provider
            assert cek["jumlah"] == DATA_UJI[provider.lower()]["jumlah"]

    # 4. Tiga jalan sukses oleh 2 agen -> keempat prosedur terverifikasi
    #    + dipakai ulang agen lain.
    for provider in metrics.PROVIDER_MVP:
        prosedur = next(p for p in graf.daftar_prosedur(user["user_id"], dsn=test_dsn)
                        if p["layanan"] == provider)
        for agen in (user["agen_id"], agen2["agen_id"], user["agen_id"]):
            graf.catat_jalan(prosedur["id"], user["user_id"], True, agen_id=agen, dsn=test_dsn)
        graf.catat_pakai_ulang(prosedur_id=prosedur["id"], user_id=user["user_id"],
                               agen_id=agen2["agen_id"], tugas_id="tugas-reuse",
                               dsn=test_dsn)
    prosedur_hasil = metrics.prosedur_terverifikasi_dan_dipakai_ulang(user["user_id"], dsn=test_dsn)
    assert prosedur_hasil["status"] == "lolos"

    # 5. Metrik moat terukur SEBELUM canary merusak PLN (sebagian lolos,
    #    sebagian tertunda jujur).
    semua = metrics.ukur_semua(user["user_id"], dsn=test_dsn)
    assert semua["cakupan_pencocokan_otomatis"]["persen"] == 100.0
    assert semua["prosedur_terverifikasi_dipakai_ulang"]["status"] == "lolos"
    assert semua["keandalan_sumber"]["status"] == "lolos"
    assert semua["rasio_token"]["status"] == "tertunda"
    assert semua["alur_aktif_pengguna_uji"]["status"] == "tertunda"

    # 6. Canary mendeteksi 2 dari 2 suntikan di situs uji.
    prosedur_pln = next(p for p in graf.daftar_prosedur(user["user_id"], dsn=test_dsn)
                        if p["layanan"] == "PLN")

    def canary_check(_prosedur):
        with SitusUji() as situs:
            teks = httpx.get(situs.url("pln"), timeout=5).text
        jumlah = float(re.search(r"Rp\s*([\d.]+)", teks).group(1).replace(".", ""))
        return {"sukses": jumlah == DATA_UJI["pln"]["jumlah"]}

    terdeteksi = 0
    try:
        SitusUji.ubah("pln", jumlah=999999)
        for _ in range(2):
            hasil_canary = graf.jalankan_canary(
                prosedur_id=prosedur_pln["id"], user_id=user["user_id"],
                agen_id=user["agen_id"], jalankan=canary_check, dsn=test_dsn)
            if not hasil_canary["sukses"]:
                terdeteksi += 1
    finally:
        SitusUji.pulihkan("pln")
    assert terdeteksi == 2
    # Canary yang gagal 2x membuat prosedur PLN menjadi rusak — perilaku
    # yang benar (§3.3): prosedur rusak tidak dijalankan lagi.
    assert graf.ambil_prosedur(prosedur_pln["id"], user["user_id"], dsn=test_dsn)["status"] == "rusak"
