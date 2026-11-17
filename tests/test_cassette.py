"""Tes uji kaset (§2.11) + 10 kaset acuan."""

import json
from pathlib import Path

import pytest

from puspa.cassette.cassette import (CassetteError, CassettePlayer,
                                     TUGAS_ACUAN, hapus_kaset_tugas,
                                     muat_kaset, rekam_dari_ledger,
                                     simpan_kaset, suntik_harga_salah)

KASET_DIR = Path(__file__).resolve().parent.parent / "cassettes"


def test_sepuluh_kaset_acuan_ada():
    assert len(TUGAS_ACUAN) == 10
    for nama in TUGAS_ACUAN:
        path = KASET_DIR / f"{nama}.json"
        assert path.exists(), f"kaset acuan hilang: {nama}"
        data = muat_kaset(path)
        assert data["rekaman"], f"kaset {nama} kosong"
        assert all("tool" in r and "keluaran" in r for r in data["rekaman"])


def test_rekam_dari_ledger_dan_putar_ulang_identik(tmp_path):
    kejadian = [
        {"jenis": "tool_selesai", "tugas_id": "t1",
         "isi": {"tool": "baca_harga", "keluaran": {"jenis": "harga", "nilai": 12500000}}},
        {"jenis": "tool_selesai", "tugas_id": "t1",
         "isi": {"tool": "baca_harga", "keluaran": {"jenis": "harga", "nilai": 11999000}}},
        {"jenis": "pesan_agen", "tugas_id": "t1", "isi": {"teks": "bukan tool"}},
    ]
    kaset = rekam_dari_ledger(kejadian, "t1")
    assert len(kaset["rekaman"]) == 2
    berkas = simpan_kaset(kaset, "uji-putar", kaset_dir=tmp_path)
    player = CassettePlayer(muat_kaset(berkas))
    assert player.putar("baca_harga") == {"jenis": "harga", "nilai": 12500000}
    assert player.putar("baca_harga") == {"jenis": "harga", "nilai": 11999000}
    with pytest.raises(CassetteError):
        player.putar("baca_harga")  # kaset habis: jujur, bukan karangan


def test_suntik_harga_salah_ditangkap_pemeriksa(tmp_path):
    kaset = {"tugas_id": "t1", "rekaman": [
        {"tool": "baca_harga", "keluaran": {"jenis": "harga", "nilai": 12500000}}]}
    disuntik = suntik_harga_salah(kaset, tool="baca_harga", nilai_baru=1000)
    assert kaset["rekaman"][0]["keluaran"]["nilai"] == 12500000  # asli tidak diubah
    player = CassettePlayer(disuntik)
    assert player.putar("baca_harga")["nilai"] == 1000
    # Pemeriksa membandingkan dengan baca ulang yang benar.
    from puspa.evidence.evidence import catat_bukti
    from puspa.evidence.verifier import periksa

    b = catat_bukti(tugas_id="t1", klaim="Laptop X", nilai=1000, satuan="Rp",
                    sumber_url="https://tokopedia.com/produk/laptop-x",
                    potongan_teks="Laptop X Rp1.000", id_pembaca="sub-1")
    periksa(b, baca_ulang=lambda url: {"nilai": 12500000, "nama": "Laptop X"})
    assert b.vonis != "Terverifikasi"


def test_kaset_ikut_terhapus_saat_tugas_dihapus(tmp_path):
    kaset = {"tugas_id": "t-hapus", "rekaman": [{"tool": "a", "keluaran": 1}]}
    simpan_kaset(kaset, "kaset-hapus", kaset_dir=tmp_path)
    assert (tmp_path / "kaset-hapus.json").exists()
    hasil = hapus_kaset_tugas("t-hapus", kaset_dir=tmp_path)
    assert hasil["dihapus"] == ["kaset-hapus"]
    assert not (tmp_path / "kaset-hapus.json").exists()


def test_putar_ulang_kaset_acuan_angka_identik_9_dari_10():
    """Gerbang 12 (parsial): 10 kaset acuan diputar ulang, angka hasil
    putar ulang identik dengan aslinya di minimal 9 dari 10."""
    identik = 0
    for nama in TUGAS_ACUAN:
        data = muat_kaset(KASET_DIR / f"{nama}.json")
        player = CassettePlayer(data)
        nilai_awal = []
        for rekaman in data["rekaman"]:
            keluaran = player.putar(rekaman["tool"])
            if isinstance(keluaran, dict) and "nilai" in keluaran:
                nilai_awal.append(keluaran["nilai"])
        nilai_asli = [r["keluaran"]["nilai"] for r in data["rekaman"]
                      if isinstance(r["keluaran"], dict) and "nilai" in r["keluaran"]]
        if nilai_awal == nilai_asli:
            identik += 1
    assert identik >= 9
