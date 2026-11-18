"""Tests: wedge definitions, contracts, fake providers, live flag (G10).

In-memory only: no database, no network. Live execution must be
OFF by default and must refuse even when enabled, because no live
marketplace/biller client exists yet (ToS decision 15 open).
"""

import pytest

from puspa.connectors.wedges import (
    LIVE_EXECUTION_ENV,
    ApprovalRequiredError,
    FakeBillProvider,
    FakeMarketplaceProvider,
    LiveExecutionDisabledError,
    LiveExecutionError,
    LiveExecutionGate,
    build_wedge_definitions,
    execute_live,
    live_execution_enabled,
)


def test_live_execution_is_off_by_default():
    assert live_execution_enabled(environ={}) is False
    assert live_execution_enabled(environ={LIVE_EXECUTION_ENV: "0"}) is False
    assert live_execution_enabled(environ={LIVE_EXECUTION_ENV: "no"}) is False
    assert live_execution_enabled(environ={LIVE_EXECUTION_ENV: "1"}) is True
    assert live_execution_enabled(environ={LIVE_EXECUTION_ENV: "TRUE"}) is True
    assert live_execution_enabled(explicit=True, environ={}) is True
    assert live_execution_enabled(explicit=False, environ={LIVE_EXECUTION_ENV: "1"}) is False


def test_execute_live_refuses_when_flag_off():
    gate = LiveExecutionGate(enabled=False)
    with pytest.raises(LiveExecutionDisabledError, match="OFF"):
        execute_live("tokopedia", "check_price", {"query": "earbuds"}, gate=gate)
    with pytest.raises(LiveExecutionDisabledError, match="ToS"):
        LiveExecutionGate(enabled=False).require_live()


def test_execute_live_still_refuses_when_flag_on_no_client_yet():
    gate = LiveExecutionGate(enabled=True)
    gate.require_live()
    with pytest.raises(LiveExecutionError, match="no live client"):
        execute_live("pln", "pay_bill", {"customer_number": "1"}, gate=gate)


def test_wedge_definitions_carry_full_contracts():
    definitions = {d.connector_id: d for d in build_wedge_definitions()}
    assert sorted(definitions) == [
        "blibli", "data", "pdam", "pln", "pulsa", "shopee", "tokopedia",
    ]
    price = definitions["shopee"].operation("check_price")
    assert price.request_schema == {"query": "string", "max_results": "integer"}
    assert price.response_schema == {"results": "array"}
    assert price.requires_approval is False
    pay = definitions["pdam"].operation("pay_bill")
    assert pay.request_schema["amount_idr"] == "integer"
    assert pay.response_schema["receipt_id"] == "string"
    assert pay.requires_approval is True


def test_fake_providers_never_need_the_live_flag():
    marketplace = FakeMarketplaceProvider("blibli")
    result = marketplace.check_price("mouse", max_results=2)
    assert len(result["results"]) == 2
    assert all("price_idr" in item for item in result["results"])
    bill = FakeBillProvider("pdam").check_bill("7654321")
    assert bill["amount_idr"] == 87500
    assert bill["period"] == "2026-11"
    with pytest.raises(ApprovalRequiredError):
        FakeBillProvider("data").top_up("081200000000", 75000, approved=False)
