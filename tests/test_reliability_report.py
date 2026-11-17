"""Tes Skor Keandalan (§3.4) + Lapor Salah (§3.8 revisi 7)."""

import psycopg

from puspa.moat import reliability, report


def _isi_pembacaan(test_dsn, user_id, sumber, kategori, terverifikasi, total):
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            for i in range(total):
                vonis = "Terverifikasi" if i < terverifikasi else "Belum pasti"
                cur.execute(
                    "INSERT INTO pembacaan_sumber (user_id, sumber, kategori, vonis)"
                    " VALUES (%s, %s, %s, %s)", (user_id, sumber, kategori, vonis))
        conn.commit()


def test_skor_sumber_persen_dan_umur(make_user, test_dsn):
    user = make_user()
    _isi_pembacaan(test_dsn, user["user_id"], "Shopee", "TV", 46, 50)
    skor = reliability.skor_sumber("Shopee", "TV", dsn=test_dsn)
    assert skor["persen_terverifikasi"] == 92.0
    assert skor["jumlah"] == 50
    assert skor["turunkan_vonis"] is False
    assert skor["umur_pembacaan_terakhir_detik"] is not None
    label = reliability.label_panel("Shopee", "TV", dsn=test_dsn)
    assert "92.0% terverifikasi dari 50 pembacaan terakhir" in label


def test_skor_sumber_di_bawah_80_turunkan_vonis(make_user, test_dsn):
    user = make_user()
    _isi_pembacaan(test_dsn, user["user_id"], "Blibli", "HP", 30, 50)
    skor = reliability.skor_sumber("Blibli", "HP", dsn=test_dsn)
    assert skor["persen_terverifikasi"] == 60.0
    assert skor["turunkan_vonis"] is True


def test_skor_sumber_tanpa_pembacaan(make_user, test_dsn):
    make_user()
    skor = reliability.skor_sumber("Tokopedia", "HP", dsn=test_dsn)
    assert skor["persen_terverifikasi"] is None
    assert skor["jumlah"] == 0
    assert "belum ada pembacaan" in reliability.label_panel("Tokopedia", "HP", dsn=test_dsn)


def test_skor_prosedur_dari_jalan(make_user, test_dsn):
    from puspa.graphs import service as graf

    user = make_user()
    prosedur = graf.tulis_prosedur(user_id=user["user_id"], layanan="PLN",
                                   langkah=[{"langkah": "baca"}], agen_id=user["agen_id"],
                                   dsn=test_dsn)
    skor = reliability.skor_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)
    assert skor["persen_terverifikasi"] == 100.0  # 1 jalan sukses saat tulis
    graf.catat_jalan(prosedur["id"], user["user_id"], False, agen_id=user["agen_id"], dsn=test_dsn)
    skor = reliability.skor_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)
    assert skor["persen_terverifikasi"] == 50.0
    assert skor["turunkan_vonis"] is True


def test_lapor_salah_menurunkan_skor_dan_dicatat_ledger(make_user, test_dsn, ledger):
    user = make_user()
    _isi_pembacaan(test_dsn, user["user_id"], "Shopee", "HP", 50, 50)
    assert reliability.skor_sumber("Shopee", "HP", dsn=test_dsn)["persen_terverifikasi"] == 100.0
    hasil = report.lapor_salah(
        bukti_id="bukti-1", user_id=user["user_id"], alasan="harga salah",
        sumber="Shopee", kategori="HP", tugas_id="tugas-lapor-1",
        agen_id=user["agen_id"], dsn=test_dsn, ledger=ledger)
    assert hasil["baca_ulang_diminta"] is True
    # Satu laporan "Tidak bisa dipastikan" masuk 50 terakhir -> 49/50 = 98%.
    assert reliability.skor_sumber("Shopee", "HP", dsn=test_dsn)["persen_terverifikasi"] == 98.0
    daftar = report.daftar_laporan("bukti-1", dsn=test_dsn)
    assert len(daftar) == 1 and daftar[0]["alasan"] == "harga salah"
    kejadian = ledger.events("tugas-lapor-1", jenis="laporan_salah")
    assert len(kejadian) == 1
    assert kejadian[0]["isi"]["bukti_id"] == "bukti-1"
