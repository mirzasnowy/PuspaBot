"""Marketplace learn-from-chat + post-reply review (G3, DuskBot pattern).

In-memory: run with ``pytest tests/test_marketplace_learning.py
--noconftest -q``. No live LLM call is made anywhere here; the LLM
path is exercised with injected stubs only.
"""

import time

from puspa.marketplace import (
    MAX_CHANGES_PER_REVIEW,
    MAX_CONSECUTIVE_FAILURES,
    REVIEW_TIMEOUT_SECONDS,
    LearningStep,
    learn_from_turn,
    review_after_reply,
)


def test_limits_are_the_documented_values():
    assert MAX_CHANGES_PER_REVIEW == 3
    assert MAX_CONSECUTIVE_FAILURES == 3
    assert REVIEW_TIMEOUT_SECONDS == 180


def test_learn_extracts_correction_patterns():
    messages = [
        {"role": "user", "content": "skill price-compare should sort by total price"},
        {"role": "assistant", "content": "Done."},
    ]
    changes = learn_from_turn(messages, known_skill_ids={"price-compare"})
    assert len(changes) == 1
    assert changes[0]["skill_id"] == "price-compare"
    assert "sort by total price" in changes[0]["note"]
    assert changes[0]["source"] == "chat"


def test_learn_ignores_unknown_skills_and_vague_chat():
    messages = [
        {"role": "user", "content": "hello, how are you?"},
        {"role": "user", "content": "skill mystery-skill should be faster"},
    ]
    changes = learn_from_turn(messages, known_skill_ids={"price-compare"})
    assert changes == []


def test_learn_caps_at_three_changes():
    messages = [
        {"role": "user", "content": f"skill price-compare should do thing {i}"} for i in range(5)
    ]
    changes = learn_from_turn(messages, known_skill_ids={"price-compare"})
    assert len(changes) == 3


def test_learn_llm_proposes_changes_as_json():
    def fake_complete(prompt):
        return '{"changes": [{"skill_id": "price-compare", "note": "add a per-unit price column"}]}'

    messages = [{"role": "user", "content": "the table is confusing"}]
    changes = learn_from_turn(
        messages,
        known_skill_ids={"price-compare"},
        llm_complete=fake_complete,
    )
    assert changes == [
        {
            "skill_id": "price-compare",
            "note": "add a per-unit price column",
            "source": "llm",
        }
    ]


def test_learn_llm_failure_falls_back_to_deterministic():
    def broken_complete(prompt):
        raise RuntimeError("no key")

    messages = [
        {"role": "user", "content": "skill price-compare should sort by total price"},
    ]
    changes = learn_from_turn(
        messages,
        known_skill_ids={"price-compare"},
        llm_complete=broken_complete,
    )
    assert len(changes) == 1
    assert changes[0]["source"] == "chat"


def test_review_applies_at_most_three_changes_sync():
    applied = []

    def apply_change(change):
        applied.append(change)
        return True

    messages = [
        {"role": "user", "content": f"skill price-compare should do thing {i}"} for i in range(5)
    ]
    outcome = review_after_reply(
        messages, apply_change=apply_change, known_skill_ids={"price-compare"}, sync=True
    )
    assert outcome.status == "completed"
    assert outcome.changes_proposed == 3
    assert outcome.changes_applied == 3
    assert len(applied) == 3


def test_review_with_no_changes_is_skipped():
    outcome = review_after_reply(
        [{"role": "user", "content": "hello"}],
        apply_change=lambda change: True,
        sync=True,
    )
    assert outcome.status == "skipped"
    assert outcome.changes_applied == 0


def test_three_consecutive_failures_discard_the_step():
    step = LearningStep(skill_id="price-compare")

    def reject(change):
        return False

    messages = [
        {"role": "user", "content": "skill price-compare should sort by total price"},
    ]
    for _ in range(2):
        outcome = review_after_reply(
            messages,
            apply_change=reject,
            step=step,
            known_skill_ids={"price-compare"},
            sync=True,
        )
        assert outcome.status == "failed"
    outcome = review_after_reply(
        messages,
        apply_change=reject,
        step=step,
        known_skill_ids={"price-compare"},
        sync=True,
    )
    assert outcome.status == "discarded"
    assert step.discarded is True
    # A discarded step runs nothing further.
    outcome = review_after_reply(
        messages,
        apply_change=lambda change: True,
        step=step,
        known_skill_ids={"price-compare"},
        sync=True,
    )
    assert outcome.status == "discarded"
    assert outcome.changes_applied == 0


def test_success_resets_the_failure_count():
    step = LearningStep(skill_id="price-compare")
    messages = [
        {"role": "user", "content": "skill price-compare should sort by total price"},
    ]
    review_after_reply(
        messages,
        apply_change=lambda c: False,
        step=step,
        known_skill_ids={"price-compare"},
        sync=True,
    )
    review_after_reply(
        messages,
        apply_change=lambda c: False,
        step=step,
        known_skill_ids={"price-compare"},
        sync=True,
    )
    assert step.consecutive_failures == 2
    outcome = review_after_reply(
        messages,
        apply_change=lambda c: True,
        step=step,
        known_skill_ids={"price-compare"},
        sync=True,
    )
    assert outcome.status == "completed"
    assert step.consecutive_failures == 0
    assert step.discarded is False


def test_review_async_handle_waits_for_result():
    applied = []

    def apply_change(change):
        time.sleep(0.05)
        applied.append(change)
        return True

    messages = [
        {"role": "user", "content": "skill price-compare should sort by total price"},
    ]
    handle = review_after_reply(
        messages,
        apply_change=apply_change,
        known_skill_ids={"price-compare"},
    )
    outcome = handle.wait(timeout=10)
    assert outcome.status == "completed"
    assert outcome.changes_applied == 1
    assert handle.done is True
