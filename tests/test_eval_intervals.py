"""Tests for the uncertainty intervals (Wilson + rule of three)."""

import math

import pytest

from puspa.eval.intervals import (
    format_interval,
    rule_of_three_upper_bound,
    wilson_interval,
)


def test_wilson_interval_is_inside_zero_one():
    low, high = wilson_interval(30, 30)
    assert 0.0 <= low <= high <= 1.0
    assert high == 1.0
    low, high = wilson_interval(0, 60)
    assert low == 0.0
    assert high > 0.0


def test_wilson_interval_narrows_with_more_trials():
    _, high_few = wilson_interval(0, 10)
    _, high_many = wilson_interval(0, 200)
    assert high_many < high_few


def test_wilson_interval_known_value():
    # 0/200: Wilson upper bound is close to the rule-of-three 3/200.
    _, high = wilson_interval(0, 200)
    assert math.isclose(high, 0.0185, abs_tol=0.002)


def test_wilson_interval_rejects_bad_counts():
    with pytest.raises(ValueError):
        wilson_interval(5, 3)
    with pytest.raises(ValueError):
        wilson_interval(-1, 10)


def test_wilson_interval_no_trials_is_uninformative():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_rule_of_three():
    assert rule_of_three_upper_bound(200) == pytest.approx(0.015)
    assert rule_of_three_upper_bound(60) == pytest.approx(0.05)
    assert rule_of_three_upper_bound(0) == 1.0


def test_format_interval():
    text = format_interval(0, 200)
    assert text.startswith("0/200")
    assert "95% Wilson" in text
