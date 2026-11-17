"""Tes wedge bandingkan harga (§4.1 revisi 7) + kredit + Skor Keandalan.

Semua pembacaan memakai Pembaca palsu (tanpa jaringan) + Pemeriksa
Fase 2 yang membaca ulang lewat callable. Mode "fixture" dipakai
untuk menguji logika wedge; mode "live" dipakai untuk menguji gerbang
ToS (pembaca TIDAK dipanggil sama sekali).
"""

import pytest

from puspa.graphs.identity import harga_terkini, tambah_produk
from puspa.pricing import cache as cache_bukti
from puspa.pricing import credits as kredit
from puspa.pricing.compare import bandingkan_harga, cek_detail
from puspa.pricing.readers import HasilBaca

URLS = {
    "Tokopedia": "https://www.tokopedia.com/toko/galaxy-s24",
    "Shopee": "https://shopee.co.id/galaxy-s24",
    "Blibli": "https://www.blibli.com/p/galaxy-s24",
}
HARGA = {"Tokopedia": 12999000.0, "Shopee": 12499000.0, "Blibli": 12750000.0}


class PembacaPalsu:
    """Pembaca fake: kembalikan harga tetap per marketplace."""

    def __init__(self, harga, nama="Samsung Galaxy S24"):
        self.harga = harga
        self.nama = nama
        self.dipanggil = 0

    def baca(self, url):
        self.dipanggil += 1
        pasar = "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
        return HasilBaca(sukses=True, marketplace=pasar, listing_url=url,
                         harga=self.harga[pasar], ongkir=15000, rating_toko=4.9,
                         nama=self.nama, potongan_teks="[DATA: Galaxy S24 Rp12.499.000]")


def _baca_ulang_benar(url):
    pasar = "Tokopedia" if "tokopedia" in url else "Shopee" if "shopee" in url else "Blibli"
    return {"nilai": HARGA[pasar], "nama": "Samsung Galaxy S24"}


def _siapkan_produk(make_user, test_dsn):
    user = make_user()
    tambah_produk(merek="Samsung", seri="Galaxy S24", nomor_model="SM-S921B",
                  kategori="HP", varian={"storage": "256GB"}, dsn=test_dsn)
    return user


def test_bandingkan_fixture_3_marketplace(make_user, test_dsn):
    user = _siapkan_produk(make_user, test_dsn)
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    hasil = bandingkan_harga(
        user_id=user["user_id"], agen_id=user["agen_id"], nomor_model="SM-S921B",
        merek="Samsung", kategori="HP", listing_urls=URLS, pembaca=pembaca,
        baca_ulang=_baca_ulang_benar, token=1200, mode="fixture", dsn=test_dsn)
    assert hasil["sukses"] is True
    assert hasil["termurah"] == "Shopee"
    assert hasil["selisih"] == 500000.0
    for pasar in URLS:
        assert hasil["hasil"][pasar]["vonis"] == "Terverifikasi"
        assert hasil["hasil"][pasar]["bukti_id"]
    # Graf terisi: harga terkini dari wedge.
    terkini = harga_terkini(hasil["produk"]["id"], dsn=test_dsn)
    assert len(terkini) == 3


def test_bandingkan_ragu_tanya_user_bukan_tebak(make_user, test_dsn):
    user = make_user()
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    hasil = bandingkan_harga(
        user_id=user["user_id"], agen_id=user["agen_id"], nomor_model="SM-S999Z",
        merek="Samsung", kategori="HP", listing_urls=URLS, pembaca=pembaca,
        baca_ulang=_baca_ulang_benar, mode="fixture", dsn=test_dsn)
    assert hasil["perlu_tanya"] is True
    assert all(p.dipanggil == 0 for p in pembaca.values())


def test_bandingkan_di_luar_scope_ditolak(make_user, test_dsn):
    user = make_user()
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    hasil = bandingkan_harga(
        user_id=user["user_id"], agen_id=user["agen_id"], nomor_model="AIRMAX90",
        kategori="fashion", listing_urls=URLS, pembaca=pembaca,
        baca_ulang=_baca_ulang_benar, mode="fixture", dsn=test_dsn)
    assert hasil["sukses"] is False and hasil["alasan"] == "di_luar_scope"
    assert "di luar scope" in hasil["pesan"]
    assert all(p.dipanggil == 0 for p in pembaca.values())


def test_bandingkan_live_marketplace_ditahan_gerbang_tos(make_user, test_dsn):
    user = _siapkan_produk(make_user, test_dsn)
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    hasil = bandingkan_harga(
        user_id=user["user_id"], agen_id=user["agen_id"], nomor_model="SM-S921B",
        merek="Samsung", kategori="HP", listing_urls=URLS, pembaca=pembaca,
        baca_ulang=_baca_ulang_benar, mode="live", dsn=test_dsn)
    assert hasil["sukses"] is True
    # TIDAK ADA upaya baca live sama sekali.
    assert all(p.dipanggil == 0 for p in pembaca.values())
    for pasar in URLS:
        assert hasil["hasil"][pasar]["tidak_bisa_dicek"] is True
        assert hasil["hasil"][pasar]["alasan"] == "syarat_layanan_menunggu_keputusan_nero"
    assert hasil["termurah"] is None


def test_satu_marketplace_gagal_sisanya_tetap_disajikan(make_user, test_dsn):
    """Aturan gagal terlihat (§4.1): satu marketplace tidak bisa dibaca
    -> bilang tidak bisa cek + tetap sajikan sisanya."""
    user = _siapkan_produk(make_user, test_dsn)

    class PembacaGagal:
        def baca(self, url):
            return HasilBaca(sukses=False, marketplace="Blibli", listing_url=url,
                             alasan="sumber_memblokir_http_403")

    pembaca = {"Tokopedia": PembacaPalsu(HARGA), "Shopee": PembacaPalsu(HARGA),
               "Blibli": PembacaGagal()}
    hasil = bandingkan_harga(
        user_id=user["user_id"], agen_id=user["agen_id"], nomor_model="SM-S921B",
        merek="Samsung", kategori="HP", listing_urls=URLS, pembaca=pembaca,
        baca_ulang=_baca_ulang_benar, mode="fixture", dsn=test_dsn)
    assert hasil["sukses"] is True
    assert hasil["termurah"] == "Shopee"
    assert hasil["hasil"]["Blibli"]["tidak_bisa_dicek"] is True
    assert "tidak bisa dicek" in hasil["hasil"]["Blibli"]["pesan"]


def test_pemeriksa_menangkap_harga_salah_yang_disuntik(make_user, test_dsn):
    user = _siapkan_produk(make_user, test_dsn)
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}

    def baca_ulang_salah(url):
        # Pembaca bilang 12.499.000, baca ulang menemukan 9.999.000.
        return {"nilai": 9999000.0, "nama": "Samsung Galaxy S24"}

    hasil = bandingkan_harga(
        user_id=user["user_id"], agen_id=user["agen_id"], nomor_model="SM-S921B",
        merek="Samsung", kategori="HP", listing_urls=URLS, pembaca=pembaca,
        baca_ulang=baca_ulang_salah, mode="fixture", dsn=test_dsn)
    assert all(hasil["hasil"][p]["vonis"] != "Terverifikasi" for p in URLS)


def test_pemeriksa_tidak_pakai_cache_bukti(make_user, test_dsn):
    """Cache dipakai Pembaca; Pemeriksa membaca ulang independen —
    dibuktikan: baca_ulang tetap dipanggil walau cache terisi."""
    user = _siapkan_produk(make_user, test_dsn)
    cache_bukti.simpan("Shopee:https://shopee.co.id/galaxy-s24", sumber="Shopee",
                       nilai={"harga": 1.0, "nama": "lama"}, bukti_id="bukti-lama",
                       dsn=test_dsn)
    panggilan = {"n": 0}

    def baca_ulang(url):
        panggilan["n"] += 1
        return _baca_ulang_benar(url)

    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    hasil = bandingkan_harga(
        user_id=user["user_id"], agen_id=user["agen_id"], nomor_model="SM-S921B",
        merek="Samsung", kategori="HP", listing_urls=URLS, pembaca=pembaca,
        baca_ulang=baca_ulang, mode="fixture", dsn=test_dsn)
    assert panggilan["n"] == 3
    assert hasil["hasil"]["Shopee"]["vonis"] == "Terverifikasi"


def test_skor_rendah_menurunkan_vonis(make_user, test_dsn):
    """Sumber < 80% Terverifikasi -> hasil maksimal Belum pasti (§3.4)."""
    import psycopg

    user = _siapkan_produk(make_user, test_dsn)
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            for i in range(10):
                vonis = "Terverifikasi" if i < 5 else "Tidak bisa dipastikan"
                cur.execute(
                    "INSERT INTO pembacaan_sumber (user_id, sumber, kategori, vonis)"
                    " VALUES (%s, 'Shopee', 'HP', %s)", (user["user_id"], vonis))
        conn.commit()
    pembaca = {p: PembacaPalsu(HARGA) for p in URLS}
    hasil = bandingkan_harga(
        user_id=user["user_id"], agen_id=user["agen_id"], nomor_model="SM-S921B",
        merek="Samsung", kategori="HP", listing_urls=URLS, pembaca=pembaca,
        baca_ulang=_baca_ulang_benar, mode="fixture", dsn=test_dsn)
    assert hasil["hasil"]["Shopee"]["vonis"] == "Belum pasti"
    assert hasil["hasil"]["Tokopedia"]["vonis"] == "Terverifikasi"


def test_cek_detail_1_kredit_dan_kredit_habis(make_user, test_dsn):
    user = _siapkan_produk(make_user, test_dsn)
    produk_id = None
    from puspa.graphs.identity import cari_produk

    produk_id = cari_produk("SM-S921B", dsn=test_dsn)["id"]
    assert kredit.sisa_kredit(user["user_id"], dsn=test_dsn) == 15
    detail = cek_detail(user_id=user["user_id"], produk_id=produk_id, dsn=test_dsn)
    assert detail["sisa_kredit"] == 14
    for _ in range(14):
        kredit.pakai_kredit(user["user_id"], dsn=test_dsn)
    assert kredit.sisa_kredit(user["user_id"], dsn=test_dsn) == 0
    with pytest.raises(kredit.KreditError):
        kredit.pakai_kredit(user["user_id"], dsn=test_dsn)
