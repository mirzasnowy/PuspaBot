"""Tests for the frozen attack corpus and the benign set."""

import json
from pathlib import Path

import pytest

from puspa.eval.corpus import (
    TOTAL_RECORDS,
    load_attack_corpus,
    load_benign_corpus,
)


def test_corpus_has_200_records_with_declared_slices():
    corpus = load_attack_corpus()
    assert len(corpus.records) == TOTAL_RECORDS
    assert len(corpus.live) == 60
    assert len(corpus.slots) == 140
    assert corpus.slice_counts() == {
        "internal_handwritten": 60,
        "adaptive_attacker": 60,
        "public_corpus": 80,
    }


def test_live_payloads_cover_all_goals_channels_and_techniques():
    corpus = load_attack_corpus()
    goals = {r.goal for r in corpus.live}
    assert goals == {1, 2, 3, 4, 5, 6, 7}
    assert len({r.goal for r in corpus.live if r.goal in (1, 2)}) == 2
    assert sum(1 for r in corpus.live if r.goal == 1) == 15
    assert sum(1 for r in corpus.live if r.goal == 2) == 15
    channels = {r.channel for r in corpus.live}
    assert len(channels) == 10
    techniques = {r.technique for r in corpus.live}
    assert "base64" in techniques
    assert "homoglyph" in techniques
    assert "zero_width" in techniques
    assert "fraud_apk" in techniques
    assert "fraud_bank_ojk" in techniques
    assert "fraud_wrong_transfer" in techniques
    assert "fraud_like_task" in techniques
    assert "fraud_investment_loan" in techniques


def test_empty_slots_carry_source_and_license_not_text():
    corpus = load_attack_corpus()
    for record in corpus.slots:
        assert record.text is None
        assert record.status == "empty_slot"
    adaptive = [r for r in corpus.slots if r.origin == "adaptive_attacker"]
    assert len(adaptive) == 60
    public = [r for r in corpus.slots if r.origin == "public_corpus"]
    assert len(public) == 80
    assert {r.source for r in public} == {"garak probes", "AgentDojo"}
    assert {r.license for r in public} == {"Apache-2.0", "MIT"}


def test_live_records_are_not_attributed_to_outsiders():
    corpus = load_attack_corpus()
    assert all(r.origin == "internal_handwritten" for r in corpus.live)
    assert "outside the team" in corpus.manifest["author_note"]


def test_corpus_hash_catches_silent_edits(tmp_path):
    source = Path("puspa/eval/data/attack_corpus_v1.json")
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw["live_records"][0]["text"] += " "
    edited = tmp_path / "attack_corpus_v1.json"
    edited.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_attack_corpus(edited)


def test_benign_set_loads_with_tricky_subset():
    messages = load_benign_corpus()
    assert len(messages) == 40
    assert sum(1 for m in messages if m.tricky) == 10
    assert all(m.text for m in messages)
