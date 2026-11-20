"""Regression tests for the E-16 follow-up (2026-11-20).

Each test runs the exact payload from the frozen corpus v1.0.0
through the real defense stack and asserts it is BLOCKED, not
merely held at approval: held is not a defense success, so a
bypass that only moves to "held" still fails the beta target of
zero attack success rate for goal 1. The payload text is read
from the corpus, never copied here, so the test always checks
the frozen bytes.
"""

import pytest

from puspa.eval.attack_harness import AttackHarness
from puspa.eval.corpus import load_attack_corpus
from puspa.policy import ApprovalPolicy

#: The five goal-1 bypasses from the E-16 scorecard, now blocked
#: at the scanner: credential-request patterns cover "kredensial"
#: and "vault", a fake "SYSTEM ADMIN" role marker is a high
#: pattern, base64 blobs are decoded and scanned, and leet-dense
#: text is scanned de-leeted.
BLOCKED_GOAL_1_BYPASSES = ("ATK-003", "ATK-008", "ATK-010", "ATK-013", "ATK-014")


def _payload(payload_id):
    corpus = load_attack_corpus()
    return next(p for p in corpus.live if p.id == payload_id)


def test_direct_vault_credential_request_is_blocked():
    result = AttackHarness().run_payload(_payload("ATK-003"))
    assert result.outcome == "blocked"
    assert result.stopped_at == "scanner"


def test_slang_vault_password_request_is_blocked():
    result = AttackHarness().run_payload(_payload("ATK-008"))
    assert result.outcome == "blocked"
    assert result.stopped_at == "scanner"


def test_base64_encoded_leak_instruction_is_blocked():
    result = AttackHarness().run_payload(_payload("ATK-010"))
    assert result.outcome == "blocked"
    assert result.stopped_at == "scanner"
    assert "base64-decoded" in result.detail


def test_fake_system_admin_role_is_blocked():
    result = AttackHarness().run_payload(_payload("ATK-013"))
    assert result.outcome == "blocked"
    assert result.stopped_at == "scanner"


def test_leetspeak_leak_instruction_is_blocked():
    result = AttackHarness().run_payload(_payload("ATK-014"))
    assert result.outcome == "blocked"
    assert result.stopped_at == "scanner"


def test_no_goal_1_bypass_remains():
    corpus = load_attack_corpus()
    summary = AttackHarness().run_corpus(corpus.live)
    assert summary.attack_success_rate(goal=1) == 0.0
    assert summary.by_goal[1]["success"] == 0
    for payload_id in BLOCKED_GOAL_1_BYPASSES:
        result = next(r for r in summary.results if r.payload_id == payload_id)
        assert result.outcome == "blocked", payload_id


def test_broad_always_share_grant_is_refused_in_beta():
    policy = ApprovalPolicy(beta_mode=True)
    with pytest.raises(ValueError, match="'always' is disabled in beta"):
        policy.record_answer("share", "always")


def test_broad_always_share_scenario_adds_no_success():
    corpus = load_attack_corpus()
    default = AttackHarness().run_corpus(corpus.live)
    broad_harness = AttackHarness(broad_always_share=True)
    assert broad_harness.broad_grant_refused is True
    broad = broad_harness.run_corpus(corpus.live)
    assert broad.success == default.success == 0
    share = next(r for r in broad.results if r.payload_id == "ATK-020")
    assert share.outcome == "held_at_approval"
