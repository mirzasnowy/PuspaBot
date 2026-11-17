"""Tes router model bertingkat (§2.4) — pemilihan di atas factory BYOK Fase 0."""

import pytest

from puspa.router.router import hitung_biaya, pilih


def test_pembaca_pakai_model_termurah():
    p = pilih("pembaca")
    assert p.rp_per_1k_input + p.rp_per_1k_output == min(
        b[3] + b[4] for b in
        __import__("puspa.router.router", fromlist=["KATALOG"]).KATALOG)


def test_perencana_pakai_model_menengah_lolos_acuan():
    from puspa.router.router import KATALOG

    p = pilih("perencana")
    baris = next(b for b in KATALOG if b[1] == p.model)
    assert baris[6] is True  # lolos 10 tugas acuan
    assert pilih("penyaji").model == p.model


def test_pemeriksa_keluarga_berbeda_dari_pembuat():
    p = pilih("pemeriksa", keluarga_pembuat="deepseek")
    assert p.keluarga != "deepseek"
    p2 = pilih("pemeriksa", keluarga_pembuat="qwen")
    assert p2.keluarga != "qwen"


def test_vision_termurah_dan_hitung_biaya():
    p = pilih("vision")
    assert p is not None and p.vision is True
    assert hitung_biaya(p, 1000, 500) == p.rp_per_1k_input + p.rp_per_1k_output // 2


def test_peran_tidak_dikenal_ditolak():
    with pytest.raises(ValueError):
        pilih("raja")
