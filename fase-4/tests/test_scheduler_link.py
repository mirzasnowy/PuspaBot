"""Tes tick penjadwal -> jalan alur (§2.14): pemicu jadwal jatuh tempo
menjalankan alur, jadwal berikutnya dihitung dari definisi alur
(bukan +1 hari Fase 2), pemicu terlewat dijalankan sekali + dilaporkan,
idempotensi."""

from datetime import datetime, timezone

from puspa.workflow.runner import aktifkan_alur, tick_alur, uji_kering
from puspa.workflow.templates import buat_dari_templat
from puspa.workflow.triggers import simpan_ke_penjadwal


def _alur_tagihan_aktif(make_user, test_dsn, izin_user_penuh):
    user_id, agen_id = make_user()
    alur = buat_dari_templat("tagihan_bulanan", user_id=user_id, agen_id=agen_id,
                             dsn=test_dsn)
    kering = uji_kering(alur["id"], user_id=user_id, dsn=test_dsn)
    aktifkan_alur(alur["id"], user_id=user_id, izin_user=izin_user_penuh,
                  hasil_kering=kering, dsn=test_dsn)
    pemicu = simpan_ke_penjadwal(
        user_id=user_id, alur_id=str(alur["id"]),
        pemicu={"jenis": "jadwal", "mode": "tanggal", "tanggal": 25,
                "jam_mulai": "07:00"},
        sekarang=datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc),
        dsn=test_dsn)
    return user_id, alur, pemicu


def test_tick_jalankan_alur_jadwal_dan_geser_ke_bulan_depan(
        make_user, test_dsn, izin_user_penuh):
    user_id, alur, pemicu = _alur_tagihan_aktif(make_user, test_dsn,
                                               izin_user_penuh)
    hasil = tick_alur(
        dsn=test_dsn,
        sekarang=datetime(2026, 11, 25, 7, 5, tzinfo=timezone.utc))
    assert len(hasil) == 1
    assert hasil[0]["alur_id"] == str(alur["id"])
    assert hasil[0]["terlewat"] is False
    # Jalan alur tercatat (berhenti di siapkan bayar -> menunggu persetujuan).
    assert hasil[0]["jalan"]["status"] == "menunggu_persetujuan"
    # Jadwal berikutnya = 25 Desember (bukan 26 November / +1 hari).
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pemicu_berikutnya FROM pemicu WHERE id = %s",
                        (pemicu.id,))
            berikut = cur.fetchone()[0]
    assert (berikut.year, berikut.month, berikut.day) == (2026, 12, 25)


def test_tick_idempotent_tidak_jalan_dua_kali(make_user, test_dsn,
                                            izin_user_penuh):
    user_id, alur, pemicu = _alur_tagihan_aktif(make_user, test_dsn,
                                               izin_user_penuh)
    sekarang = datetime(2026, 11, 25, 7, 5, tzinfo=timezone.utc)
    assert len(tick_alur(dsn=test_dsn, sekarang=sekarang)) == 1
    assert tick_alur(dsn=test_dsn, sekarang=sekarang) == []


def test_tick_terlewat_dijalankan_sekali_dan_dilaporkan(make_user, test_dsn,
                                                       izin_user_penuh):
    user_id, alur, pemicu = _alur_tagihan_aktif(make_user, test_dsn,
                                               izin_user_penuh)
    # Host "mati" 2 hari: bangun 27 Nov — pemicu 25 Nov terlewat.
    hasil = tick_alur(
        dsn=test_dsn,
        sekarang=datetime(2026, 11, 27, 7, 5, tzinfo=timezone.utc),
        terlewat=True)
    assert len(hasil) == 1
    assert hasil[0]["terlewat"] is True
    # Dijalankan sekali (bukan berkali-kali): tick terlewat kedua kosong.
    assert tick_alur(
        dsn=test_dsn,
        sekarang=datetime(2026, 11, 27, 7, 6, tzinfo=timezone.utc),
        terlewat=True) == []
    # Kejadian pemicu_terlewat tercatat di Buku Kerja.
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM kejadian WHERE jenis = 'pemicu_terlewat'")
            assert cur.fetchone()[0] >= 1
