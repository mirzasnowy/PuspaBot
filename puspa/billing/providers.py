"""Payment providers: one swappable interface, a fake for tests,
and a local-gateway stub.

No real money ever moves through this package. The provider that
talks to a real payment gateway does not exist yet: the local gateway
is an interface-only stub that raises a clear ``NotImplementedError``
when asked to call the real gateway, instead of pretending to
succeed. Wiring a real gateway is follow-up work for after Nero
chooses the gateway and supplies credentials.

Product keys are mapped from the environment, never hardcoded:
``PUSPA_BILLING_PRODUCT_KEY_<PRODUCT_ID>`` (uppercased, non-alphanumerics
turned into ``_``). A missing key raises ``ProviderError`` at call
time, not at import time, so the rest of the package stays usable
without any gateway configured.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


class ProviderError(Exception):
    """The payment provider refused or could not do the operation."""


class KeyConflictError(ProviderError):
    """An idempotency key was already used for a different charge.

    The caller must start a new charge with a fresh key (see
    ``client.py``); reusing the conflicting key is never retried.
    """


@dataclass(frozen=True)
class Checkout:
    """A checkout session created by a provider."""

    checkout_id: str
    url: str
    amount_idr: int
    currency: str
    status: str  # "pending" | "completed" | "failed"
    idempotency_key: str


@dataclass(frozen=True)
class Invoice:
    """One invoice as the provider reports it."""

    invoice_id: str
    amount_idr: int
    currency: str
    status: str  # "paid" | "open" | "void" | "failed"
    period: str
    url: str | None = None


class PaymentProvider(Protocol):
    """Interface every payment provider must implement."""

    name: str

    def create_checkout(
        self,
        *,
        amount_idr: int,
        description: str,
        idempotency_key: str,
        success_url: str = "",
        cancel_url: str = "",
    ) -> Checkout: ...

    def get_invoice(self, invoice_id: str) -> Invoice: ...

    def list_invoices(self, *, user_id: str, limit: int = 20) -> list[Invoice]: ...

    def verify_webhook_signature(self, *, payload: bytes, signature: str, secret: str) -> bool: ...

    def refund(
        self, *, checkout_id: str, amount_idr: int | None = None, idempotency_key: str = ""
    ) -> dict: ...


def product_key_from_env(product_id: str, *, env: Mapping[str, str] | None = None) -> str:
    """Map a product id to its provider product key from the environment.

    Reads ``PUSPA_BILLING_PRODUCT_KEY_<PRODUCT_ID>``. Raises
    ``ProviderError`` when the key is not configured; secrets are
    never hardcoded and never logged.
    """
    env = os.environ if env is None else env
    var = "PUSPA_BILLING_PRODUCT_KEY_" + re.sub(r"[^A-Z0-9]", "_", product_id.upper())
    value = env.get(var)
    if not value:
        raise ProviderError(f"product key for {product_id!r} is not configured (set {var})")
    return value


def hmac_sha256_hex(secret: str, payload: bytes) -> str:
    """HMAC-SHA256 hex digest used for webhook signatures."""
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_hmac_signature(*, payload: bytes, signature: str, secret: str) -> bool:
    """Verify a webhook signature. Accepts ``sha256=<hex>`` or raw hex."""
    if not signature or not secret:
        return False
    expected = hmac_sha256_hex(secret, payload)
    given = signature.removeprefix("sha256=").strip()
    return hmac.compare_digest(given, expected)


@dataclass
class FakePaymentProvider:
    """Deterministic fake provider for tests and local development.

    Checkouts always succeed deterministically: the checkout id is
    derived from the idempotency key, so the same key always returns
    the same checkout and a different amount under the same key raises
    ``KeyConflictError``. Invoices are consistent fakes derived from
    their id. No network, no money.
    """

    name: str = "fake"
    _checkouts: dict[str, Checkout] = field(default_factory=dict)
    _invoices: dict[str, Invoice] = field(default_factory=dict)

    def create_checkout(
        self,
        *,
        amount_idr: int,
        description: str,
        idempotency_key: str,
        success_url: str = "",
        cancel_url: str = "",
    ) -> Checkout:
        if not idempotency_key:
            raise ProviderError("idempotency_key is required")
        existing = self._checkouts.get(idempotency_key)
        if existing is not None:
            if existing.amount_idr != amount_idr:
                raise KeyConflictError(
                    f"idempotency key {idempotency_key!r} was already used "
                    f"for a different amount (Rp{existing.amount_idr})"
                )
            return existing
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
        checkout = Checkout(
            checkout_id=f"chk_fake_{digest}",
            url=f"https://checkout.fake/puspa/{digest}",
            amount_idr=amount_idr,
            currency="IDR",
            status="completed",
            idempotency_key=idempotency_key,
        )
        self._checkouts[idempotency_key] = checkout
        invoice = Invoice(
            invoice_id=f"inv_fake_{digest}",
            amount_idr=amount_idr,
            currency="IDR",
            status="paid",
            period="one-off",
            url=f"https://invoice.fake/puspa/{digest}",
        )
        self._invoices[invoice.invoice_id] = invoice
        return checkout

    def get_invoice(self, invoice_id: str) -> Invoice:
        invoice = self._invoices.get(invoice_id)
        if invoice is None:
            raise ProviderError(f"unknown invoice {invoice_id!r}")
        return invoice

    def list_invoices(self, *, user_id: str, limit: int = 20) -> list[Invoice]:
        # The fake has no per-user bookkeeping; it returns the invoices
        # it created, newest first, capped at the limit.
        return list(reversed(list(self._invoices.values())))[:limit]

    def verify_webhook_signature(self, *, payload: bytes, signature: str, secret: str) -> bool:
        return verify_hmac_signature(payload=payload, signature=signature, secret=secret)

    def refund(
        self, *, checkout_id: str, amount_idr: int | None = None, idempotency_key: str = ""
    ) -> dict:
        matches = [c for c in self._checkouts.values() if c.checkout_id == checkout_id]
        if not matches:
            raise ProviderError(f"unknown checkout {checkout_id!r}")
        return {
            "checkout_id": checkout_id,
            "refunded_idr": amount_idr if amount_idr is not None else matches[0].amount_idr,
            "status": "refunded",
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
        }


@dataclass
class LocalGatewayProvider:
    """Stub for a local (Indonesian) payment gateway.

    Interface only. Calling the real gateway is NOT implemented:
    every money-moving method raises ``NotImplementedError`` with a
    clear message instead of pretending to succeed. Wiring the real
    gateway needs Nero's gateway choice plus credentials, which do
    not exist yet.
    """

    name: str = "local-gateway"
    gateway_name: str = ""

    def _unimplemented(self, operation: str) -> NotImplementedError:
        return NotImplementedError(
            f"local gateway {operation!r} is not wired: no gateway was "
            "chosen and no credentials were supplied. Choose the gateway "
            "(decision for Nero) and implement this method against its "
            "real API; until then use FakePaymentProvider."
        )

    def create_checkout(
        self,
        *,
        amount_idr: int,
        description: str,
        idempotency_key: str,
        success_url: str = "",
        cancel_url: str = "",
    ) -> Checkout:
        raise self._unimplemented("create_checkout")

    def get_invoice(self, invoice_id: str) -> Invoice:
        raise self._unimplemented("get_invoice")

    def list_invoices(self, *, user_id: str, limit: int = 20) -> list[Invoice]:
        raise self._unimplemented("list_invoices")

    def verify_webhook_signature(self, *, payload: bytes, signature: str, secret: str) -> bool:
        # Signature verification is pure HMAC and needs no gateway.
        return verify_hmac_signature(payload=payload, signature=signature, secret=secret)

    def refund(
        self, *, checkout_id: str, amount_idr: int | None = None, idempotency_key: str = ""
    ) -> dict:
        raise self._unimplemented("refund")


def provider_from_env(*, env: Mapping[str, str] | None = None) -> PaymentProvider:
    """Pick a provider from ``PUSPA_BILLING_PROVIDER``.

    ``"fake"`` (default) or ``"local-gateway"``. Anything else raises
    ``ProviderError``.
    """
    env = os.environ if env is None else env
    name = env.get("PUSPA_BILLING_PROVIDER", "fake").strip().lower()
    if name == "fake":
        return FakePaymentProvider()
    if name == "local-gateway":
        return LocalGatewayProvider(gateway_name=env.get("PUSPA_BILLING_GATEWAY_NAME", ""))
    raise ProviderError(f"unknown billing provider {name!r}")
