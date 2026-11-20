"""Tests for the release scorecard assembly and rendering."""

from puspa.eval.scorecard import build_scorecard, render_scorecard_markdown


def _inputs():
    return {
        "date": "2026-11-20",
        "reference_tasks": {
            "successes": 30,
            "trials": 30,
            "total_seconds": 1.0,
            "total_cost_rupiah": 0.0,
        },
        "retrieval": {
            "bm25": {
                "recall_at_5": 0.84,
                "answer_accuracy": 0.84,
                "hits": 42,
                "answered": 42,
                "questions": 50,
            },
            "vectors": {
                "recall_at_5": 0.86,
                "answer_accuracy": 0.9,
                "hits": 43,
                "answered": 45,
                "questions": 50,
            },
            "hipporag": {
                "recall_at_5": 0.74,
                "answer_accuracy": 0.74,
                "hits": 37,
                "answered": 37,
                "questions": 50,
            },
        },
        "attack": {
            "live": 60,
            "empty_slots": 140,
            "blocked": 30,
            "held_at_approval": 25,
            "success": 5,
            "by_goal": {"1": {"blocked": 10, "held_at_approval": 0, "success": 5, "no_action": 0}},
        },
        "benign": {"total": 40, "flagged": 7, "false_positive_rate": 0.175},
        "utility": {"total": 40, "completed": 40, "utility_rate": 1.0},
        "gold": {
            "entity_identity": {"precision": 0.83, "recall": 1.0},
            "quoted_numbers": {"accuracy": 0.86, "found": 30, "total": 35},
            "orphan_numbers": {"hold_rate": 1.0, "wrong_hold_rate": 0.0},
        },
        "mutation": {"measured": False, "note": "not run"},
    }


def test_scorecard_carries_intervals_and_versions():
    card = build_scorecard(_inputs())
    assert card["corpus_version"] == "1.0.0"
    assert card["date"] == "2026-11-20"
    assert card["reference_tasks"]["summary"].startswith("30/30")
    assert card["attack"]["rule_of_three_upper_bound_95"] == 3 / 60
    assert card["attack"]["attack_success_rate"] == 5 / 60
    assert card["mutation"]["measured"] is False
    assert card["not_measured"]
    assert card["honest_limits"]


def test_scorecard_build_is_deterministic():
    assert build_scorecard(_inputs()) == build_scorecard(_inputs())


def test_scorecard_markdown_names_the_honest_limits():
    text = render_scorecard_markdown(build_scorecard(_inputs()))
    assert "# Release scorecard" in text
    assert "rule-of-three" in text.lower() or "Rule-of-three" in text
    assert "Not measured" in text
    assert "Honest limits" in text
    assert "held at approval" in text


def test_scorecard_without_measurements_reports_not_measured():
    card = build_scorecard({})
    assert card["reference_tasks"]["trials"] == 0
    assert card["attack"]["live_payloads"] == 0
    assert card["mutation"]["measured"] is False
