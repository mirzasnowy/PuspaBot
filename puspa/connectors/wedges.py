"""Indonesian wedge connectors (G10, layer A): definitions, not live.

The Puspa wedges are price comparison for new products
(Tokopedia, Shopee, Blibli) and bill autopilot (PLN, PDAM, pulsa,
data). This module defines them as connector definitions with
request/response contracts, plus deterministic fake providers that
return sample data for tests and demos.

ToS gate (decision 15, still open with Nero): live execution
(scraping or live API calls to a marketplace or biller) is ON HOLD
until Nero decides. Nothing in this module performs a live call:

- ``live_execution_enabled`` defaults to OFF and is only ON when
  explicitly enabled via the ``PUSPA_CONNECTORS_LIVE_EXECUTION``
  environment variable or an explicit argument.
- Fake providers run without the flag: they return fixed sample
  data and never touch the network.
- ``execute_live`` refuses when the flag is OFF, and even when the
  flag is ON it raises ``LiveExecutionError`` because no live
  marketplace or biller client exists yet. Building one is a
  follow-up gated on Nero's ToS decision; it must never be faked
  into looking connected.
"""

from __future__ import annotations

import os

from .native import ConnectorDefinition, CuratedDirectory, Operation

__all__ = [
    "LIVE_EXECUTION_ENV",
    "LiveExecutionError",
    "LiveExecutionDisabledError",
    "ApprovalRequiredError",
    "live_execution_enabled",
    "LiveExecutionGate",
    "WEDGE_CONNECTOR_IDS",
    "build_wedge_definitions",
    "curated_wedge_directory",
    "FakeMarketplaceProvider",
    "FakeBillProvider",
    "FAKE_PRICE_RESULTS",
    "FAKE_BILL_RESULTS",
]

#: Env var that enables live wedge execution. Default OFF.
LIVE_EXECUTION_ENV = "PUSPA_CONNECTORS_LIVE_EXECUTION"

#: Connector ids of the Indonesian wedge connectors.
WEDGE_CONNECTOR_IDS = (
    "tokopedia",
    "shopee",
    "blibli",
    "pln",
    "pdam",
    "pulsa",
    "data",
)

#: Deterministic sample price results returned by fake providers.
FAKE_PRICE_RESULTS = (
    {"title": "Sample Wireless Earbuds X1", "price_idr": 299000,
     "seller": "sample-store", "url": "https://example.id/product/x1"},
    {"title": "Sample Wireless Earbuds X2", "price_idr": 349000,
     "seller": "sample-store-2", "url": "https://example.id/product/x2"},
)

#: Deterministic sample bill results returned by fake providers.
FAKE_BILL_RESULTS = {
    "pln": {"customer_number": "12345678901", "amount_idr": 132500,
            "period": "2026-11", "status": "unpaid"},
    "pdam": {"customer_number": "7654321", "amount_idr": 87500,
             "period": "2026-11", "status": "unpaid"},
    "pulsa": {"phone_number": "081200000000", "amount_idr": 50000,
              "status": "unpaid"},
    "data": {"phone_number": "081200000000", "amount_idr": 75000,
             "status": "unpaid"},
}


class LiveExecutionError(RuntimeError):
    """Live execution was requested but no live client exists."""


class LiveExecutionDisabledError(LiveExecutionError):
    """Live execution is OFF (ToS decision pending)."""


class ApprovalRequiredError(RuntimeError):
    """A side-effect operation needs user approval first."""


def live_execution_enabled(explicit=None, environ=None) -> bool:
    """True only when live wedge execution is explicitly enabled.

    ``explicit`` (a bool) wins; otherwise the
    ``PUSPA_CONNECTORS_LIVE_EXECUTION`` env var is read (``"1"`` /
    ``"true"``, case-insensitive, means ON). Anything else, including
    an unset variable, means OFF.
    """
    if explicit is not None:
        return bool(explicit)
    env = os.environ if environ is None else environ
    return str(env.get(LIVE_EXECUTION_ENV, "")).strip().lower() in ("1", "true")


class LiveExecutionGate:
    """The single gate in front of every live wedge call."""

    def __init__(self, enabled=False, *, environ=None):
        self.enabled = live_execution_enabled(enabled, environ=environ)

    def require_live(self):
        """Raise unless live execution is enabled."""
        if not self.enabled:
            raise LiveExecutionDisabledError(
                "live wedge execution is OFF: the marketplace/biller "
                "ToS decision (decision 15) is still open with Nero. "
                "Use the fake providers for tests and demos."
            )


def _price_check_operation() -> Operation:
    return Operation(
        name="check_price",
        description="Check the price of a new product by search query.",
        request_schema={"query": "string", "max_results": "integer"},
        response_schema={"results": "array"},
        requires_approval=False,
        live=True,
    )


def _bill_check_operation() -> Operation:
    return Operation(
        name="check_bill",
        description="Check an unpaid bill for a customer number.",
        request_schema={"customer_number": "string"},
        response_schema={
            "customer_number": "string",
            "amount_idr": "integer",
            "period": "string",
            "status": "string",
        },
        requires_approval=False,
        live=True,
    )


def _bill_pay_operation() -> Operation:
    return Operation(
        name="pay_bill",
        description="Pay an unpaid bill. Always needs user approval.",
        request_schema={"customer_number": "string", "amount_idr": "integer"},
        response_schema={"receipt_id": "string", "status": "string"},
        requires_approval=True,
        live=True,
    )


def _topup_operation() -> Operation:
    return Operation(
        name="top_up",
        description="Top up pulsa or mobile data. Always needs approval.",
        request_schema={"phone_number": "string", "amount_idr": "integer"},
        response_schema={"receipt_id": "string", "status": "string"},
        requires_approval=True,
        live=True,
    )


def build_wedge_definitions() -> list[ConnectorDefinition]:
    """The seven wedge connector definitions (no live clients)."""
    price_check = _price_check_operation()
    bill_check = _bill_check_operation()
    bill_pay = _bill_pay_operation()
    top_up = _topup_operation()
    return [
        ConnectorDefinition(
            connector_id="tokopedia",
            display_name="Tokopedia",
            auth_kind="oauth2",
            scopes=("price:read",),
            operations=(price_check,),
            description="New-product price check on Tokopedia.",
        ),
        ConnectorDefinition(
            connector_id="shopee",
            display_name="Shopee",
            auth_kind="oauth2",
            scopes=("price:read",),
            operations=(price_check,),
            description="New-product price check on Shopee.",
        ),
        ConnectorDefinition(
            connector_id="blibli",
            display_name="Blibli",
            auth_kind="oauth2",
            scopes=("price:read",),
            operations=(price_check,),
            description="New-product price check on Blibli.",
        ),
        ConnectorDefinition(
            connector_id="pln",
            display_name="PLN",
            auth_kind="credentials",
            scopes=("bill:read", "bill:pay"),
            operations=(bill_check, bill_pay),
            description="Electricity bill check and autopilot payment.",
        ),
        ConnectorDefinition(
            connector_id="pdam",
            display_name="PDAM",
            auth_kind="credentials",
            scopes=("bill:read", "bill:pay"),
            operations=(bill_check, bill_pay),
            description="Water bill check and autopilot payment.",
        ),
        ConnectorDefinition(
            connector_id="pulsa",
            display_name="Pulsa",
            auth_kind="credentials",
            scopes=("topup:read", "topup:pay"),
            operations=(bill_check, top_up),
            description="Mobile credit balance check and top-up.",
        ),
        ConnectorDefinition(
            connector_id="data",
            display_name="Data",
            auth_kind="credentials",
            scopes=("topup:read", "topup:pay"),
            operations=(bill_check, top_up),
            description="Mobile data balance check and top-up.",
        ),
    ]


def curated_wedge_directory() -> CuratedDirectory:
    """A curated directory holding exactly the wedge definitions."""
    directory = CuratedDirectory()
    for definition in build_wedge_definitions():
        directory.register(definition)
    return directory


class FakeMarketplaceProvider:
    """Deterministic fake for tokopedia / shopee / blibli.

    Returns the same sample results for every query (filtered only
    by ``max_results``). Never touches the network, so it runs with
    the live flag OFF. The sample data is clearly fake: example.id
    URLs and ``sample-store`` sellers.
    """

    def __init__(self, connector_id: str):
        if connector_id not in ("tokopedia", "shopee", "blibli"):
            raise ValueError(f"not a marketplace wedge: {connector_id!r}")
        self.connector_id = connector_id

    def check_price(self, query: str, *, max_results: int = 10) -> dict:
        """Fake price check: deterministic sample results."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if max_results < 1:
            raise ValueError("max_results must be at least 1")
        return {
            "connector": self.connector_id,
            "query": query,
            "results": [
                dict(result) for result in FAKE_PRICE_RESULTS[:max_results]
            ],
        }


class FakeBillProvider:
    """Deterministic fake for pln / pdam / pulsa / data.

    Returns the fixed sample bill for the connector. ``pay_bill``
    and ``top_up`` refuse without approval, mirroring the real
    approval gate; with approval they return a deterministic fake
    receipt and never move money.
    """

    def __init__(self, connector_id: str):
        if connector_id not in ("pln", "pdam", "pulsa", "data"):
            raise ValueError(f"not a bill wedge: {connector_id!r}")
        self.connector_id = connector_id

    def check_bill(self, customer_number: str) -> dict:
        """Fake bill check: the fixed sample bill for this biller."""
        if not isinstance(customer_number, str) or not customer_number:
            raise ValueError("customer_number must be a non-empty string")
        bill = dict(FAKE_BILL_RESULTS[self.connector_id])
        bill["customer_number"] = customer_number
        return bill

    def pay_bill(self, customer_number: str, amount_idr: int, *, approved=False) -> dict:
        """Fake payment: approval required, receipt is deterministic."""
        if not approved:
            raise ApprovalRequiredError(
                "paying a bill needs user approval (single gate)"
            )
        return {
            "receipt_id": f"fake-{self.connector_id}-{customer_number}",
            "status": "paid (fake)",
        }

    def top_up(self, phone_number: str, amount_idr: int, *, approved=False) -> dict:
        """Fake top-up: approval required, receipt is deterministic."""
        if not approved:
            raise ApprovalRequiredError(
                "topping up needs user approval (single gate)"
            )
        return {
            "receipt_id": f"fake-{self.connector_id}-{phone_number}",
            "status": "topped up (fake)",
        }


def execute_live(connector_id: str, operation: str, request: dict, *, gate=None):
    """Live wedge execution. Always refuses today.

    When the gate is OFF (the default) it raises
    :class:`LiveExecutionDisabledError`. When the gate is ON it
    raises :class:`LiveExecutionError`: no live marketplace or
    biller client exists yet, and building one waits for Nero's
    ToS decision. This function exists so the refusal point is
    single and testable, not so live calls can slip through
    another path.
    """
    gate = gate or LiveExecutionGate()
    gate.require_live()
    raise LiveExecutionError(
        f"no live client for {connector_id}.{operation} yet: live "
        "marketplace/biller execution waits for the ToS decision "
        "(decision 15). Use the fake providers until then."
    )
