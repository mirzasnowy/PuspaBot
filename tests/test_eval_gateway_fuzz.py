"""Tests for the gateway rule-parser fuzzer."""

import random

from puspa.eval.fuzz import fuzz_gateway, random_policy, random_rule


def test_fuzz_gateway_finds_no_unexpected_exception():
    result = fuzz_gateway(seed=20261120, iterations=500)
    assert result.failures == 0
    assert result.parse_failures == []
    assert result.load_failures == []
    assert result.decide_failures == []
    assert result.empty_policy_denies


def test_fuzz_gateway_is_deterministic_for_a_seed():
    first = fuzz_gateway(seed=42, iterations=200)
    second = fuzz_gateway(seed=42, iterations=200)
    assert first.failures == second.failures
    assert first.empty_policy_denies == second.empty_policy_denies


def test_random_rule_and_policy_generators_produce_inputs():
    rng = random.Random(7)
    rules = {random_rule(rng) for _ in range(50)}
    assert len(rules) > 10
    policies = [random_policy(rng) for _ in range(50)]
    assert any(isinstance(p, dict) for p in policies)
    assert any(not isinstance(p, dict) for p in policies)
