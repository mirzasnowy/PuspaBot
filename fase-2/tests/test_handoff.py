"""Tes kontrak handoff + mesin status + detak (§2.4)."""

from datetime import datetime, timedelta, timezone

import pytest

from puspa.runtime.handoff import (Handoff, HandoffContract, HandoffError,
                                   cek_batas_handoff_antar_agen,
                                   tolak_lingkaran)


def _kontrak(**kw):
    dasar = {"dari_id": "agen-a", "ke_id": "agen-b", "tujuan": "cari harga",
             "masukan": {"jenis": "cari_harga", "barang": "laptop"},
             "keluaran_diharapkan": {"jenis": "harga"},
             "definisi_selesai": "harga + toko + tautan ditemukan"}
    dasar.update(kw)
    return HandoffContract(**dasar)


def test_kontrak_valid_dan_field_wajib():
    k = _kontrak()
    assert k.batas_waktu_detik == 45
    assert k.kunci_idempotensi.startswith("handoff-")


def test_kontrak_tanpa_skema_ditolak():
    with pytest.raises(HandoffError):
        _kontrak(masukan={"barang": "laptop"})  # tanpa 'jenis'
    with pytest.raises(HandoffError):
        _kontrak(keluaran_diharapkan={"nilai": 1})
    with pytest.raises(HandoffError):
        _kontrak(kelas_izin="Raja")
    with pytest.raises(HandoffError):
        _kontrak(dari_id="agen-a", ke_id="agen-a")
    with pytest.raises(HandoffError):
        _kontrak(definisi_selesai="  ")


def test_mesin_status_lengkap():
    h = Handoff(_kontrak())
    assert h.status == "diajukan"
    h.terima()
    h.mulai()
    assert h.serahkan({"jenis": "harga", "nilai": 12500000}) == "diserahkan"
    h.nyatakan_lolos()
    assert h.status == "lolos"


def test_keluaran_tidak_lolos_skema_ditolak():
    h = Handoff(_kontrak())
    h.terima()
    h.mulai()
    assert h.serahkan({"jenis": "bukan_harga"}) == "ditolak"
    assert h.status == "ditolak"


def test_transisi_tidak_sah_ditolak():
    h = Handoff(_kontrak())
    with pytest.raises(HandoffError):
        h.mulai()  # belum diterima
    with pytest.raises(HandoffError):
        h.serahkan({"jenis": "harga"})


def test_satu_percobaan_ulang_lalu_lapor():
    h = Handoff(_kontrak())
    h.terima()
    h.mulai()
    h.tolak("sumber tidak bisa dibaca")
    assert h.coba_ulang() is True
    assert h.status == "diajukan" and h.percobaan == 2
    h.tolak("gagal lagi")
    assert h.coba_ulang() is False  # habis: agen melapor ke user


def test_macet_tanpa_detak_30_detik():
    h = Handoff(_kontrak())
    h.terima()
    h.mulai()
    sekarang = h.dibuat + timedelta(seconds=31)
    assert h.macet(sekarang) is True
    h.detak(h.dibuat + timedelta(seconds=20))
    assert h.macet(h.dibuat + timedelta(seconds=40)) is False
    assert h.macet(h.dibuat + timedelta(seconds=51)) is True


def test_macet_lewat_batas_waktu():
    h = Handoff(_kontrak(batas_waktu_detik=45))
    h.terima()
    h.mulai()
    # Detak terus, tapi tetap lewat batas waktu kontrak.
    h.detak(h.dibuat + timedelta(seconds=40))
    assert h.macet(h.dibuat + timedelta(seconds=46)) is True


def test_lingkaran_ditolak_dan_maks_tiga_handoff():
    tolak_lingkaran(["agen-a", "agen-b"], "agen-b", "agen-c")  # boleh
    with pytest.raises(HandoffError, match="melingkar"):
        tolak_lingkaran(["agen-a", "agen-b"], "agen-b", "agen-a")
    cek_batas_handoff_antar_agen(2)
    with pytest.raises(HandoffError):
        cek_batas_handoff_antar_agen(3)
