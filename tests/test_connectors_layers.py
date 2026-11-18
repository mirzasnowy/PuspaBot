"""Tests: native layer, curated directory, account sync (G10).

In-memory only: no database, no network.
"""

import pytest

from puspa.connectors.accounts import (
    AccountError,
    ConnectorAccountStore,
    ModuleAccountSelection,
)
from puspa.connectors.native import (
    ConnectorDefinition,
    CuratedDirectory,
    DirectoryError,
    Operation,
)
from puspa.connectors.wedges import (
    FakeBillProvider,
    FakeMarketplaceProvider,
    build_wedge_definitions,
    curated_wedge_directory,
)


def _definition(connector_id="sample-app"):
    return ConnectorDefinition(
        connector_id=connector_id,
        display_name="Sample App",
        auth_kind="oauth2",
        scopes=("read",),
        operations=(
            Operation(
                name="list_items",
                description="List items.",
                request_schema={"query": "string"},
                response_schema={"items": "array"},
                live=False,
            ),
        ),
        description="A sample native connector.",
    )


def test_directory_registers_and_lists():
    directory = CuratedDirectory()
    directory.register(_definition())
    assert "sample-app" in directory
    assert directory.list_ids() == ["sample-app"]
    assert directory.get("sample-app").display_name == "Sample App"
    assert directory.tools("sample-app") == [
        {"name": "list_items", "description": "List items.", "service": "sample-app"}
    ]


def test_directory_rejects_duplicates_bad_slugs_and_bad_operations():
    directory = CuratedDirectory()
    directory.register(_definition())
    with pytest.raises(DirectoryError, match="already registered"):
        directory.register(_definition())
    with pytest.raises(DirectoryError, match="slug"):
        directory.register(_definition("Bad ID"))
    with pytest.raises(DirectoryError, match="auth_kind"):
        directory.register(
            ConnectorDefinition(
                connector_id="bad-auth",
                display_name="Bad",
                auth_kind="magic",
                operations=(_definition().operations[0],),
            )
        )
    with pytest.raises(DirectoryError, match="at least one operation"):
        directory.register(
            ConnectorDefinition(connector_id="no-ops", display_name="No Ops", auth_kind="none")
        )


def test_wedge_directory_has_all_seven_definitions():
    directory = curated_wedge_directory()
    assert directory.list_ids() == [
        "tokopedia",
        "shopee",
        "blibli",
        "pln",
        "pdam",
        "pulsa",
        "data",
    ]
    assert len(directory.all_tools()) == 11
    tokopedia = directory.get("tokopedia")
    assert tokopedia.operation("check_price").live is True
    pln = directory.get("pln")
    assert pln.operation("pay_bill").requires_approval is True
    assert pln.operation("check_bill").requires_approval is False
    assert build_wedge_definitions()[0].auth_kind == "oauth2"
    assert directory.get("pln").auth_kind == "credentials"


def test_account_sync_one_account_many_modules():
    store = ConnectorAccountStore()
    first = store.link("user-1", "tokopedia", "Main Store")
    second = store.link("user-1", "tokopedia", "Second Store")
    assert store.list_accounts("user-1", "tokopedia")[0].account_id == first.account_id
    assert len(store.list_accounts("user-1")) == 2
    assert len(store.list_accounts("user-2")) == 0
    assert store.get(first.account_id).label == "Main Store"
    assert store.get(first.account_id).credentials_ref is None
    assert store.unlink("user-1", first.account_id) is True
    assert store.unlink("user-1", first.account_id) is False
    assert store.list_accounts("user-1", "tokopedia")[0].account_id == second.account_id


def test_per_module_account_choice_beats_global_default():
    store = ConnectorAccountStore()
    selection = ModuleAccountSelection()
    acc_a = store.link("user-1", "pln", "Home")
    acc_b = store.link("user-1", "pln", "Office")
    # No choice yet: explicit default wins, else first linked account.
    assert selection.resolve("module-x", "user-1", "pln", store) == acc_a.account_id
    assert (
        selection.resolve("module-x", "user-1", "pln", store, default_account_id=acc_b.account_id)
        == acc_b.account_id
    )
    # Module choice beats the default.
    selection.set_choice("module-x", "user-1", "pln", acc_b.account_id, store)
    assert (
        selection.resolve("module-x", "user-1", "pln", store, default_account_id=acc_a.account_id)
        == acc_b.account_id
    )
    # Another module is unaffected by module-x's choice.
    assert (
        selection.resolve("module-y", "user-1", "pln", store, default_account_id=acc_a.account_id)
        == acc_a.account_id
    )
    assert selection.choices_for_module("module-x", "user-1") == {"pln": acc_b.account_id}
    selection.clear_choice("module-x", "user-1", "pln")
    assert selection.choice("module-x", "user-1", "pln") is None


def test_account_choice_rejects_foreign_or_wrong_connector_accounts():
    store = ConnectorAccountStore()
    selection = ModuleAccountSelection()
    other_user = store.link("user-2", "pln", "Other Home")
    pln_acc = store.link("user-1", "pln", "Home")
    with pytest.raises(AccountError, match="another user"):
        selection.set_choice("module-x", "user-1", "pln", other_user.account_id, store)
    with pytest.raises(AccountError, match="unknown account"):
        selection.set_choice("module-x", "user-1", "pln", "no-such-account", store)
    with pytest.raises(AccountError, match="not 'pdam'"):
        selection.set_choice("module-x", "user-1", "pdam", pln_acc.account_id, store)
    # Unlinking drops choices that pointed at the account.
    selection.set_choice("module-x", "user-1", "pln", pln_acc.account_id, store)
    store.unlink("user-1", pln_acc.account_id)
    selection.drop_account(pln_acc.account_id)
    assert selection.choice("module-x", "user-1", "pln") is None
    assert selection.resolve("module-x", "user-1", "pln", store) is None


def test_fake_marketplace_provider_is_deterministic():
    provider = FakeMarketplaceProvider("tokopedia")
    first = provider.check_price("earbuds", max_results=1)
    second = provider.check_price("earbuds", max_results=1)
    assert first == second
    assert first["connector"] == "tokopedia"
    assert first["results"][0]["price_idr"] == 299000
    assert provider.check_price("earbuds")["results"][-1]["price_idr"] == 349000
    with pytest.raises(ValueError):
        provider.check_price("  ")
    with pytest.raises(ValueError):
        FakeMarketplaceProvider("pln")


def test_fake_bill_provider_needs_approval_to_pay():
    provider = FakeBillProvider("pln")
    bill = provider.check_bill("12345678901")
    assert bill["amount_idr"] == 132500
    assert bill["status"] == "unpaid"
    with pytest.raises(Exception, match="approval"):
        provider.pay_bill("12345678901", 132500, approved=False)
    receipt = provider.pay_bill("12345678901", 132500, approved=True)
    assert receipt["receipt_id"].startswith("fake-pln-")
    assert (
        FakeBillProvider("pulsa")
        .top_up("081200000000", 50000, approved=True)["status"]
        .startswith("topped up (fake)")
    )
