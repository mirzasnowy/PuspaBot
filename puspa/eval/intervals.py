"""Uncertainty intervals for eval numbers.

Every reported number carries an interval. Two tools:

- the Wilson score interval for a proportion (success rate, recall,
  attack success rate) at 95% confidence;
- the rule of three: observing 0 successes in N trials puts the
  95% upper bound at 3/N. So "0 of 200" means "at most about 1.5%",
  never "safe".
"""

from __future__ import annotations

import math

#: z for a two-sided 95% interval.
Z_95 = 1.96


def wilson_interval(successes: int, trials: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for ``successes`` out of ``trials``.

    Returns ``(low, high)`` as proportions in [0, 1]. With zero
    trials the interval is the whole range: nothing was measured.
    """
    if trials <= 0:
        return (0.0, 1.0)
    if successes < 0 or successes > trials:
        raise ValueError("successes must be between 0 and trials")
    n = float(trials)
    p = successes / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    low = (center - margin) / denom
    high = (center + margin) / denom
    return (max(0.0, low), min(1.0, high))


def rule_of_three_upper_bound(trials: int) -> float:
    """95% upper bound on a rate after 0 successes in ``trials``.

    3/N. Zero trials means nothing was measured, so the bound is
    1.0 (no information), not 0.0.
    """
    if trials <= 0:
        return 1.0
    return min(1.0, 3.0 / trials)


def format_interval(successes: int, trials: int) -> str:
    """Human-readable ``"k/n (low-high, 95% Wilson)"`` summary."""
    low, high = wilson_interval(successes, trials)
    return f"{successes}/{trials} ({low:.3f}-{high:.3f}, 95% Wilson)"
