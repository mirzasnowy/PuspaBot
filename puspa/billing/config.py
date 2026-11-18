"""Billing configuration: the single source of prices for Puspa.

Every price in the billing surface (quotes, invoices, entitlements)
is computed from this module and nothing else. There is no second
price table anywhere in the repo.

Billing model
-------------
The billing *model* (credits vs subscription vs selling compute) is a
business decision for Nero and is NOT locked here. It is a config
value, ``billing_model``:

- ``"subscription"`` (default): a paid tier grants a monthly credit
  allowance and a cloud machine; credit packs add credits on top.
  This matches the current brief numbers (tiers + credits running
  together).
- ``"credits"``: pay-as-you-go credits only; tiers only gate features,
  they do not grant a monthly allowance.
- ``"compute"``: the customer buys compute (dedicated machines) and
  the AI cost is billed separately / paid by the customer to the
  model provider.

The mechanism (contract, quote, ledger, entitlement, provider, webhook)
is identical in all three modes; only ``BillingConfig`` changes which
parts are active. See ``billing_model_notes()`` for the open question
to ask Nero.

Prices are integer Rupiah (IDR). No real money ever moves in this
package: payment providers are swappable modules and the local
gateway is a stub (see ``providers.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Supported values of ``billing_model``. Anything else is rejected.
BILLING_MODELS = ("credits", "subscription", "compute")

#: Contract version. Additive only: new fields may be added, existing
#: fields are never renamed, removed, or retyped (see ``contract.py``).
CONTRACT_VERSION = 1

#: Free monthly credits per user (brief, section 10).
FREE_CREDITS_PER_MONTH = 15

#: Monthly task cost ceiling enforced as a policy on top of the usage
#: ledger (see ``ledger.task_budget_remaining``).
TASK_COST_CEILING_IDR = 2000


@dataclass(frozen=True)
class Tier:
    """One subscription tier."""

    tier_id: str
    price_idr_per_month: int
    monthly_credits: int
    vcpu: int
    ram_gb: int
    disk_gb: int
    seats: int
    description: str


@dataclass(frozen=True)
class CreditPack:
    """One purchasable credit pack. Credits never expire."""

    pack_id: str
    credits: int
    price_idr: int


@dataclass(frozen=True)
class DedicatedPricing:
    """Dedicated-machine pricing: base + per unit, from a spec.

    A dedicated machine costs ``base_idr`` plus ``per_vcpu_idr`` per
    vCPU, ``per_ram_gb_idr`` per GB of RAM, and ``per_disk_chunk_idr``
    per started 20 GB chunk of disk.
    """

    base_idr: int = 50_000
    per_vcpu_idr: int = 15_000
    per_ram_gb_idr: int = 5_000
    per_disk_chunk_idr: int = 10_000
    disk_chunk_gb: int = 20


@dataclass(frozen=True)
class BillingConfig:
    """All billing behaviour that varies by deployment or decision."""

    billing_model: str = "subscription"
    currency: str = "IDR"
    # Annual billing: 2 months free (12 months charged as 10).
    annual_free_months: int = 2
    annual_months_charged: int = 10
    trial_days: int = 14
    # Entitlement grace after expiry: still valid, flagged "grace".
    grace_days: int = 30
    # Max cloud machines per account.
    max_clouds_per_account: int = 5
    # Upsell: a user counts as "invested" at these thresholds.
    invested_min_bots: int = 3
    invested_min_messages: int = 50
    invested_min_routines: int = 3
    # Upsell snooze after dismissal.
    upsell_snooze_days: int = 10
    # Settlement polling (client side).
    settlement_poll_interval_s: float = 2.0
    settlement_poll_timeout_s: float = 300.0
    # Eventual-consistency poll after an upgrade.
    upgrade_consistency_poll_s: float = 30.0
    portal_base_url: str = "https://portal.puspa.example"
    tiers: tuple[Tier, ...] = field(default_factory=lambda: DEFAULT_TIERS)
    credit_packs: tuple[CreditPack, ...] = field(default_factory=lambda: DEFAULT_CREDIT_PACKS)
    dedicated: DedicatedPricing = field(default_factory=DedicatedPricing)
    # Credit cost of metered actions (credits, not Rupiah).
    credit_costs: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_CREDIT_COSTS))

    def __post_init__(self) -> None:
        if self.billing_model not in BILLING_MODELS:
            raise ValueError(
                f"unknown billing_model {self.billing_model!r}; "
                f"supported: {', '.join(BILLING_MODELS)}"
            )

    def tier(self, tier_id: str) -> Tier:
        for tier in self.tiers:
            if tier.tier_id == tier_id:
                return tier
        raise KeyError(f"unknown tier {tier_id!r}")

    def credit_pack(self, pack_id: str) -> CreditPack:
        for pack in self.credit_packs:
            if pack.pack_id == pack_id:
                return pack
        raise KeyError(f"unknown credit pack {pack_id!r}")

    def monthly_credit_allowance(self, tier_id: str) -> int:
        """Monthly credits granted by a tier under the current model.

        Under ``"credits"`` the allowance is 0: tiers gate features
        only and credits are bought separately. Under ``"subscription"``
        and ``"compute"`` the tier grants its allowance.
        """
        if self.billing_model == "credits":
            return 0
        return self.tier(tier_id).monthly_credits


DEFAULT_TIERS: tuple[Tier, ...] = (
    Tier(
        tier_id="free",
        price_idr_per_month=0,
        monthly_credits=FREE_CREDITS_PER_MONTH,
        vcpu=0,
        ram_gb=0,
        disk_gb=0,
        seats=1,
        description="Free tier: own computer (PC or VPS), all light features.",
    ),
    Tier(
        tier_id="plus",
        price_idr_per_month=99_000,
        monthly_credits=60,
        vcpu=2,
        ram_gb=4,
        disk_gb=20,
        seats=1,
        description="Plus: cloud machine provided (2 vCPU, 4 GB RAM, 20 GB disk), idle-sleeping microVM.",
    ),
    Tier(
        tier_id="pro",
        price_idr_per_month=199_000,
        monthly_credits=150,
        vcpu=4,
        ram_gb=8,
        disk_gb=60,
        seats=1,
        description="Pro: larger cloud (4 vCPU, 8 GB RAM, 60 GB disk), priority, single-user work license.",
    ),
    Tier(
        tier_id="team",
        price_idr_per_month=499_000,
        monthly_credits=400,
        vcpu=4,
        ram_gb=8,
        disk_gb=60,
        seats=2,
        description="Team: 2 seats plus a dedicated cloud machine; each extra seat Rp99.000/month.",
    ),
)

DEFAULT_CREDIT_PACKS: tuple[CreditPack, ...] = (
    CreditPack(pack_id="pack-20", credits=20, price_idr=9_000),
    CreditPack(pack_id="pack-100", credits=100, price_idr=35_000),
    CreditPack(pack_id="pack-500", credits=500, price_idr=150_000),
)

#: Credit cost of metered actions. The brief locks module generation
#: at 5-10 credits; the config records the low end and the quote for a
#: generation states the exact amount before charging.
DEFAULT_CREDIT_COSTS: dict[str, int] = {
    "detail_check": 1,
    "module_generate": 5,
    "module_generate_max": 10,
    "heavy_compute_second": 1,
}

#: Extra seat on the Team tier, per seat per month (brief).
TEAM_EXTRA_SEAT_IDR_PER_MONTH = 99_000


def default_config() -> BillingConfig:
    """Config from the environment, falling back to brief defaults.

    ``PUSPA_BILLING_MODEL`` selects the model (see module docstring).
    ``PUSPA_BILLING_PORTAL_URL`` overrides the portal base URL.
    """
    model = os.environ.get("PUSPA_BILLING_MODEL", "subscription")
    portal = os.environ.get("PUSPA_BILLING_PORTAL_URL", "https://portal.puspa.example")
    return BillingConfig(billing_model=model, portal_base_url=portal)


def billing_model_notes() -> dict:
    """The open billing-model decision, for the coordinator to ask Nero.

    Returns the three options, what each one means for the product,
    and what the mechanism already supports under each option.
    """
    return {
        "question": "Which billing model should Puspa sell?",
        "options": {
            "credits": (
                "Pay-as-you-go credits only. Users buy credit packs "
                "(Rp9.000/20, Rp35.000/100, Rp150.000/500); every metered "
                "action costs credits; tiers gate features but grant no "
                "monthly allowance."
            ),
            "subscription": (
                "Monthly tiers (Free Rp0 / Plus Rp99.000 / Pro Rp199.000 / "
                "Team Rp499.000). Each paid tier grants a monthly credit "
                "allowance plus a cloud machine; packs add credits on top. "
                "This is the current default and matches the brief numbers."
            ),
            "compute": (
                "Sell compute, not AI tokens: the customer pays for the "
                "cloud machine (dedicated pricing: base + per vCPU + per "
                "GB RAM + per 20 GB disk) and pays the model provider "
                "directly for AI cost (own API key), as DuskBot does."
            ),
        },
        "already_supported": (
            "All three: the mechanism (contract, quote, ledger, "
            "entitlement, provider, webhook) is identical; only "
            "BillingConfig.billing_model changes which parts are active. "
            "No choice is locked in code."
        ),
        "default_now": "subscription",
    }
