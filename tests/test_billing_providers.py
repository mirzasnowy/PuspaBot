"""Tests: payment providers — fake provider, local-gateway stub,
product-key mapping from env."""

import pytest

from puspa.billing.providers import (
    FakePaymentProvider,
    KeyConflictError,
    LocalGatewayProvider,
    ProviderError,
    hmac_sha256_hex,
    product_key_from_env,
    provider_from_env,
    verify_hmac_signature,
)


def test_fake_checkout_deterministic_per_key():
    provider = FakePaymentProvider()
    first = provider.create_checkout(
        amount_idr=35_000, description="pack-100", idempotency_key="key-1"
    )
    second = provider.create_checkout(
        amount_idr=35_000, description="pack-100", idempotency_key="key-1"
    )
    assert first == second
    assert first.status == "completed"
    assert first.currency == "IDR"
    assert first.checkout_id.startswith("chk_fake_")


def test_fake_checkout_key_conflict_on_different_amount():
    provider = FakePaymentProvider()
    provider.create_checkout(amount_idr=35_000, description="pack-100", idempotency_key="key-2")
    with pytest.raises(KeyConflictError):
        provider.create_checkout(amount_idr=9_000, description="pack-20", idempotency_key="key-2")


def test_fake_checkout_requires_key():
    with pytest.raises(ProviderError):
        FakePaymentProvider().create_checkout(
            amount_idr=9_000, description="pack-20", idempotency_key=""
        )


def test_fake_invoice_consistent_with_checkout():
    provider = FakePaymentProvider()
    provider.create_checkout(amount_idr=9_000, description="pack-20", idempotency_key="key-3")
    invoices = provider.list_invoices(user_id="user-1")
    assert len(invoices) == 1
    invoice = provider.get_invoice(invoices[0].invoice_id)
    assert invoice.amount_idr == 9_000
    assert invoice.status == "paid"
    assert invoice.currency == "IDR"
    with pytest.raises(ProviderError):
        provider.get_invoice("inv_nope")


def test_fake_refund():
    provider = FakePaymentProvider()
    checkout = provider.create_checkout(
        amount_idr=35_000, description="pack-100", idempotency_key="key-4"
    )
    refund = provider.refund(checkout_id=checkout.checkout_id)
    assert refund["status"] == "refunded"
    assert refund["refunded_idr"] == 35_000
    with pytest.raises(ProviderError):
        provider.refund(checkout_id="chk_nope")


def test_local_gateway_stub_refuses_money_calls():
    provider = LocalGatewayProvider(gateway_name="example-gateway")
    with pytest.raises(NotImplementedError, match="not wired"):
        provider.create_checkout(amount_idr=9_000, description="pack-20", idempotency_key="k")
    with pytest.raises(NotImplementedError, match="not wired"):
        provider.get_invoice("inv-1")
    with pytest.raises(NotImplementedError, match="not wired"):
        provider.list_invoices(user_id="u1")
    with pytest.raises(NotImplementedError, match="not wired"):
        provider.refund(checkout_id="chk-1")


def test_local_gateway_stub_still_verifies_signatures():
    provider = LocalGatewayProvider()
    secret = "webhook-secret"
    payload = b'{"type": "invoice.paid"}'
    signature = "sha256=" + hmac_sha256_hex(secret, payload)
    assert (
        provider.verify_webhook_signature(payload=payload, signature=signature, secret=secret)
        is True
    )
    assert (
        provider.verify_webhook_signature(
            payload=payload, signature="sha256=deadbeef", secret=secret
        )
        is False
    )


def test_hmac_signature_formats():
    secret = "s3cret"
    payload = b"hello"
    raw = hmac_sha256_hex(secret, payload)
    assert verify_hmac_signature(payload=payload, signature=raw, secret=secret)
    assert verify_hmac_signature(payload=payload, signature="sha256=" + raw, secret=secret)
    assert not verify_hmac_signature(payload=payload, signature="", secret=secret)
    assert not verify_hmac_signature(payload=payload, signature=raw, secret="")
    assert not verify_hmac_signature(payload=b"other", signature=raw, secret=secret)


def test_product_key_from_env():
    env = {"PUSPA_BILLING_PRODUCT_KEY_PACK_100": "prod_abc123"}
    assert product_key_from_env("pack-100", env=env) == "prod_abc123"
    assert product_key_from_env("PACK-100", env=env) == "prod_abc123"
    with pytest.raises(ProviderError, match="not configured"):
        product_key_from_env("pack-500", env=env)


def test_provider_from_env():
    assert isinstance(provider_from_env(env={}), FakePaymentProvider)
    assert isinstance(
        provider_from_env(env={"PUSPA_BILLING_PROVIDER": "fake"}), FakePaymentProvider
    )
    assert isinstance(
        provider_from_env(env={"PUSPA_BILLING_PROVIDER": "local-gateway"}), LocalGatewayProvider
    )
    with pytest.raises(ProviderError):
        provider_from_env(env={"PUSPA_BILLING_PROVIDER": "dodo"})
