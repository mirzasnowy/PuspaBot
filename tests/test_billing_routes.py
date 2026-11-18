"""Tests: billing routes — one handler, every path, unknown paths
404, malformed payloads 400."""

import json

from puspa.billing.config import default_config
from puspa.billing.entitlements import issue_entitlement
from puspa.billing.ledger import MemoryUsageStore, UsageRow
from puspa.billing.providers import FakePaymentProvider, hmac_sha256_hex
from puspa.billing.routes import BillingDeps, handle_billing_request
from puspa.billing.webhook import MemoryIdempotencyStore


def _deps(**overrides):
    values = {
        "config": default_config(),
        "provider": FakePaymentProvider(),
        "usage_store": MemoryUsageStore(),
        "webhook_store": MemoryIdempotencyStore(),
        "webhook_secret": "hook-secret",
        "entitlement_secret": "ent-secret",
    }
    values.update(overrides)
    return BillingDeps(**values)


def test_catalog_lists_tiers_packs_and_model():
    response = handle_billing_request("GET", "/billing/catalog", {}, _deps())
    assert response["status"] == 200
    body = response["body"]
    assert body["currency"] == "IDR"
    assert body["billing_model"] == "subscription"
    assert [t["tier_id"] for t in body["tiers"]] == ["free", "plus", "pro", "team"]
    prices = {t["tier_id"]: t["price_idr_per_month"] for t in body["tiers"]}
    assert prices == {"free": 0, "plus": 99_000, "pro": 199_000, "team": 499_000}
    assert body["credit_packs"] == [
        {"pack_id": "pack-20", "credits": 20, "price_idr": 9_000},
        {"pack_id": "pack-100", "credits": 100, "price_idr": 35_000},
        {"pack_id": "pack-500", "credits": 500, "price_idr": 150_000},
    ]
    assert body["tiers"][1]["portal_url"].endswith("?plan=plus")


def test_quote_route():
    response = handle_billing_request(
        "POST",
        "/billing/quote",
        {"interval": "annual", "items": [{"kind": "tier", "ref": "pro"}]},
        _deps(),
    )
    assert response["status"] == 200
    assert response["body"]["total_idr"] == 199_000 * 10
    assert response["body"]["discount_idr"] == 199_000 * 2
    bad = handle_billing_request(
        "POST", "/billing/quote", {"items": [{"kind": "tier", "ref": "ultra"}]}, _deps()
    )
    assert bad["status"] == 400


def test_overview_route_with_usage_and_upsell():
    deps = _deps()
    deps.usage_store.append(UsageRow(user_id="user-1", bot_id="bot-a", cost_idr=500))
    response = handle_billing_request(
        "GET", "/billing/overview", {"user_id": "user-1", "tier_id": "plus", "bots": 3}, deps
    )
    assert response["status"] == 200
    body = response["body"]
    assert body["monthly_credit_allowance"] == 60
    assert body["usage_battery_7d"]["total_idr"] == 500
    assert body["upsell"]["due"] is True
    missing = handle_billing_request("GET", "/billing/overview", {}, deps)
    assert missing["status"] == 400


def test_invoices_route_lists_fake_invoices():
    deps = _deps()
    deps.provider.create_checkout(
        amount_idr=9_000, description="pack-20", idempotency_key="inv-key"
    )
    response = handle_billing_request("GET", "/billing/invoices", {"user_id": "user-1"}, deps)
    assert response["status"] == 200
    assert len(response["body"]["invoices"]) == 1
    assert response["body"]["invoices"][0]["amount_idr"] == 9_000


def test_license_issue_and_verify_routes():
    deps = _deps()
    issued = handle_billing_request(
        "POST",
        "/billing/license",
        {"user_id": "user-1", "machine_id": "m-1", "tier": "plus", "clouds": ["cloud-a"]},
        deps,
    )
    assert issued["status"] == 201
    entitlement = issued["body"]["entitlement"]
    assert entitlement["signature"]
    verified = handle_billing_request("GET", "/billing/license", {"entitlement": entitlement}, deps)
    assert verified["status"] == 200
    assert verified["body"]["verification"]["status"] == "active"
    bad = handle_billing_request(
        "POST",
        "/billing/license",
        {"user_id": "user-1", "machine_id": "m-1", "tier": "ultra"},
        deps,
    )
    assert bad["status"] == 400


def test_checkout_route():
    deps = _deps()
    response = handle_billing_request(
        "POST",
        "/billing/checkout",
        {"amount_idr": 35_000, "description": "pack-100", "idempotency_key": "route-key-1"},
        deps,
    )
    assert response["status"] == 201
    assert response["body"]["amount_idr"] == 35_000
    assert response["body"]["status"] == "completed"
    # Same key + same amount is idempotent through the provider.
    again = handle_billing_request(
        "POST",
        "/billing/checkout",
        {"amount_idr": 35_000, "description": "pack-100", "idempotency_key": "route-key-1"},
        deps,
    )
    assert again["body"]["checkout_id"] == response["body"]["checkout_id"]
    bad = handle_billing_request("POST", "/billing/checkout", {"amount_idr": 0}, deps)
    assert bad["status"] == 400


def test_webhook_route_verifies_signature():
    deps = _deps()
    event = {"event_id": "evt-route-1", "type": "invoice.paid"}
    payload = json.dumps(event).encode("utf-8")
    signature = "sha256=" + hmac_sha256_hex("hook-secret", payload)
    ok = handle_billing_request(
        "POST", "/billing/webhook", {"payload": payload, "signature": signature}, deps
    )
    assert ok["status"] == 200
    assert ok["body"]["status"] == "processed"
    bad = handle_billing_request(
        "POST", "/billing/webhook", {"payload": payload, "signature": "sha256=deadbeef"}, deps
    )
    assert bad["status"] == 401
    assert bad["body"]["status"] == "rejected"


def test_team_and_clouds_routes():
    deps = _deps()
    team = handle_billing_request("GET", "/billing/team", {}, deps)
    assert team["status"] == 200
    assert team["body"]["included_seats"] == 2
    assert team["body"]["extra_seat_idr_per_month"] == 99_000
    entitlement = issue_entitlement(
        user_id="user-1",
        machine_id="m-1",
        tier="team",
        secret="ent-secret",
        clouds=("cloud-a", "cloud-b"),
    )
    clouds = handle_billing_request(
        "GET", "/billing/clouds", {"entitlement": entitlement.to_dict()}, deps
    )
    assert clouds["status"] == 200
    assert clouds["body"]["clouds"] == ["cloud-a", "cloud-b"]
    assert clouds["body"]["max_clouds_per_account"] == 5


def test_ops_routes():
    deps = _deps()
    funnel = handle_billing_request(
        "POST", "/billing/ops/funnel", {"event": "checkout_viewed", "user_id": "user-1"}, deps
    )
    assert funnel["status"] == 201
    assert len(deps.ops_log) == 1
    grant = handle_billing_request(
        "POST",
        "/billing/ops/grant",
        {"user_id": "user-1", "credits": 20, "reason": "goodwill"},
        deps,
    )
    assert grant["status"] == 201
    assert deps.grants[0]["credits"] == 20
    review = handle_billing_request(
        "POST", "/billing/ops/review", {"decision": "approve", "subject": "grant-1"}, deps
    )
    assert review["status"] == 201
    bad_review = handle_billing_request("POST", "/billing/ops/review", {"decision": "maybe"}, deps)
    assert bad_review["status"] == 400


def test_unknown_route_404():
    response = handle_billing_request("GET", "/billing/nope", {}, _deps())
    assert response["status"] == 404
    assert response["body"]["error"] == "unknown_route"
    response = handle_billing_request("DELETE", "/billing/catalog", {}, _deps())
    assert response["status"] == 404
