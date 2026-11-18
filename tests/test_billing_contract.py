"""Tests: billing contract v1 — quote, line items, annual discount,
dedicated pricing from spec, additive-only validation."""

import pytest

from puspa.billing.config import BillingConfig
from puspa.billing.contract import (
    Cart,
    CartItem,
    dedicated_price_idr,
    quote,
    validate_additive,
)


def test_quote_plus_monthly_single_line():
    result = quote(Cart(items=(CartItem(kind="tier", ref="plus"),)))
    assert result.total_idr == 99_000
    assert result.subtotal_idr == 99_000
    assert result.discount_idr == 0
    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.kind == "tier" and line.ref == "plus"
    assert line.unit_price_idr == 99_000
    assert line.line_total_idr == 99_000
    assert result.currency == "IDR"
    assert result.contract_version == 1


def test_quote_annual_is_two_months_free():
    monthly = quote(Cart(items=(CartItem(kind="tier", ref="plus"),)))
    annual = quote(Cart(interval="annual", items=(CartItem(kind="tier", ref="plus"),)))
    assert annual.subtotal_idr == 99_000 * 12
    assert annual.discount_idr == 99_000 * 2
    assert annual.total_idr == 99_000 * 10
    assert annual.total_idr == monthly.total_idr * 10


def test_quote_credit_pack_is_one_off_not_discounted():
    annual = quote(
        Cart(
            interval="annual",
            items=(CartItem(kind="tier", ref="plus"), CartItem(kind="credit_pack", ref="pack-100")),
        )
    )
    assert annual.subtotal_idr == 99_000 * 12 + 35_000
    assert annual.discount_idr == 99_000 * 2
    assert annual.total_idr == 99_000 * 10 + 35_000
    # 60 monthly allowance credits + 100 pack credits.
    assert annual.credits_granted == 160


def test_quote_team_plus_extra_seats():
    result = quote(
        Cart(
            items=(
                CartItem(kind="tier", ref="team"),
                CartItem(kind="extra_seat", ref="extra_seat", quantity=2),
            )
        )
    )
    assert result.total_idr == 499_000 + 2 * 99_000
    assert result.lines[1].kind == "extra_seat"
    assert result.lines[1].line_total_idr == 198_000


def test_dedicated_price_from_spec():
    # base 50_000 + 2*15_000 vCPU + 4*5_000 RAM + 1 chunk (20 GB) * 10_000.
    assert dedicated_price_idr(vcpu=2, ram_gb=4, disk_gb=20) == 110_000
    # 60 GB disk is 3 chunks; 61 GB rounds up to 4 chunks.
    assert dedicated_price_idr(vcpu=4, ram_gb=8, disk_gb=60) == (50_000 + 60_000 + 40_000 + 30_000)
    assert dedicated_price_idr(vcpu=0, ram_gb=0, disk_gb=0) == 50_000
    assert dedicated_price_idr(vcpu=1, ram_gb=1, disk_gb=61) == (50_000 + 15_000 + 5_000 + 40_000)
    with pytest.raises(ValueError):
        dedicated_price_idr(vcpu=-1, ram_gb=0, disk_gb=0)


def test_quote_dedicated_line_uses_spec_price():
    result = quote(
        Cart(items=(CartItem(kind="dedicated", ref="dedicated", vcpu=2, ram_gb=4, disk_gb=20),))
    )
    assert result.total_idr == 110_000
    assert result.lines[0].detail == {"vcpu": 2, "ram_gb": 4, "disk_gb": 20}


def test_quote_unknown_tier_or_pack_rejected():
    with pytest.raises(KeyError):
        quote(Cart(items=(CartItem(kind="tier", ref="ultra"),)))
    with pytest.raises(KeyError):
        quote(Cart(items=(CartItem(kind="credit_pack", ref="pack-999"),)))


def test_quote_bad_cart_rejected():
    with pytest.raises(ValueError):
        Cart(interval="weekly")
    with pytest.raises(ValueError):
        Cart(items=(CartItem(kind="tier", ref="plus", quantity=0),))
    with pytest.raises(ValueError):
        Cart(items=(CartItem(kind="mystery", ref="plus"),))


def test_validate_additive_accepts_v1_and_extra_fields():
    result = quote(Cart(items=(CartItem(kind="tier", ref="free"),)))
    doc = result.to_dict()
    validate_additive(doc)
    doc["new_future_field"] = "allowed: additive only"
    validate_additive(doc)


def test_validate_additive_rejects_missing_or_retyped_field():
    result = quote(Cart(items=(CartItem(kind="tier", ref="free"),)))
    doc = result.to_dict()
    del doc["total_idr"]
    with pytest.raises(ValueError, match="total_idr"):
        validate_additive(doc)
    doc = result.to_dict()
    doc["total_idr"] = "99000"
    with pytest.raises(ValueError, match="total_idr"):
        validate_additive(doc)
    doc = result.to_dict()
    doc["total_idr"] = True
    with pytest.raises(ValueError, match="total_idr"):
        validate_additive(doc)


def test_credits_model_grants_no_monthly_allowance():
    cfg = BillingConfig(billing_model="credits")
    result = quote(Cart(items=(CartItem(kind="tier", ref="plus"),)), config=cfg)
    assert result.credits_granted == 0
    assert result.billing_model == "credits"
    with pytest.raises(ValueError):
        BillingConfig(billing_model="barter")
