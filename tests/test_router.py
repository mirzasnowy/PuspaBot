"""Tes router model bertingkat (§2.4) — pemilihan di atas factory BYOK Fase 0."""

import pytest

from puspa.router.router import choose, compute_cost


def test_reader_uses_cheapest_model():
    p = choose("pembaca")
    assert p.rp_per_1k_input + p.rp_per_1k_output == min(
        b[3] + b[4] for b in __import__("puspa.router.router", fromlist=["KATALOG"]).CATALOG
    )


def test_planner_uses_mid_tier_model_passed_by_reference():
    from puspa.router.router import CATALOG

    p = choose("perencana")
    rows = next(b for b in CATALOG if b[1] == p.model)
    assert rows[6] is True  # lolos 10 tugas acuan
    assert choose("penyaji").model == p.model


def test_verifier_family_differs_from_maker():
    p = choose("pemeriksa", creator_family="deepseek")
    assert p.family != "deepseek"
    p2 = choose("pemeriksa", creator_family="qwen")
    assert p2.family != "qwen"


def test_cheapest_vision_model_and_cost_counted():
    p = choose("vision")
    assert p is not None and p.vision is True
    assert compute_cost(p, 1000, 500) == p.rp_per_1k_input + p.rp_per_1k_output // 2


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        choose("raja")
