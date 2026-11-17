"""Tes wedge tagihan PPOB (§4.2 revisi 7) di atas situs uji lokal.

Nomor pelanggan Nero (keputusan 5) belum ada — semua uji memakai
data uji + situs uji lokal (fixture). TIDAK ADA angka tagihan nyata
yang dikarang: nilai yang diuji adalah nilai fixture yang diketahui.
"""

import httpx
import pytest

from puspa.billing import billing
from puspa.billing.fixture import DATA_UJI, SitusUji


def _baca_dari_fixture(situs):
    def _baca(url):
        provider = url.split("fixture://")[1].split("/")[0]
        return httpx.get(situs.url(provider), timeout=5).text

    return _baca


def _baca_ulang_dari_fixture(situs):
    def _ulang(url):
        # Pemeriksa membaca ulang situs yang sama, independen (tanpa
        # menerima halaman dari Pembaca — hanya klaim + URL).
        provider = url.split("fixture://")[1].split("/")[0] if "fixture://" in url else "pln"
        teks = httpx.get(situs.url(provider), timeout=5).text
        import re

        jumlah = float(re.search(r"Rp\s*([\d.]+)", teks).group(1).replace(".", ""))
        return {"nilai": jumlah, "nama": f"tagihan {provider}"}

    return _ulang


def test_cek_tagihan_pln_fixture(make_user, test_dsn):
    user = make_user()
    with SitusUji() as situs:
        hasil = billing.cek_tagihan(
            user_id=user["user_id"], agen_id=user["agen_id"], provider="PLN",
            nomor_pelanggan="123456789012", baca_situs=_baca_dari_fixture(situs),
            baca_ulang=_baca_ulang_dari_fixture(situs),
            tugas_id="tugas-pln-1", mode="fixture", dsn=test_dsn)
    assert hasil["sukses"] is True
    assert hasil["jumlah"] == DATA_UJI["pln"]["jumlah"]
    assert hasil["jatuh_tempo"] == DATA_UJI["pln"]["jatuh_tempo"]
    assert hasil["status"] == "belum_bayar"
    assert hasil["vonis"] == "Terverifikasi"
    assert hasil["bukti_id"]
    # Prosedur PLN lahir otomatis sebagai percobaan.
    assert hasil["prosedur"]["layanan"] == "PLN"


def test_cek_tagihan_keempat_provider(make_user, test_dsn):
    user = make_user()
    with SitusUji() as situs:
        for provider, nomor in (("PLN", "123456789012"), ("PDAM", "987654321"),
                                ("pulsa", "081234567890"), ("paket data", "081234567890")):
            hasil = billing.cek_tagihan(
                user_id=user["user_id"], agen_id=user["agen_id"], provider=provider,
                nomor_pelanggan=nomor, baca_situs=_baca_dari_fixture(situs),
                baca_ulang=_baca_ulang_dari_fixture(situs),
                tugas_id=f"tugas-{provider}", mode="fixture", dsn=test_dsn)
            assert hasil["sukses"] is True, provider
            assert hasil["jumlah"] == DATA_UJI[provider.lower()]["jumlah"], provider


def test_bpjs_ditolak_tegas(make_user, test_dsn):
    user = make_user()
    hasil = billing.cek_tagihan(
        user_id=user["user_id"], agen_id=user["agen_id"], provider="BPJS",
        nomor_pelanggan="0001234567890", baca_situs=lambda u: "",
        baca_ulang=lambda u: {}, tugas_id="tugas-bpjs", mode="fixture", dsn=test_dsn)
    assert hasil["sukses"] is False and hasil["alasan"] == "provider_di_luar_mvp"


def test_prosedur_rusak_tidak_dijalankan(make_user, test_dsn):
    user = make_user()
    with SitusUji() as situs:
        def _gagal(url):
            raise RuntimeError("situs down")

        for i in range(2):
            hasil = billing.cek_tagihan(
                user_id=user["user_id"], agen_id=user["agen_id"], provider="PLN",
                nomor_pelanggan="123456789012", baca_situs=_gagal,
                baca_ulang=lambda u: {}, tugas_id=f"tugas-gagal-{i}",
                mode="fixture", dsn=test_dsn)
            assert hasil["sukses"] is False
        # Prosedur kini rusak -> cek berikutnya menolak tanpa membaca.
        dipanggil = {"n": 0}

        def _hitung(url):
            dipanggil["n"] += 1
            return httpx.get(situs.url("pln"), timeout=5).text

        hasil = billing.cek_tagihan(
            user_id=user["user_id"], agen_id=user["agen_id"], provider="PLN",
            nomor_pelanggan="123456789012", baca_situs=_hitung,
            baca_ulang=_baca_ulang_dari_fixture(situs),
            tugas_id="tugas-sesudah-rusak", mode="fixture", dsn=test_dsn)
        assert hasil["sukses"] is False and hasil["alasan"] == "prosedur_rusak"
        assert dipanggil["n"] == 0


def test_pengingat_jatuh_tempo():
    from datetime import date

    assert billing.pengingat_jatuh_tempo(jatuh_tempo="2026-11-20", hari_sebelum=3,
                                         hari_ini=date(2026, 11, 18)) == {
        "perlu_ingatkan": True, "hari_lagi": 2}
    assert billing.pengingat_jatuh_tempo(jatuh_tempo="2026-11-20", hari_sebelum=3,
                                         hari_ini=date(2026, 11, 10))["perlu_ingatkan"] is False
    assert billing.pengingat_jatuh_tempo(jatuh_tempo=None)["perlu_ingatkan"] is False


def test_siapkan_bayar_hanya_draf_tanpa_eksekusi():
    draf = billing.siapkan_bayar(provider="PLN", jumlah=132500,
                                 jatuh_tempo="2026-11-20",
                                 nomor_pelanggan_masked="1234******12")
    assert draf["mode"] == "siapkan_bayar"
    assert draf["draf"]["jumlah"] == 132500.0
    assert len(draf["isi_hash"]) == 64
    assert "tidak memegang" in draf["pesan"]
