"""Billing routes: one handler, ``if path + method -> service``.

This is a plain Python function, not a live HTTP server: the HTTP
surface belongs to another work item (G1/G13), which calls
:func:`handle_billing_request` with the method, path, and payload it
received and returns the ``{"status", "body"}`` dict as the response.

Routes:
- GET  /billing/catalog    tier + credit-pack catalog, one price source
- POST /billing/quote      price a cart (see ``contract.quote``)
- GET  /billing/overview   plan, credit allowance, usage battery, upsell
- GET  /billing/invoices   invoices from the payment provider
- GET  /billing/license    verify a signed entitlement
- POST /billing/license    issue a signed entitlement
- POST /billing/checkout   create a checkout (idempotent)
- POST /billing/webhook    verified, idempotent webhook handling
- GET  /billing/team       seats and extra-seat pricing
- GET  /billing/clouds     cloud machines on the entitlement
- POST /billing/ops/funnel record a funnel event (ops)
- POST /billing/ops/grant  grant credits (ops)
- POST /billing/ops/review record a review decision (ops)

Unknown paths return 404; malformed payloads return 400. Nothing
here moves real money: checkout goes through the configured provider
(fake by default) and the local gateway is a stub.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import entitlements as entitlement_mod
from .client import BillingClient, new_idempotency_key, portal_link
from .config import TEAM_EXTRA_SEAT_IDR_PER_MONTH, BillingConfig, default_config
from .contract import Cart, CartItem
from .contract import quote as price_quote
from .ledger import (
    MemoryUsageStore,
    battery_view,
    default_memory_store,
    upsell_due,
)
from .providers import (
    FakePaymentProvider,
    PaymentProvider,
    ProviderError,
)
from .webhook import MemoryIdempotencyStore, handle_webhook


@dataclass
class BillingDeps:
    """Everything the handler needs, injected by the caller."""

    config: BillingConfig = field(default_factory=default_config)
    provider: PaymentProvider = field(default_factory=FakePaymentProvider)
    usage_store: MemoryUsageStore = field(default_factory=default_memory_store)
    webhook_store: MemoryIdempotencyStore = field(default_factory=MemoryIdempotencyStore)
    webhook_secret: str = ""
    entitlement_secret: str = ""
    ops_log: list = field(default_factory=list)
    grants: list = field(default_factory=list)


def _bad_request(message: str) -> dict:
    return {"status": 400, "body": {"error": "bad_request", "message": message}}


def _not_found(path: str) -> dict:
    return {"status": 404, "body": {"error": "unknown_route", "path": path}}


def _ok(body: dict, *, status: int = 200) -> dict:
    return {"status": status, "body": body}


def _catalog(deps: BillingDeps, payload: dict) -> dict:
    cfg = deps.config
    return _ok(
        {
            "contract_version": 1,
            "currency": cfg.currency,
            "billing_model": cfg.billing_model,
            "tiers": [
                {
                    "tier_id": t.tier_id,
                    "price_idr_per_month": t.price_idr_per_month,
                    "monthly_credits": t.monthly_credits,
                    "vcpu": t.vcpu,
                    "ram_gb": t.ram_gb,
                    "disk_gb": t.disk_gb,
                    "seats": t.seats,
                    "description": t.description,
                    "portal_url": portal_link(t.tier_id, config=cfg),
                }
                for t in cfg.tiers
            ],
            "credit_packs": [
                {"pack_id": p.pack_id, "credits": p.credits, "price_idr": p.price_idr}
                for p in cfg.credit_packs
            ],
            "dedicated": {
                "base_idr": cfg.dedicated.base_idr,
                "per_vcpu_idr": cfg.dedicated.per_vcpu_idr,
                "per_ram_gb_idr": cfg.dedicated.per_ram_gb_idr,
                "per_disk_chunk_idr": cfg.dedicated.per_disk_chunk_idr,
                "disk_chunk_gb": cfg.dedicated.disk_chunk_gb,
            },
            "free_credits_per_month": cfg.tiers[0].monthly_credits,
        }
    )


def _quote(deps: BillingDeps, payload: dict) -> dict:
    try:
        cart = Cart(
            interval=payload.get("interval", "monthly"),
            items=tuple(
                CartItem(
                    kind=item["kind"],
                    ref=item["ref"],
                    quantity=item.get("quantity", 1),
                    vcpu=item.get("vcpu", 0),
                    ram_gb=item.get("ram_gb", 0),
                    disk_gb=item.get("disk_gb", 0),
                )
                for item in payload.get("items", [])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _bad_request(f"invalid cart: {exc}")
    try:
        result = price_quote(cart, config=deps.config)
    except (KeyError, ValueError) as exc:
        return _bad_request(str(exc))
    return _ok(result.to_dict())


def _overview(deps: BillingDeps, payload: dict) -> dict:
    user_id = payload.get("user_id")
    if not user_id:
        return _bad_request("user_id is required")
    tier_id = payload.get("tier_id", "free")
    try:
        tier = deps.config.tier(tier_id)
    except KeyError:
        return _bad_request(f"unknown tier {tier_id!r}")
    rows = deps.usage_store.rows_for_user(user_id)
    battery = battery_view(rows, now=datetime.now(UTC))
    upsell = upsell_due(
        bots=int(payload.get("bots", 0)),
        messages=int(payload.get("messages", 0)),
        routines=int(payload.get("routines", 0)),
    )
    entitlement_status = None
    entitlement = payload.get("entitlement")
    if entitlement:
        try:
            parsed = entitlement_mod.entitlement_from_dict(entitlement)
        except (KeyError, ValueError) as exc:
            return _bad_request(f"invalid entitlement: {exc}")
        entitlement_status = entitlement_mod.verify_entitlement(
            parsed, secret=deps.entitlement_secret, config=deps.config
        )
    return _ok(
        {
            "user_id": user_id,
            "tier_id": tier_id,
            "price_idr_per_month": tier.price_idr_per_month,
            "monthly_credit_allowance": deps.config.monthly_credit_allowance(tier_id),
            "billing_model": deps.config.billing_model,
            "entitlement": entitlement_status,
            "usage_battery_7d": battery,
            "upsell": upsell,
        }
    )


def _invoices(deps: BillingDeps, payload: dict) -> dict:
    user_id = payload.get("user_id")
    if not user_id:
        return _bad_request("user_id is required")
    try:
        invoices = deps.provider.list_invoices(user_id=user_id, limit=20)
    except (ProviderError, NotImplementedError) as exc:
        return {"status": 502, "body": {"error": "provider_error", "message": str(exc)}}
    return _ok(
        {
            "user_id": user_id,
            "invoices": [
                {
                    "invoice_id": inv.invoice_id,
                    "amount_idr": inv.amount_idr,
                    "currency": inv.currency,
                    "status": inv.status,
                    "period": inv.period,
                    "url": inv.url,
                }
                for inv in invoices
            ],
        }
    )


def _license_get(deps: BillingDeps, payload: dict) -> dict:
    entitlement = payload.get("entitlement")
    if not entitlement:
        return _bad_request("entitlement is required")
    try:
        parsed = entitlement_mod.entitlement_from_dict(entitlement)
    except (KeyError, ValueError) as exc:
        return _bad_request(f"invalid entitlement: {exc}")
    result = entitlement_mod.verify_entitlement(
        parsed, secret=deps.entitlement_secret, config=deps.config
    )
    return _ok({"entitlement": parsed.to_dict(), "verification": result})


def _license_post(deps: BillingDeps, payload: dict) -> dict:
    try:
        entitlement = entitlement_mod.issue_entitlement(
            user_id=payload["user_id"],
            machine_id=payload["machine_id"],
            tier=payload["tier"],
            secret=deps.entitlement_secret,
            config=deps.config,
            valid_days=int(payload.get("valid_days", 30)),
            trial=bool(payload.get("trial", False)),
            clouds=tuple(payload.get("clouds") or ()),
        )
    except (KeyError, ValueError) as exc:
        return _bad_request(str(exc))
    return _ok({"entitlement": entitlement.to_dict()}, status=201)


def _checkout(deps: BillingDeps, payload: dict) -> dict:
    try:
        amount = int(payload["amount_idr"])
        description = str(payload.get("description", "Puspa purchase"))
    except (KeyError, TypeError, ValueError):
        return _bad_request("amount_idr (integer) is required")
    if amount <= 0:
        return _bad_request("amount_idr must be > 0")
    client = BillingClient(provider=deps.provider, config=deps.config)
    try:
        started = client.start_checkout(
            amount_idr=amount,
            description=description,
            idempotency_key=payload.get("idempotency_key") or new_idempotency_key(),
        )
    except (ProviderError, NotImplementedError) as exc:
        return {"status": 502, "body": {"error": "provider_error", "message": str(exc)}}
    checkout = started["checkout"]
    return _ok(
        {
            "checkout_id": checkout.checkout_id,
            "url": checkout.url,
            "amount_idr": checkout.amount_idr,
            "currency": checkout.currency,
            "status": checkout.status,
            "idempotency_key": checkout.idempotency_key,
        },
        status=201,
    )


def _webhook(deps: BillingDeps, payload: dict) -> dict:
    raw = payload.get("payload")
    if raw is None:
        return _bad_request("payload (bytes or str) is required")
    payload_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
    signature = payload.get("signature", "")
    result = handle_webhook(
        payload_bytes, signature=signature, secret=deps.webhook_secret, store=deps.webhook_store
    )
    status = 200 if result.status in ("processed", "duplicate", "ignored") else 401
    return {"status": status, "body": result.to_dict()}


def _team(deps: BillingDeps, payload: dict) -> dict:
    team = deps.config.tier("team")
    return _ok(
        {
            "tier_id": "team",
            "price_idr_per_month": team.price_idr_per_month,
            "included_seats": team.seats,
            "extra_seat_idr_per_month": TEAM_EXTRA_SEAT_IDR_PER_MONTH,
            "members": payload.get("members", []),
        }
    )


def _clouds(deps: BillingDeps, payload: dict) -> dict:
    entitlement = payload.get("entitlement")
    clouds: list[str] = []
    if entitlement:
        try:
            parsed = entitlement_mod.entitlement_from_dict(entitlement)
            clouds = list(parsed.clouds)
        except (KeyError, ValueError) as exc:
            return _bad_request(f"invalid entitlement: {exc}")
    return _ok(
        {
            "max_clouds_per_account": deps.config.max_clouds_per_account,
            "clouds": clouds,
            "count": len(clouds),
        }
    )


def _ops_funnel(deps: BillingDeps, payload: dict) -> dict:
    event = payload.get("event")
    if not event:
        return _bad_request("event is required")
    record = {
        "event": event,
        "user_id": payload.get("user_id"),
        "at": datetime.now(UTC).isoformat(),
    }
    deps.ops_log.append(record)
    return _ok({"recorded": record}, status=201)


def _ops_grant(deps: BillingDeps, payload: dict) -> dict:
    try:
        credits = int(payload["credits"])
        user_id = payload["user_id"]
    except (KeyError, TypeError, ValueError):
        return _bad_request("user_id and credits (integer) are required")
    if credits <= 0:
        return _bad_request("credits must be > 0")
    grant = {
        "user_id": user_id,
        "credits": credits,
        "reason": payload.get("reason", "ops_grant"),
        "at": datetime.now(UTC).isoformat(),
    }
    deps.grants.append(grant)
    return _ok({"grant": grant}, status=201)


def _ops_review(deps: BillingDeps, payload: dict) -> dict:
    decision = payload.get("decision")
    if decision not in ("approve", "reject", "escalate"):
        return _bad_request("decision must be approve, reject, or escalate")
    record = {
        "decision": decision,
        "subject": payload.get("subject"),
        "note": payload.get("note", ""),
        "at": datetime.now(UTC).isoformat(),
    }
    deps.ops_log.append(record)
    return _ok({"review": record}, status=201)


_ROUTES: dict[tuple[str, str], Callable[[BillingDeps, dict], dict]] = {
    ("GET", "/billing/catalog"): _catalog,
    ("POST", "/billing/quote"): _quote,
    ("GET", "/billing/overview"): _overview,
    ("GET", "/billing/invoices"): _invoices,
    ("GET", "/billing/license"): _license_get,
    ("POST", "/billing/license"): _license_post,
    ("POST", "/billing/checkout"): _checkout,
    ("POST", "/billing/webhook"): _webhook,
    ("GET", "/billing/team"): _team,
    ("GET", "/billing/clouds"): _clouds,
    ("POST", "/billing/ops/funnel"): _ops_funnel,
    ("POST", "/billing/ops/grant"): _ops_grant,
    ("POST", "/billing/ops/review"): _ops_review,
}


def handle_billing_request(
    method: str, path: str, payload: dict | None, deps: BillingDeps | None = None
) -> dict:
    """Route one billing request to its service.

    Returns ``{"status": <http status>, "body": {...}}``. This is a
    plain function; the live HTTP server that calls it belongs to
    another work item.
    """
    deps = deps or BillingDeps()
    handler = _ROUTES.get((method.upper(), path))
    if handler is None:
        return _not_found(path)
    return handler(deps, payload or {})
