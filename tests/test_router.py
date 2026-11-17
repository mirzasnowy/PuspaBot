"""Tes router model bertingkat (§2.4) — pemilihan di atas factory BYOK Fase 0."""

import pytest

from puspa.router.router import hitung_cost, pilih


def test_pembaca_use_model_termurah():
    p = pilih("pembaca")
    assert p.rp_per_1k_input + p.rp_per_1k_output == min(
        b[3] + b[4] for b in
        __import__("puspa.router.router", fromlist=["KATALOG"]).KATALOG)


def test_perencana_use_model_menengah_passed_reference():
    from puspa.router.router import KATALOG

    p = pilih("perencana")
    rows = next(b for b in KATALOG if b[1] == p.model)
    assert rows[6] is True  # lolos 10 tugas acuan
    assert pilih("penyaji").model == p.model


def test_pemeriksa_family_berbeda_dari_pembuat():
    p = pilih("pemeriksa", family_pembuat="deepseek")
    assert p.family != "deepseek"
    p2 = pilih("pemeriksa", family_pembuat="qwen")
    assert p2.family != "qwen"


def test_vision_termurah_and_hitung_cost():
    p = pilih("vision")
    assert p is not None and p.vision is True
    assert hitung_cost(p, 1000, 500) == p.rp_per_1k_input + p.rp_per_1k_output // 2


def test_peran_not_dikenal_rejected():
    with pytest.raises(ValueError):
        pilih("raja")
