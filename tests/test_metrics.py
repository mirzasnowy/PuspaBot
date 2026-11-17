"""Tes metrik moat gerbang 8 (§3.6) + pustaka alur (§3.8 revisi 7)."""

import psycopg

from puspa.graphs import service as graf
from puspa.graphs.identity import tambah_produk, tautkan_listing
from puspa.moat import library, metrics


def test_cakupan_pencocokan_otomatis(make_user, test_dsn):
    user = make_user()
    produk = tambah_produk(merek="Samsung", nomor_model="SM-S921B", kategori="HP", dsn=test_dsn)
    for i, pasar in enumerate(("Tokopedia", "Shopee", "Blibli")):
        tautan = tautkan_listing(produk_id=produk["id"], user_id=user["user_id"],
                                 marketplace=pasar,
                                 listing_url=f"https://www.{pasar.lower()}.com/x{i}",
                                 dsn=test_dsn)
        if pasar == "Blibli":
            from puspa.graphs.identity import konfirmasi_tautan

            konfirmasi_tautan(tautan["id"], user_id=user["user_id"], diterima=True, dsn=test_dsn)
    hasil = metrics.cakupan_pencocokan_otomatis(dsn=test_dsn)
    assert hasil["persen"] == 66.7
    assert hasil["status"] == "lolos"


def test_cakupan_tanpa_data_tertunda(test_dsn):
    hasil = metrics.cakupan_pencocokan_otomatis(dsn=test_dsn)
    assert hasil["status"] == "tertunda" and hasil["persen"] is None


def test_prosedur_terverifikasi_dan_dipakai_ulang(make_user, agen_kedua, test_dsn):
    user = make_user()
    agen2 = agen_kedua(user["user_id"])
    for provider in metrics.PROVIDER_MVP:
        prosedur = graf.tulis_prosedur(user_id=user["user_id"], layanan=provider,
                                       langkah=[{"langkah": "baca"}],
                                       agen_id=user["agen_id"], dsn=test_dsn)
        for agen in (user["agen_id"], agen2["agen_id"], user["agen_id"]):
            graf.catat_jalan(prosedur["id"], user["user_id"], True, agen_id=agen, dsn=test_dsn)
        graf.catat_pakai_ulang(prosedur_id=prosedur["id"], user_id=user["user_id"],
                               agen_id=agen2["agen_id"], tugas_id="tugas-reuse",
                               dsn=test_dsn)
    hasil = metrics.prosedur_terverifikasi_dan_dipakai_ulang(user["user_id"], dsn=test_dsn)
    assert hasil["status"] == "lolos"
    assert all(v["terverifikasi"] and v["dipakai_ulang"]
               for v in hasil["per_provider"].values())


def test_rasio_token_tertunda_tanpa_masa_uji(test_dsn):
    hasil = metrics.rasio_token_perbandingan(dsn=test_dsn)
    assert hasil["status"] == "tertunda" and hasil["rasio"] is None


def test_rasio_token_menghitung_minggu(make_user, test_dsn):
    from datetime import datetime, timedelta, timezone

    user = make_user()
    sekarang = datetime.now(timezone.utc)
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            for i in range(4):
                cur.execute(
                    "INSERT INTO metrik_token (tugas_id, user_id, jenis, token, dicatat)"
                    " VALUES (%s, %s, 'bandingkan_harga', 1000, %s)",
                    (f"t-awal-{i}", user["user_id"], sekarang - timedelta(days=10)))
            for i in range(4):
                cur.execute(
                    "INSERT INTO metrik_token (tugas_id, user_id, jenis, token, dicatat)"
                    " VALUES (%s, %s, 'bandingkan_harga', 700, %s)",
                    (f"t-akhir-{i}", user["user_id"], sekarang - timedelta(days=1)))
        conn.commit()
    hasil = metrics.rasio_token_perbandingan(dsn=test_dsn)
    assert hasil["rasio"] == 0.7
    assert hasil["status"] == "lolos"


def test_keandalan_semua_sumber_dan_ukur_semua(make_user, test_dsn):
    user = make_user()
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pembacaan_sumber (user_id, sumber, kategori, vonis)"
                " VALUES (%s, 'Shopee', 'HP', 'Terverifikasi')", (user["user_id"],))
        conn.commit()
    hasil = metrics.keandalan_semua_sumber(dsn=test_dsn)
    assert hasil["status"] == "lolos"
    assert "Shopee x HP" in hasil["sumber"]
    semua = metrics.ukur_semua(user["user_id"], dsn=test_dsn)
    # Metrik alur aktif Fase 4 TIDAK diklaim di Fase 3.
    assert semua["alur_aktif_pengguna_uji"]["status"] == "tertunda"


def test_pustaka_alur_dan_permintaan_blok(make_user, test_dsn):
    user = make_user()
    hasil = library.catat_alur_sukses(user_id=user["user_id"], nama="Tagihan bulanan",
                                      blok=["cek_tagihan", "kirim_notif"], dsn=test_dsn)
    assert hasil["jalan_sukses"] == 1
    library.catat_alur_sukses(user_id=user["user_id"], nama="Tagihan bulanan",
                              blok=["cek_tagihan", "kirim_notif"], dsn=test_dsn)
    library.catat_permintaan_blok(user_id=user["user_id"], blok="cek_saldo_qris",
                                  alasan="diminta user, belum ada", dsn=test_dsn)
    pemakaian = library.pemakaian_blok(dsn=test_dsn)
    blok = next(p for p in pemakaian if p["blok"] == "cek_tagihan")
    assert blok["dipakai"] == 2
