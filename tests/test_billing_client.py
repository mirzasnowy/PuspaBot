"""Tests: billing client state machine — render table, refusal
codes, idempotency on key conflict, settlement polling with a fake
clock (no real 5-minute sleeps), charge preview, upgrade matrix,
resumable step-up, portal deep links."""

from puspa.billing.client import (
    BillingClient,
    StepUpFlow,
    evaluate_upgrade,
    fetch_billing_state,
    poll_settlement,
    portal_link,
    preview_charge_effect,
    refusal_for,
    render_state,
)
from puspa.billing.providers import FakePaymentProvider


class FakeClock:
    """A clock + sleep that never really waits."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_render_table_covers_every_state():
    from puspa.billing.client import CLIENT_STATES

    for state in CLIENT_STATES:
        rendered = render_state(state)
        assert rendered["title"]
        assert rendered["body"]
        assert isinstance(rendered["actions"], list)
    # Fail-closed fetch failure never renders an empty state.
    unknown = render_state("no_such_state")
    assert unknown["title"]
    assert "contact_support" in unknown["actions"]


def test_fetch_failure_is_fail_closed():
    def boom():
        raise RuntimeError("network down")

    result = fetch_billing_state(boom)
    assert result["state"] == "failed"
    assert result["overlay_open"] is False
    assert result["assumed"] is None
    assert "network down" in result["error"]

    malformed = fetch_billing_state(lambda: ["not", "a", "dict"])
    assert malformed["overlay_open"] is False

    ok = fetch_billing_state(lambda: {"state": "ready", "tier_id": "plus"})
    assert ok["overlay_open"] is True
    assert ok["state"] == "ready"
    assert ok["tier_id"] == "plus"


def test_refusal_codes_have_copy_recovery_and_state():
    cases = {
        "insufficient_scope": "needs_scope_stepup",
        "session_revoked": "session_revoked",
        "role_required": "upgrade_blocked",
        "consent_required": "needs_consent",
        "monthly_cap_exceeded": "upgrade_blocked",
        "no_payment_method": "needs_payment_method",
        "upgrade_cap_exceeded": "upgrade_blocked",
        "stripe_unavailable": "failed",
        "payment_failed": "failed",
    }
    for code, state in cases.items():
        refusal = refusal_for(code)
        assert refusal["message"]
        assert refusal["recovery"]
        assert refusal["action"]
        assert refusal["state"] == state, code


def test_unknown_refusal_code_shows_server_message():
    refusal = refusal_for("brand_new_code", server_message="The gateway said no.")
    assert refusal["message"] == "The gateway said no."
    assert refusal["state"] == "failed"
    assert refusal["action"] == "contact_support"
    without_message = refusal_for("brand_new_code")
    assert "brand_new_code" in without_message["message"]


def test_idempotency_key_conflict_starts_new_charge():
    provider = FakePaymentProvider()
    # Pre-use a key for a different amount so the first attempt conflicts.
    provider.create_checkout(amount_idr=1, description="other", idempotency_key="conflict-key")
    client = BillingClient(provider=provider)
    started = client.start_checkout(
        amount_idr=35_000, description="pack-100", idempotency_key="conflict-key"
    )
    assert started["checkout"].amount_idr == 35_000
    assert started["attempts"] == 2
    assert started["idempotency_key"] != "conflict-key"


def test_poll_settlement_settles_without_real_sleep():
    clock = FakeClock()
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        return "pending" if calls["n"] < 3 else "settled"

    result = poll_settlement(
        check, interval_s=2.0, timeout_s=300.0, sleep=clock.sleep, monotonic=clock.monotonic
    )
    assert result == {"outcome": "settled", "attempts": 3, "framing": "confirmed"}
    assert clock.now == 1004.0  # only two fake 2s sleeps


def test_poll_timeout_is_timeout_not_failure():
    clock = FakeClock()
    result = poll_settlement(
        lambda: "pending",
        interval_s=2.0,
        timeout_s=5.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert result["outcome"] == "timeout"
    assert result["framing"] == "check_balance_first"
    assert result["attempts"] == 4  # t=0,2,4 then t=6 >= deadline


def test_poll_failed_is_failure():
    clock = FakeClock()
    result = poll_settlement(
        lambda: {"status": "failed"},
        interval_s=2.0,
        timeout_s=300.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert result["outcome"] == "failed"
    assert result["attempts"] == 1


def test_preview_charge_effects():
    assert preview_charge_effect(amount_idr=35_000, balance_idr=100_000)["effect"] == "charge_now"
    preview = preview_charge_effect(amount_idr=35_000, balance_idr=100_000)
    assert preview["balance_after_idr"] == 65_000
    assert preview_charge_effect(amount_idr=0, balance_idr=100_000)["effect"] == "no_op"
    assert (
        preview_charge_effect(amount_idr=35_000, balance_idr=100_000, scheduled=True)["effect"]
        == "scheduled"
    )
    blocked = preview_charge_effect(
        amount_idr=35_000, balance_idr=100_000, blocked_reason="monthly_cap_exceeded"
    )
    assert blocked["effect"] == "blocked"
    assert blocked["reason"] == "monthly_cap_exceeded"


def test_upgrade_matrix_checks_reason_before_status():
    # Blocking reason wins even when status says ok.
    blocked = evaluate_upgrade(
        current_tier="free", target_tier="plus", reason="monthly_cap_exceeded", status="ok"
    )
    assert blocked["allowed"] is False
    assert blocked["reason"] == "monthly_cap_exceeded"
    # Non-blocking status still blocks.
    bad_status = evaluate_upgrade(
        current_tier="free", target_tier="plus", reason=None, status="payment_method_missing"
    )
    assert bad_status["allowed"] is False
    assert bad_status["reason"] == "payment_method_missing"
    # Clean reason + ok status allows; free target is a no-op.
    allowed = evaluate_upgrade(current_tier="free", target_tier="plus", reason=None, status="ok")
    assert allowed == {
        "allowed": True,
        "effect": "charge_now",
        "reason": None,
        "current_tier": "free",
        "target_tier": "plus",
    }
    free = evaluate_upgrade(current_tier="plus", target_tier="free", reason=None, status="ok")
    assert free["effect"] == "no_op"
    unknown = evaluate_upgrade(current_tier="free", target_tier="ultra", reason=None, status="ok")
    assert unknown["allowed"] is False
    assert unknown["reason"] == "unknown_tier"


def test_step_up_flow_resumes_where_it_left_off():
    flow = StepUpFlow(action="buy_pack_100", payload={"credits": 100})
    resumed = flow.resume(scope_granted=True)
    assert resumed == {
        "resumed": True,
        "state": "resumed",
        "action": "buy_pack_100",
        "payload": {"credits": 100},
    }
    flow2 = StepUpFlow(action="buy_pack_100")
    denied = flow2.resume(scope_granted=False)
    assert denied["resumed"] is False
    assert denied["state"] == "cancelled"


def test_portal_link_has_stable_plan_param():
    assert portal_link("plus") == "https://portal.puspa.example/billing?plan=plus"
    assert portal_link("team").endswith("?plan=team")


def test_full_charge_flow_with_fake_clock():
    clock = FakeClock()
    provider = FakePaymentProvider()
    client = BillingClient(provider=provider)
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        return "settled" if calls["n"] >= 2 else "pending"

    result = client.charge(
        amount_idr=35_000,
        description="pack-100",
        balance_idr=100_000,
        check_status=check,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert result["preview"]["effect"] == "charge_now"
    assert result["checkout"].status == "completed"
    assert result["poll"]["outcome"] == "settled"
    assert clock.now == 1002.0


def test_charge_zero_amount_is_no_op_without_provider_call():
    clock = FakeClock()
    provider = FakePaymentProvider()
    client = BillingClient(provider=provider)
    result = client.charge(
        amount_idr=0,
        description="free",
        balance_idr=0,
        check_status=lambda: "settled",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert result["preview"]["effect"] == "no_op"
    assert result["checkout"] is None
    assert result["poll"] is None
    assert provider.list_invoices(user_id="u") == []
