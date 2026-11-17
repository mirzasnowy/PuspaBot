"""Tes watchlist + sampler (§3.8) dan cache bukti bersama (§3.8)."""

import psycopg
import pytest

from puspa.graphs.identity import tambah_produk
from puspa.pricing import cache as cache_bukti
from puspa.pricing import watchlist


def _produk(make_user, test_dsn, nomor_model="SM-S921B"):
    user = make_user()
    produk = tambah_produk(merek="Samsung", nomor_model=nomor_model,
                           kategori="HP", dsn=test_dsn)
    return user, produk


def test_watchlist_tambah_dan_daftar(make_user, test_dsn):
    user, produk = _produk(make_user, test_dsn)
    hasil = watchlist.tambah_watchlist(user_id=user["user_id"], produk_id=produk["id"],
                                       ambang_harga=12000000, dsn=test_dsn)
    assert hasil["aktif"] is True
    daftar = watchlist.daftar_watchlist(user["user_id"], dsn=test_dsn)
    assert len(daftar) == 1 and daftar[0]["ambang_harga"] == 12000000.0


def test_sampler_menghormati_anggaran_20_per_hari(make_user, test_dsn):
    user, produk = _produk(make_user, test_dsn)
    watchlist.tambah_watchlist(user_id=user["user_id"], produk_id=produk["id"], dsn=test_dsn)
    assert watchlist.sisa_anggaran_baca(user["user_id"], dsn=test_dsn) == 20
    hasil = watchlist.jalankan_sampler(
        user_id=user["user_id"], agen_id=user["agen_id"],
        baca_harga=lambda b: {"harga": 12499000, "bukti_id": "bukti-x",
                              "tautan_id": "t-x"},
        dsn=test_dsn)
    assert hasil["dibaca"] == [produk["id"]]
    assert hasil["sisa_anggaran"] == 19
    # Habiskan anggaran -> sampler melewati barang, bukan membaca diam-diam.
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE anggaran_baca SET jumlah = 20 WHERE user_id = %s",
                (user["user_id"],))
        conn.commit()
    hasil = watchlist.jalankan_sampler(
        user_id=user["user_id"], agen_id=user["agen_id"],
        baca_harga=lambda b: {"harga": 1, "bukti_id": "b", "tautan_id": "t"},
        dsn=test_dsn)
    assert hasil["dibaca"] == []
    assert hasil["dilewati"][0]["alasan"] == "anggaran_baca_harian_habis"


def test_sampler_alert_harga_turun_di_bawah_ambang(make_user, test_dsn):
    user, produk = _produk(make_user, test_dsn)
    watchlist.tambah_watchlist(user_id=user["user_id"], produk_id=produk["id"],
                               ambang_harga=13000000, dsn=test_dsn)
    hasil = watchlist.jalankan_sampler(
        user_id=user["user_id"], agen_id=user["agen_id"],
        baca_harga=lambda b: {"harga": 12499000, "bukti_id": "bukti-x",
                              "tautan_id": "t-x"},
        dsn=test_dsn)
    assert len(hasil["alert"]) == 1
    assert hasil["alert"][0]["harga"] == 12499000


def test_cache_bukti_ttl_30_menit_dan_statistik(test_dsn):
    cache_bukti.simpan("Tokopedia:https://x", sumber="Tokopedia",
                       nilai={"harga": 1000}, bukti_id="bukti-1", dsn=test_dsn)
    assert cache_bukti.ambil("Tokopedia:https://x", dsn=test_dsn)["bukti_id"] == "bukti-1"
    assert cache_bukti.ambil("Tokopedia:https://tidak-ada", dsn=test_dsn) is None
    # Kedaluwarsa -> None (pemanggil wajib baca ulang sumber).
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE cache_bukti SET kedaluwarsa = now() - INTERVAL '1 hour'"
                " WHERE kunci = 'Tokopedia:https://x'")
        conn.commit()
    assert cache_bukti.ambil("Tokopedia:https://x", dsn=test_dsn) is None
    statistik = cache_bukti.statistik_hit(dsn=test_dsn)
    assert statistik["total_hit"] == 1
    assert statistik["entri_cache"] == 1
