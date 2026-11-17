"""Tes modul kustom (§2.13): gerbang manifes, isolasi, versi, hapus+ekspor,
Tagihan sebagai manifes."""

import pytest

from puspa.modules.manifest import (
    ModuleError, aktifkan_modul, ambil_modul, buat_modul, daftar_modul,
    hapus_modul, jalankan_gerbang, rollback_modul, ubah_modul,
)
from puspa.modules.notifications import evaluasi_notif
from puspa.modules.tables import (
    TableError, baca_tabel, baca_tabel_modul_lain, daftar_tabel, tambah_baris,
)
from puspa.modules.tagihan import MANIFES_TAGIHAN, buat_modul_tagihan
from puspa.modules.views import render_modul

MANIFES_MINIMAL = {
    "nama": "Catatan belanja",
    "tampilan": [
        {"jenis": "tabel", "judul": "Daftar belanja", "tabel": "belanja",
         "kolom": ["barang", "harga"]},
    ],
    "tabel": [
        {"nama": "belanja", "kolom": [
            {"nama": "barang", "tipe": "teks"},
            {"nama": "harga", "tipe": "Rupiah"},
        ]},
    ],
    "contoh_data": {
        "belanja": [{"barang": "Beras 5 kg", "harga": 65000}],
    },
}


def test_tagihan_sebagai_manifes_lolos_kelima_gerbang(make_user, test_dsn,
                                                     izin_user_penuh):
    user_id, _ = make_user()
    hasil = jalankan_gerbang(dict(MANIFES_TAGIHAN), dsn=test_dsn)
    assert hasil["lolos"] is True, hasil["masalah"]
    modul = buat_modul_tagihan(user_id=user_id, dsn=test_dsn)
    assert modul["status"] == "draf"
    aktif = aktifkan_modul(modul["id"], user_id=user_id,
                           izin_user=izin_user_penuh, dsn=test_dsn)
    assert aktif["status"] == "aktif"
    # Tabel milik modul dibuat dari manifes.
    tabel = daftar_tabel(modul["id"], user_id=user_id, dsn=test_dsn)
    assert len(tabel) == 1 and tabel[0]["nama"] == "tagihan"
    # Pratinjau dengan data contoh: angka ringkas + tabel + grafik.
    html = render_modul(MANIFES_TAGIHAN)
    assert "220.000" in html  # 132.500 + 87.500
    assert "PLN" in html and "<svg" in html


def test_modul_minimal_lolos_dan_aktif(make_user, test_dsn, izin_user_penuh):
    user_id, _ = make_user()
    modul = buat_modul(user_id=user_id, manifes=dict(MANIFES_MINIMAL), dsn=test_dsn)
    aktif = aktifkan_modul(modul["id"], user_id=user_id,
                           izin_user=izin_user_penuh, dsn=test_dsn)
    assert aktif["status"] == "aktif"


def test_manifes_nakal_ditolak():
    nakal = [
        # HTML/JS di dalam manifes.
        dict(MANIFES_MINIMAL, tampilan=[
            {"jenis": "kartu", "judul": "x",
             "teks": "<script>alert(1)</script>"}]),
        # Komponen tidak diizinkan.
        dict(MANIFES_MINIMAL, tampilan=[
            {"jenis": "video", "judul": "x"}]),
        # Tipe kolom tidak diizinkan.
        dict(MANIFES_MINIMAL, tabel=[
            {"nama": "belanja", "kolom": [{"nama": "barang", "tipe": "gambar"}]}]),
        # SQL di dalam manifes.
        dict(MANIFES_MINIMAL, tampilan=[
            {"jenis": "kartu", "judul": "x",
             "teks": "SELECT * FROM pengguna"}]),
        # 4 tampilan (maks 3).
        dict(MANIFES_MINIMAL, tampilan=[
            {"jenis": "kartu", "judul": f"k{i}", "teks": "x"} for i in range(4)]),
        # 6 tabel (maks 5).
        dict(MANIFES_MINIMAL, tabel=[
            {"nama": f"t{i}", "kolom": [{"nama": "a", "tipe": "teks"}]}
            for i in range(6)]),
    ]
    for m in nakal:
        hasil = jalankan_gerbang(m, dsn=None)
        assert hasil["lolos"] is False, m
        assert hasil["masalah"]


def test_buat_modul_nakal_menolak_dengan_alasan(make_user, test_dsn):
    user_id, _ = make_user()
    nakal = dict(MANIFES_MINIMAL, tampilan=[{"jenis": "video", "judul": "x"}])
    with pytest.raises(ModuleError, match="tidak diizinkan"):
        buat_modul(user_id=user_id, manifes=nakal, dsn=test_dsn)


def test_isolasi_modul_tidak_bisa_baca_data_modul_lain(make_user, test_dsn,
                                                      izin_user_penuh):
    user_a, _ = make_user()
    user_b, _ = make_user()
    modul_a = buat_modul(user_id=user_a, manifes=dict(MANIFES_MINIMAL), dsn=test_dsn)
    aktifkan_modul(modul_a["id"], user_id=user_a, izin_user=izin_user_penuh,
                   dsn=test_dsn)
    tabel_a = daftar_tabel(modul_a["id"], user_id=user_a, dsn=test_dsn)[0]
    tambah_baris(tabel_a["id"], user_id=user_a,
                 data={"barang": "Beras", "harga": 65000}, dsn=test_dsn)
    # Modul lain (milik user B) mencoba baca tabel modul A -> ditolak.
    modul_b = buat_modul(user_id=user_b, manifes=dict(MANIFES_MINIMAL), dsn=test_dsn)
    with pytest.raises(TableError, match="tidak bisa membaca data modul lain"):
        baca_tabel_modul_lain(tabel_a["id"], modul_id_peminta=modul_b["id"],
                              user_id=user_b, dsn=test_dsn)
    # Pengguna lain juga tidak bisa baca langsung.
    with pytest.raises(TableError, match="pengguna lain"):
        baca_tabel(tabel_a["id"], user_id=user_b, dsn=test_dsn)
    # Pemilik sendiri bisa baca.
    assert baca_tabel(tabel_a["id"], user_id=user_a, dsn=test_dsn) == [
        {"barang": "Beras", "harga": 65000}]


def test_tipe_kolom_ditegakkan_saat_tambah_baris(make_user, test_dsn):
    user_id, _ = make_user()
    modul = buat_modul(user_id=user_id, manifes=dict(MANIFES_MINIMAL), dsn=test_dsn)
    tabel = daftar_tabel(modul["id"], user_id=user_id, dsn=test_dsn)[0]
    with pytest.raises(TableError, match="harus angka"):
        tambah_baris(tabel["id"], user_id=user_id,
                     data={"barang": "Beras", "harga": "mahal"}, dsn=test_dsn)
    with pytest.raises(TableError, match="tidak boleh negatif"):
        tambah_baris(tabel["id"], user_id=user_id,
                     data={"barang": "Beras", "harga": -100}, dsn=test_dsn)
    ok = tambah_baris(tabel["id"], user_id=user_id,
                      data={"barang": "Beras", "harga": 65000}, dsn=test_dsn)
    assert ok["data"]["harga"] == 65000


def test_notif_modul_dari_data(make_user, test_dsn):
    user_id, _ = make_user()
    modul = buat_modul_tagihan(user_id=user_id, dsn=test_dsn)
    data = MANIFES_TAGIHAN["contoh_data"]
    notif = evaluasi_notif(MANIFES_TAGIHAN, data)
    assert len(notif) == 2  # dua tagihan belum_bayar
    assert "belum dibayar" in notif[0]["pesan"]
    lunas = {"tagihan": [dict(r, status="sudah_bayar")
                         for r in data["tagihan"]]}
    assert evaluasi_notif(MANIFES_TAGIHAN, lunas) == []


def test_ubah_modul_versi_baru_dan_rollback(make_user, test_dsn):
    user_id, _ = make_user()
    modul = buat_modul(user_id=user_id, manifes=dict(MANIFES_MINIMAL), dsn=test_dsn)
    baru = dict(MANIFES_MINIMAL)
    baru["tampilan"] = [
        {"jenis": "tabel", "judul": "Daftar belanja", "tabel": "belanja",
         "kolom": ["barang", "harga"]},
        {"jenis": "angka", "judul": "Total", "tabel": "belanja",
         "kolom": "harga", "format": "Rupiah"},
    ]
    diubah = ubah_modul(modul["id"], user_id=user_id, manifes=baru, dsn=test_dsn)
    assert diubah["versi_aktif"] == 2
    pulih = rollback_modul(modul["id"], user_id=user_id, versi=1, dsn=test_dsn)
    assert pulih["versi_aktif"] == 3
    assert len(pulih["manifes"]["tampilan"]) == 1


def test_hapus_modul_menawarkan_ekspor_dulu(make_user, test_dsn):
    user_id, _ = make_user()
    modul = buat_modul(user_id=user_id, manifes=dict(MANIFES_MINIMAL), dsn=test_dsn)
    tabel = daftar_tabel(modul["id"], user_id=user_id, dsn=test_dsn)[0]
    tambah_baris(tabel["id"], user_id=user_id,
                 data={"barang": "Beras", "harga": 65000}, dsn=test_dsn)
    hasil = hapus_modul(modul["id"], user_id=user_id, dsn=test_dsn)
    assert hasil["dihapus"]["status"] == "dihapus"
    assert hasil["ekspor"]["tabel"]["belanja"] == [
        {"barang": "Beras", "harga": 65000}]
    # Data baris ikut terhapus sesudah diekspor.
    assert daftar_tabel(modul["id"], user_id=user_id, dsn=test_dsn) == []


def test_maks_3_modul_aktif(make_user, test_dsn, izin_user_penuh):
    user_id, _ = make_user()
    for i in range(3):
        m = dict(MANIFES_MINIMAL, nama=f"Modul {i}")
        modul = buat_modul(user_id=user_id, manifes=m, dsn=test_dsn)
        aktifkan_modul(modul["id"], user_id=user_id, izin_user=izin_user_penuh,
                       dsn=test_dsn)
    keempat = buat_modul(user_id=user_id,
                         manifes=dict(MANIFES_MINIMAL, nama="Modul 4"), dsn=test_dsn)
    with pytest.raises(ModuleError, match="3 modul aktif"):
        aktifkan_modul(keempat["id"], user_id=user_id, izin_user=izin_user_penuh,
                       dsn=test_dsn)
