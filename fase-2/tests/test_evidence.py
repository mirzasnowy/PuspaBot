"""Tes Pemeriksa + Buku Bukti + renderer angka yatim (§2.5) + proyeksi (§2.3)."""

from datetime import datetime, timedelta, timezone

import pytest

from puspa.evidence.evidence import (catat_bukti, ringkasan_vonis,
                                      skor_keandalan, EvidenceError)
from puspa.evidence.projections import (audit, biaya, daftar_bukti, kantor,
                                        thread_chat)
from puspa.evidence.renderer import cari_angka_yatim, render, sajikan
from puspa.evidence.verifier import periksa, periksa_suntikan


def _bukti(nilai=12500000, url="https://tokopedia.com/produk/laptop-x"):
    return catat_bukti(tugas_id="tugas-1", klaim="Laptop X di Tokopedia",
                       nilai=nilai, satuan="Rp", sumber_url=url,
                       potongan_teks="Laptop X Rp12.500.000 toko resmi",
                       halaman="<html>Laptop X Rp12.500.000</html>",
                       id_pembaca="sub-1")


def _baca_ulang_benar(url):
    return {"nilai": 12500000, "nama": "Laptop X di Tokopedia",
            "waktu_ambil": datetime.now(timezone.utc)}


def test_catat_bukti_validasi():
    b = _bukti()
    assert b.bukti_id.startswith("bukti-")
    assert len(b.potongan_teks) <= 200
    assert b.vonis == "Belum pasti"
    with pytest.raises(EvidenceError):
        catat_bukti(tugas_id="t", klaim="x", nilai="bukan angka", satuan="Rp",
                    sumber_url="https://tokopedia.com/x", potongan_teks="x",
                    id_pembaca="s")
    with pytest.raises(EvidenceError):
        catat_bukti(tugas_id="t", klaim="x", nilai=100, satuan="Rp",
                    sumber_url="https://situs-asing.example/x", potongan_teks="x",
                    id_pembaca="s")


def test_pemeriksa_terverifikasi_saat_nilai_sama():
    b = _bukti()
    periksa(b, baca_ulang=_baca_ulang_benar)
    assert b.vonis == "Terverifikasi"


def test_pemeriksa_tangkap_harga_salah_disuntik_3_dari_3():
    for nilai_salah in (9999999, 15000000, 100000):
        b = _bukti()
        vonis = periksa_suntikan(b, nilai_salah=nilai_salah, baca_ulang=_baca_ulang_benar)
        assert vonis != "Terverifikasi", nilai_salah


def test_pemeriksa_tidak_bisa_dipastikan_saat_sumber_tidak_bisa_dibaca():
    b = _bukti()
    periksa(b, baca_ulang=lambda url: (_ for _ in ()).throw(RuntimeError("timeout")))
    assert b.vonis == "Tidak bisa dipastikan"


def test_pemeriksa_belum_pasti_saat_nilai_beda_tapi_sumber_berubah():
    b = _bukti()
    def baca_baru(url):
        return {"nilai": 12900000, "nama": "Laptop X di Tokopedia",
                "waktu_ambil": datetime.now(timezone.utc) + timedelta(minutes=5)}
    periksa(b, baca_ulang=baca_baru)
    assert b.vonis == "Belum pasti"


def test_renderer_isi_penanda_dari_bukti():
    b = _bukti()
    b.vonis = "Terverifikasi"
    teks, hilang = render("Harga laptop {harga:" + b.bukti_id + "} di Tokopedia.",
                         {b.bukti_id: b})
    assert teks == "Harga laptop Rp12.500.000 di Tokopedia."
    assert hilang == []


def test_angka_yatim_ditahan_lalu_dibuang():
    # 3 dari 3 angka yatim yang disuntikkan harus ditahan/dibuang.
    for teks_yatim in ("Harganya Rp9.999.000.", "Tagihan Rp132.500 jatuh tempo 20 Nov.",
                       "Selisihnya 15% lebih murah."):
        hasil = sajikan(teks_yatim, {})
        assert hasil["ditahan"] is True
        assert hasil["dibuang"]
        assert "tidak bisa memastikan angkanya" in hasil["teks"]
        assert cari_angka_yatim(hasil["teks"]) == []


def test_angka_yatim_diperbaiki_satu_kali():
    b = _bukti()
    b.vonis = "Terverifikasi"
    def perbaiki(teks):
        return teks.replace("Rp9.999.000", "{harga:" + b.bukti_id + "}")
    hasil = sajikan("Harganya Rp9.999.000.", {b.bukti_id: b}, perbaiki=perbaiki)
    assert hasil["ditahan"] is True
    assert hasil["dibuang"] == []
    assert "Rp12.500.000" in hasil["teks"]


def test_skor_keandalan_dan_turunkan_vonis():
    skor = skor_keandalan(["Terverifikasi"] * 46 + ["Belum pasti"] * 4)
    assert skor["persen_terverifikasi"] == 92.0
    assert skor["turunkan_vonis"] is False
    rendah = skor_keandalan(["Terverifikasi"] * 30 + ["Belum pasti"] * 20)
    assert rendah["turunkan_vonis"] is True
    assert skor_keandalan([])["persen_terverifikasi"] is None


def test_ringkasan_vonis_lencana_kantor():
    b1, b2 = _bukti(), _bukti()
    b1.vonis, b2.vonis = "Terverifikasi", "Belum pasti"
    assert ringkasan_vonis([b1, b2]) == "1 terverifikasi, 1 belum pasti"


def test_proyeksi_dari_ledger():
    kejadian = [
        {"jenis": "pesan_user", "tugas_id": "t1", "agen_id": "puspa",
         "isi": {"dari": "user", "teks": "halo"}, "waktu": "2026-11-17"},
        {"jenis": "tugas_dibuat", "tugas_id": "t1", "agen_id": "puspa",
         "isi": {"biaya_rp": 500}, "waktu": "2026-11-17"},
        {"jenis": "handoff_diajukan", "tugas_id": "t1", "agen_id": "puspa",
         "isi": {"dari": "puspa", "ke": "sub-1", "tujuan": "cari"}, "waktu": "2026-11-17"},
        {"jenis": "bukti_dicatat", "tugas_id": "t1", "agen_id": "puspa",
         "isi": {"bukti_id": "bukti-1", "klaim": "harga", "nilai": 100}, "waktu": "2026-11-17"},
        {"jenis": "pemeriksaan_selesai", "tugas_id": "t1", "agen_id": "puspa",
         "isi": {"bukti_id": "bukti-1", "vonis": "Terverifikasi"}, "waktu": "2026-11-17"},
        {"jenis": "tugas_selesai", "tugas_id": "t1", "agen_id": "puspa",
         "isi": {"biaya_rp": 300}, "waktu": "2026-11-17"},
    ]
    assert len(thread_chat(kejadian)) == 3
    assert kantor(kejadian)["agen"][0]["status"] == "idle"
    assert kantor(kejadian)["agen"][0]["biaya_rp"] == 800
    assert daftar_bukti(kejadian)[0]["vonis"] == "Terverifikasi"
    assert biaya(kejadian)["total_rp"] == 800
    assert audit(kejadian) == []
