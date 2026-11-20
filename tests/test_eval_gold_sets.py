"""Tests for the gold sets (entity identity, quoted numbers, orphans)."""

from puspa.eval.gold_sets import (
    evaluate_all_gold_sets,
    evaluate_entity_identity,
    evaluate_orphan_numbers,
    evaluate_quoted_numbers,
    load_entity_gold,
    load_orphan_gold,
    load_quoted_gold,
)


def test_gold_sets_have_thirty_cases_each():
    _, entity_cases = load_entity_gold()
    assert len(entity_cases) == 30
    assert len(load_quoted_gold()) == 30
    assert len(load_orphan_gold()) == 30


def test_entity_identity_precision_and_recall():
    result = evaluate_entity_identity()
    assert result["cases"] == 30
    assert result["true_positives"] == 24
    assert result["false_positives"] == 5
    assert result["false_negatives"] == 0
    assert result["precision"] == 24 / 29
    assert result["recall"] == 1.0
    # The five false positives are the concept traps: a query for
    # a model that does not exist matches the nearest series.
    wrong = {c["id"] for c in result["per_case"] if not c["correct"]}
    assert wrong == {"ENT-020", "ENT-021", "ENT-022", "ENT-023", "ENT-024"}


def test_quoted_number_accuracy():
    result = evaluate_quoted_numbers()
    assert result["cases"] == 30
    assert result["found"] == 30
    assert result["total"] == 35
    assert result["accuracy"] == 30 / 35
    # The five misses are the deliberately broken cases.
    missed = {c["id"] for c in result["per_case"] if c["found"] < len(c["expected"])}
    assert missed == {"QTE-025", "QTE-026", "QTE-027", "QTE-028", "QTE-029"}


def test_orphan_number_hold_and_wrong_hold_rates():
    result = evaluate_orphan_numbers()
    assert result["cases"] == 30
    assert result["orphan_cases"] == 20
    assert result["clean_cases"] == 10
    assert result["hold_rate"] == 1.0
    assert result["wrong_hold_rate"] == 0.0
    assert result["label_mismatches"] == []


def test_all_gold_sets_evaluate_together():
    result = evaluate_all_gold_sets()
    assert set(result) == {"entity_identity", "quoted_numbers", "orphan_numbers"}
