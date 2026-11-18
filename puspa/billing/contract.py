"""Billing contract v1: Cart -> Quote, priced purely from config.

The contract is additive only: new fields may be added in later
versions, but existing fields are never renamed, removed, or retyped.
``validate_additive`` enforces that a stored v1 document still reads
under a newer reader: every v1 field must still be present with the
same type.

Prices come from exactly one source, ``BillingConfig`` (see
``config.py``). The same config prices the quote, the invoice, and the
entitlement allowance, so the three can never disagree.

Money is integer Rupiah (IDR). Annual billing is 2 months free:
a 12-month term is charged as 10 months.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import (
    CONTRACT_VERSION,
    TEAM_EXTRA_SEAT_IDR_PER_MONTH,
    BillingConfig,
    default_config,
)

#: Interval values a cart may carry.
INTERVALS = ("monthly", "annual")

#: Cart item kinds.
CART_KINDS = ("tier", "credit_pack", "dedicated", "extra_seat")


@dataclass(frozen=True)
class CartItem:
    """One thing the customer wants to buy."""

    kind: str  # "tier" | "credit_pack" | "dedicated" | "extra_seat"
    ref: str  # tier_id, pack_id, or "dedicated" / "extra_seat"
    quantity: int = 1
    # Dedicated-machine spec; only used when kind == "dedicated".
    vcpu: int = 0
    ram_gb: int = 0
    disk_gb: int = 0


@dataclass(frozen=True)
class Cart:
    """What the customer wants, before pricing."""

    interval: str = "monthly"  # "monthly" | "annual"
    items: tuple[CartItem, ...] = ()

    def __post_init__(self) -> None:
        if self.interval not in INTERVALS:
            raise ValueError(f"unknown interval {self.interval!r}")
        for item in self.items:
            if item.kind not in CART_KINDS:
                raise ValueError(f"unknown cart kind {item.kind!r}")
            if item.quantity < 1:
                raise ValueError("quantity must be >= 1")


@dataclass(frozen=True)
class QuoteLine:
    """One priced line of a quote."""

    label: str
    kind: str
    ref: str
    quantity: int
    unit_price_idr: int
    line_total_idr: int
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Quote:
    """A priced cart: line breakdown plus total, all in IDR."""

    contract_version: int
    currency: str
    interval: str
    lines: tuple[QuoteLine, ...]
    subtotal_idr: int
    discount_idr: int
    total_idr: int
    # Credits the purchase grants (monthly allowance or pack credits).
    credits_granted: int
    billing_model: str

    def to_dict(self) -> dict:
        return {
            "contract_version": self.contract_version,
            "currency": self.currency,
            "interval": self.interval,
            "lines": [
                {
                    "label": line.label,
                    "kind": line.kind,
                    "ref": line.ref,
                    "quantity": line.quantity,
                    "unit_price_idr": line.unit_price_idr,
                    "line_total_idr": line.line_total_idr,
                    "detail": dict(line.detail),
                }
                for line in self.lines
            ],
            "subtotal_idr": self.subtotal_idr,
            "discount_idr": self.discount_idr,
            "total_idr": self.total_idr,
            "credits_granted": self.credits_granted,
            "billing_model": self.billing_model,
        }


#: v1 field names and their types. A document missing one of these, or
#: carrying one with a different type, is not a valid v1 quote.
_V1_QUOTE_FIELDS: dict[str, type] = {
    "contract_version": int,
    "currency": str,
    "interval": str,
    "lines": list,
    "subtotal_idr": int,
    "discount_idr": int,
    "total_idr": int,
    "credits_granted": int,
    "billing_model": str,
}


def validate_additive(quote_dict: dict, *, version: int = CONTRACT_VERSION) -> None:
    """Check a stored quote document still satisfies contract v1.

    Raises ``ValueError`` if a v1 field is missing or has the wrong
    type. Extra fields are allowed: that is what additive-only means.
    """
    if version != CONTRACT_VERSION:
        raise ValueError(f"unsupported contract version {version!r}")
    for name, expected in _V1_QUOTE_FIELDS.items():
        if name not in quote_dict:
            raise ValueError(f"quote is missing v1 field {name!r}")
        value = quote_dict[name]
        # bool is a subclass of int; a boolean is never a valid price.
        if isinstance(value, bool) or not isinstance(value, expected):
            raise ValueError(
                f"quote field {name!r} has type {type(value).__name__}, "
                f"expected {expected.__name__}"
            )


def dedicated_price_idr(
    *, vcpu: int, ram_gb: int, disk_gb: int, config: BillingConfig | None = None
) -> int:
    """Price a dedicated machine from its spec.

    base + per vCPU + per GB RAM + per started 20 GB chunk of disk.
    """
    cfg = config or default_config()
    pricing = cfg.dedicated
    if vcpu < 0 or ram_gb < 0 or disk_gb < 0:
        raise ValueError("dedicated spec values must be >= 0")
    chunks = math.ceil(disk_gb / pricing.disk_chunk_gb) if disk_gb else 0
    return (
        pricing.base_idr
        + pricing.per_vcpu_idr * vcpu
        + pricing.per_ram_gb_idr * ram_gb
        + pricing.per_disk_chunk_idr * chunks
    )


def quote(cart: Cart, *, config: BillingConfig | None = None) -> Quote:
    """Price a cart purely from config. Returns line items + total.

    Annual interval: each monthly-priced line is charged for 10 months
    instead of 12 (2 months free); one-off items (credit packs) are
    not discounted. The discount is shown as its own amount.
    """
    cfg = config or default_config()
    lines: list[QuoteLine] = []
    credits_granted = 0
    monthly_subtotal = 0
    one_off_subtotal = 0

    for item in cart.items:
        if item.kind == "tier":
            tier = cfg.tier(item.ref)
            monthly = tier.price_idr_per_month * item.quantity
            lines.append(
                QuoteLine(
                    label=f"Tier {tier.tier_id} ({tier.description})",
                    kind="tier",
                    ref=item.ref,
                    quantity=item.quantity,
                    unit_price_idr=tier.price_idr_per_month,
                    line_total_idr=monthly,
                    detail={
                        "vcpu": tier.vcpu,
                        "ram_gb": tier.ram_gb,
                        "disk_gb": tier.disk_gb,
                        "seats": tier.seats,
                    },
                )
            )
            monthly_subtotal += monthly
            credits_granted += cfg.monthly_credit_allowance(item.ref) * item.quantity
        elif item.kind == "credit_pack":
            pack = cfg.credit_pack(item.ref)
            total = pack.price_idr * item.quantity
            lines.append(
                QuoteLine(
                    label=f"Credit pack {pack.pack_id} ({pack.credits} credits)",
                    kind="credit_pack",
                    ref=item.ref,
                    quantity=item.quantity,
                    unit_price_idr=pack.price_idr,
                    line_total_idr=total,
                    detail={"credits": pack.credits},
                )
            )
            one_off_subtotal += total
            credits_granted += pack.credits * item.quantity
        elif item.kind == "dedicated":
            unit = dedicated_price_idr(
                vcpu=item.vcpu, ram_gb=item.ram_gb, disk_gb=item.disk_gb, config=cfg
            )
            total = unit * item.quantity
            lines.append(
                QuoteLine(
                    label="Dedicated machine",
                    kind="dedicated",
                    ref="dedicated",
                    quantity=item.quantity,
                    unit_price_idr=unit,
                    line_total_idr=total,
                    detail={"vcpu": item.vcpu, "ram_gb": item.ram_gb, "disk_gb": item.disk_gb},
                )
            )
            monthly_subtotal += total
        elif item.kind == "extra_seat":
            unit = TEAM_EXTRA_SEAT_IDR_PER_MONTH
            total = unit * item.quantity
            lines.append(
                QuoteLine(
                    label="Extra Team seat",
                    kind="extra_seat",
                    ref="extra_seat",
                    quantity=item.quantity,
                    unit_price_idr=unit,
                    line_total_idr=total,
                    detail={},
                )
            )
            monthly_subtotal += total

    if cart.interval == "annual":
        # 12 months charged as 10: the discount is 2 monthly payments.
        subtotal = monthly_subtotal * 12 + one_off_subtotal
        discount = monthly_subtotal * cfg.annual_free_months
        total = subtotal - discount
    else:
        subtotal = monthly_subtotal + one_off_subtotal
        discount = 0
        total = subtotal

    return Quote(
        contract_version=CONTRACT_VERSION,
        currency=cfg.currency,
        interval=cart.interval,
        lines=tuple(lines),
        subtotal_idr=subtotal,
        discount_idr=discount,
        total_idr=total,
        credits_granted=credits_granted,
        billing_model=cfg.billing_model,
    )
