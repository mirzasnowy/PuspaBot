"""Gateway rule-parser fuzzer (plan section 8.6).

Feeds the gateway rule parser (``puspa.policy.rules.parse_rule``)
and the policy loader (``puspa.policy.gateway.load_policy``) with
seeded random input — valid rules, malformed rules, and malformed
policy dicts — and checks the fail-closed invariants:

- ``parse_rule`` raises only ``PolicyError`` (a ``ValueError``)
  on malformed input, never any other exception;
- ``load_policy`` raises only ``PolicyError`` on a malformed
  policy dict;
- ``Gateway.decide`` never raises for a compiled policy, and an
  empty policy denies every action.

Any other exception is a fuzz failure and is reported with the
input that produced it. The fuzzer is seeded and deterministic:
the same seed produces the same inputs.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from puspa.policy.gateway import ActionContext, Gateway, load_policy
from puspa.policy.rules import PolicyError, RuleEvaluationError, evaluate_rule, parse_rule

_RULE_FRAGMENTS = [
    "true",
    "false",
    'tool.name == "pay"',
    "contains(tool.name)",
    'contains(tool.name, "pay")',
    'element.type == "submit"',
    'bot.id == "other-bot"',
    'page.host == "tokopedia.com"',
    'matches(page.host, "tokopedia")',
    'matches(page.host, "([")',
    "tool.name == 'send'",
    "not true",
    "true and false",
    "true or false",
    "(true)",
    "tool.name",
    "unknown.attr == 1",
    "tool.name == ",
    "((true)",
    "true and",
    "1 == 1",
    '"unterminated',
    "tool.name === 'pay'",
    "tool..name",
    "",
    " ",
    'tool.name == "pay" and element.type == "submit"',
    "not not true",
]

_MALFORMED_POLICIES: list[object] = [
    None,
    "not a dict",
    42,
    ["deny"],
    {"deny": "not a list"},
    {"allow": "not a list"},
    {"deny": [42]},
    {"deny": [None]},
    {"deny": ["tool.name == "]},
    {"deny": ['matches(page.host, "([")']},
    {"deny": [], "allow": [], "extra": []},
    {"deny": [], "unknown_section": ["true"]},
    {"deny": ["unknown.attr == 1"], "allow": ["true"]},
    {"deny": ["tool.name == 'pay'"], "allow": [123]},
]


@dataclass
class FuzzResult:
    iterations: int
    parse_failures: list[dict] = field(default_factory=list)
    load_failures: list[dict] = field(default_factory=list)
    decide_failures: list[dict] = field(default_factory=list)
    empty_policy_denies: bool = True

    @property
    def failures(self) -> int:
        return len(self.parse_failures) + len(self.load_failures) + len(self.decide_failures)


def random_rule(rng: random.Random) -> str:
    """A random rule string: a fragment, a splice, or noise."""
    choice = rng.random()
    if choice < 0.6:
        return rng.choice(_RULE_FRAGMENTS)
    if choice < 0.8:
        left = rng.choice(_RULE_FRAGMENTS)
        right = rng.choice(_RULE_FRAGMENTS)
        joiner = rng.choice([" and ", " or ", " == ", " ", "(", ")"])
        return left + joiner + right
    alphabet = "abcxyz01=()\"' .!"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))


def random_policy(rng: random.Random) -> object:
    """A random policy value: often a dict, sometimes malformed."""
    if rng.random() < 0.25:
        return rng.choice(_MALFORMED_POLICIES)
    policy: dict[str, list[str]] = {}
    if rng.random() < 0.8:
        policy["deny"] = [random_rule(rng) for _ in range(rng.randint(0, 3))]
    if rng.random() < 0.8:
        policy["allow"] = [random_rule(rng) for _ in range(rng.randint(0, 3))]
    if rng.random() < 0.1:
        policy["surprise"] = ["true"]
    return policy


def fuzz_gateway(*, seed: int = 20261120, iterations: int = 500) -> FuzzResult:
    """Run the seeded gateway fuzz and return every failure."""
    rng = random.Random(seed)
    result = FuzzResult(iterations=iterations)
    context = ActionContext(tool_name="pay", page_host="tokopedia.com", element_type="submit")
    empty = Gateway.start(None)
    empty_decision = empty.decide(context)
    result.empty_policy_denies = not empty_decision.allowed and empty_decision.outcome == "deny"
    for n in range(iterations):
        rule_text = random_rule(rng)
        try:
            parsed = parse_rule(rule_text)
            evaluate_rule(parsed, context.to_dict())
        except (PolicyError, RuleEvaluationError):
            # Designed errors: bad syntax at parse time, a missing
            # attribute at evaluation time.
            pass
        except Exception as exc:
            result.parse_failures.append(
                {"iteration": n, "input": rule_text, "error": f"{type(exc).__name__}: {exc}"}
            )
        policy_value = random_policy(rng)
        try:
            policy = load_policy(policy_value)  # type: ignore[arg-type]
        except PolicyError:
            continue
        except Exception as exc:
            result.load_failures.append(
                {
                    "iteration": n,
                    "input": repr(policy_value),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        try:
            gateway = Gateway(policy)
            gateway.decide(context)
            gateway.decide(ActionContext(tool_name="send_message"))
        except Exception as exc:
            result.decide_failures.append(
                {
                    "iteration": n,
                    "input": repr(policy_value),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return result
