"""Store + API tests for B17 creator support (``puspa/gallery/support.py`` + ``puspa/pwa/support_api.py``).

Covers the HTTP layer against the real ``puspa.pwa.app.create_app``:
the split math (platform commission + creator credits in Rupiah,
always summing exactly), the hard rule that the creator's credit
value stays below the price paid, self-purchase always losing money
at every amount, the ledger recording each purchase as an
``evidence_recorded`` event (the ledger's closed event set gains no
new type), commission refused for anything that is not a digital
gallery work (price check, authenticity check), the support totals
and creator credit balance, and the absence of any cash-out path:
no function, route, or field turns credits back into money. The
support schema is applied by the autouse fixture below (conftest
does NOT apply it).
"""

import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from puspa.gallery.store import GalleryStore
from puspa.gallery.store import create_schema as create_gallery_schema
from puspa.gallery.store import reset_tables as reset_gallery_tables
from puspa.gallery.support import (
    CREDIT_VALUE_IDR,
    MAX_SUPPORT_IDR,
    MIN_SUPPORT_IDR,
    PLATFORM_COMMISSION_RATE,
    SupportError,
    SupportStore,
    create_schema,
    split_amount,
)
from puspa.gallery.support import reset_tables as reset_support_tables
from puspa.ledger import Ledger
from puspa.mockup.store import MockupStore
from puspa.mockup.store import create_schema as create_mockup_schema
from puspa.mockup.store import reset_tables as reset_mockup_tables
from puspa.mockup.templates import fill_template
from puspa.pwa.app import create_app


@pytest.fixture(autouse=True)
def _support_schema(test_dsn):
    create_mockup_schema(test_dsn)
    create_gallery_schema(test_dsn)
    create_schema(test_dsn)
    reset_mockup_tables(test_dsn)
    reset_gallery_tables(test_dsn)
    reset_support_tables(test_dsn)
    yield
    reset_support_tables(test_dsn)
    reset_gallery_tables(test_dsn)
    reset_mockup_tables(test_dsn)


def _client(user_id, test_dsn):
    return TestClient(create_app(test_user_id=user_id, dsn=test_dsn))


def _mockup_doc(user_id, test_dsn, title="Promo Warung Kopi"):
    store = MockupStore(dsn=test_dsn)
    doc = fill_template("promo_banner", {"shop": "Warung Sari"})
    doc["title"] = title
    return store.create(user_id, doc)


def _published_work(owner_id, test_dsn):
    doc = _mockup_doc(owner_id, test_dsn)
    return GalleryStore(dsn=test_dsn).publish(owner_id, doc)


def _support(client, work_id, amount_idr):
    response = client.post(f"/api/gallery/{work_id}/support", json={"amount_idr": amount_idr})
    assert response.status_code == 200, response.text
    return response.json()


def test_routes_exist_and_require_login(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/gallery/{work_id}/support" in paths
    assert "/api/gallery/support/credits" in paths
    anon = TestClient(create_app(test_user_id=None, dsn=test_dsn))
    with anon:
        assert (
            anon.post("/api/gallery/gal-x/support", json={"amount_idr": 10_000}).status_code == 401
        )
        assert anon.get("/api/gallery/gal-x/support").status_code == 401
        assert anon.get("/api/gallery/support/credits").status_code == 401


def test_split_math_sums_exactly_and_credits_stay_below_price():
    for amount in (1_000, 1_001, 5_000, 9_999, 10_000, 35_000, 100_000, 1_000_000):
        split = split_amount(amount)
        assert split["amount_idr"] == amount
        assert split["credits_granted"] >= 1
        # Every Rupiah is either commission or creator credit value.
        assert split["commission_idr"] + split["credits_granted"] * CREDIT_VALUE_IDR == amount
        # The platform never keeps less than its 20% rate.
        assert split["commission_idr"] * 100 >= amount * round(PLATFORM_COMMISSION_RATE * 100)
        # The hard rule: the credits are worth strictly less than the price paid.
        assert split["credits_granted"] * CREDIT_VALUE_IDR < amount
    assert split_amount(10_000) == {
        "amount_idr": 10_000,
        "commission_idr": 2_350,
        "credits_granted": 17,
        "credit_value_idr": CREDIT_VALUE_IDR,
    }
    assert split_amount(1_000)["credits_granted"] == 1


def test_split_rejects_bad_amounts():
    for bad in (0, 999, -5, 1_000_001, "10000", 10_000.0, True, None):
        with pytest.raises(SupportError):
            split_amount(bad)


def test_split_rejects_non_gallery_subjects():
    # Commission is only for digital gallery works: never for a price
    # check or an authenticity check of a physical item.
    for kind in ("price_check", "authenticity_check", "subscription", ""):
        with pytest.raises(SupportError, match="only to digital gallery works"):
            split_amount(10_000, subject_kind=kind)


def test_support_grants_credits_below_price(make_user, test_dsn, ledger):
    owner = make_user()
    supporter = make_user()
    work = _published_work(owner["user_id"], test_dsn)
    store = SupportStore(dsn=test_dsn)
    receipt = store.support(
        supporter["user_id"],
        work["id"],
        10_000,
        ledger=ledger,
        agent_id=supporter["agent_id"],
    )
    assert receipt["work_id"] == work["id"]
    assert receipt["supporter_id"] == supporter["user_id"]
    assert receipt["creator_id"] == owner["user_id"]
    assert receipt["amount_idr"] == 10_000
    assert receipt["commission_idr"] == 2_350
    assert receipt["credits_granted"] == 17
    assert receipt["credits_granted"] * CREDIT_VALUE_IDR < receipt["amount_idr"]
    # Prepare-only (B8): recorded, but no real money moves.
    assert receipt["status"] == "prepared"
    assert store.get(receipt["id"]) == receipt
    assert [row["id"] for row in store.list_for_work(work["id"])] == [receipt["id"]]
    totals = store.totals_for_work(work["id"])
    assert totals == {
        "work_id": work["id"],
        "support_count": 1,
        "total_amount_idr": 10_000,
        "total_commission_idr": 2_350,
        "total_credits_granted": 17,
    }
    assert store.credits_for_creator(owner["user_id"]) == {
        "user_id": owner["user_id"],
        "credits_received": 17,
        "supports_received": 1,
    }
    assert store.credits_for_creator(supporter["user_id"])["credits_received"] == 0


def test_self_purchase_always_loses_money(make_user, test_dsn):
    # Buying your own work to farm credits must always lose money:
    # the credits you get back are worth less than the price you paid.
    owner = make_user()
    work = _published_work(owner["user_id"], test_dsn)
    store = SupportStore(dsn=test_dsn)
    for amount in (MIN_SUPPORT_IDR, 5_000, 10_000, 100_000, MAX_SUPPORT_IDR):
        receipt = store.support(owner["user_id"], work["id"], amount)
        credit_value = receipt["credits_granted"] * CREDIT_VALUE_IDR
        net_loss = receipt["amount_idr"] - credit_value
        assert credit_value < receipt["amount_idr"]
        assert net_loss > 0
        assert net_loss >= receipt["commission_idr"]
    # The same loss through the API: the owner may support their own
    # work, and the receipt still shows the loss.
    client = _client(owner["user_id"], test_dsn)
    with client:
        receipt = _support(client, work["id"], 10_000)
        assert receipt["creator_id"] == owner["user_id"] == receipt["supporter_id"]
        assert receipt["credits_granted"] * CREDIT_VALUE_IDR < receipt["amount_idr"]


def test_support_recorded_in_the_ledger(make_user, test_dsn, ledger):
    owner = make_user()
    supporter = make_user()
    work = _published_work(owner["user_id"], test_dsn)
    store = SupportStore(dsn=test_dsn)
    receipt = store.support(
        supporter["user_id"],
        work["id"],
        10_000,
        ledger=ledger,
        agent_id=supporter["agent_id"],
    )
    events = ledger.events(receipt["id"])
    assert len(events) == 1
    event = events[0]
    # No new event type: the purchase rides on the existing type.
    assert event["type"] == "evidence_recorded"
    assert event["agent_id"] == supporter["agent_id"]
    assert event["content"]["kind"] == "creator_support"
    assert event["content"]["support_id"] == receipt["id"]
    assert event["content"]["amount_idr"] == 10_000
    assert event["content"]["commission_idr"] == 2_350
    assert event["content"]["credits_granted"] == 17
    assert event["content"]["status"] == "prepared"
    intact, broken = ledger.verify_chain(receipt["id"])
    assert intact and broken == []


def test_support_without_registered_agent_is_refused(make_user, test_dsn, ledger):
    # The ledger entry needs the supporter's agent id; a supporter
    # with no registered agent is refused instead of recorded
    # without a ledger entry.
    owner = make_user()
    work = _published_work(owner["user_id"], test_dsn)
    store = SupportStore(dsn=test_dsn)
    agentless_supporter = str(uuid.uuid4())  # a real user id with no registered agent
    with pytest.raises(SupportError, match="no registered Puspa agent"):
        store.support(agentless_supporter, work["id"], 10_000, ledger=ledger)
    assert store.list_for_work(work["id"]) == []
    assert store.totals_for_work(work["id"])["support_count"] == 0


def test_support_unknown_or_unpublished_work(make_user, test_dsn):
    owner = make_user()
    supporter = make_user()
    work = _published_work(owner["user_id"], test_dsn)
    store = SupportStore(dsn=test_dsn)
    with pytest.raises(KeyError):
        store.support(supporter["user_id"], "gal-nope", 10_000)
    with pytest.raises(KeyError):
        store.totals_for_work("gal-nope")
    GalleryStore(dsn=test_dsn).unpublish(owner["user_id"], work["id"])
    with pytest.raises(KeyError):
        store.support(supporter["user_id"], work["id"], 10_000)
    client = _client(supporter["user_id"], test_dsn)
    with client:
        assert (
            client.post("/api/gallery/gal-nope/support", json={"amount_idr": 10_000}).status_code
            == 404
        )
        assert client.get("/api/gallery/gal-nope/support").status_code == 404
        assert (
            client.post(
                f"/api/gallery/{work['id']}/support", json={"amount_idr": 10_000}
            ).status_code
            == 404
        )


def test_support_api_validation(make_user, test_dsn):
    owner = make_user()
    supporter = make_user()
    work = _published_work(owner["user_id"], test_dsn)
    client = _client(supporter["user_id"], test_dsn)
    with client:
        assert client.post(f"/api/gallery/{work['id']}/support", json={}).status_code == 400
        for bad in (0, 999, -5, 1_000_001, "10000", 10_000.5, True):
            response = client.post(f"/api/gallery/{work['id']}/support", json={"amount_idr": bad})
            assert response.status_code == 400, (bad, response.text)
        assert (
            client.post(
                f"/api/gallery/{work['id']}/support", json={"amount_idr": 10_000}
            ).status_code
            == 200
        )


def test_support_api_totals_and_creator_credits(make_user, test_dsn):
    owner = make_user()
    first = make_user()
    second = make_user()
    work = _published_work(owner["user_id"], test_dsn)
    with _client(first["user_id"], test_dsn) as client:
        _support(client, work["id"], 10_000)
    with _client(second["user_id"], test_dsn) as client:
        _support(client, work["id"], 5_000)
    with _client(first["user_id"], test_dsn) as client:
        totals = client.get(f"/api/gallery/{work['id']}/support")
        assert totals.status_code == 200
        assert totals.json() == {
            "work_id": work["id"],
            "support_count": 2,
            "total_amount_idr": 15_000,
            "total_commission_idr": 3_750,
            "total_credits_granted": 25,
        }
    with _client(owner["user_id"], test_dsn) as client:
        credits = client.get("/api/gallery/support/credits")
        assert credits.status_code == 200
        assert credits.json() == {
            "user_id": owner["user_id"],
            "credits_received": 25,
            "supports_received": 2,
        }
    with _client(first["user_id"], test_dsn) as client:
        assert client.get("/api/gallery/support/credits").json()["credits_received"] == 0


def test_no_cash_out_path_anywhere():
    # There is no way to turn credits back into money: no function,
    # route, or receipt field for it, and the feature never touches
    # the price-check module (physical goods carry no commission).
    root = Path(__file__).resolve().parents[1]
    sources = {
        "puspa/gallery/support.py": (root / "puspa/gallery/support.py").read_text(),
        "puspa/pwa/support_api.py": (root / "puspa/pwa/support_api.py").read_text(),
    }
    for name, text in sources.items():
        assert "check_before_buy" not in text, name
        defined = re.findall(r"(?m)^(?:def|class)\s+(\w+)", text)
        accessed = re.findall(r"\.(\w*(?:cash_?out|withdraw|payout)\w*)\s*\(", text)
        assert not [d for d in defined if re.search("cash_?out|withdraw|payout", d)], name
        assert not accessed, name
    store = SupportStore()  # DSN comes from PUSPA_TEST_DSN, never hardcoded here
    for forbidden in ("cash_out", "cashout", "withdraw", "payout"):
        assert not hasattr(store, forbidden)
    assert set(split_amount(10_000)) == {
        "amount_idr",
        "commission_idr",
        "credits_granted",
        "credit_value_idr",
    }


def test_support_uses_the_ledger_fixture(make_user, test_dsn, ledger):
    # The shared ledger fixture is a real Ledger on the test DSN:
    # a support recorded through it reads back from the same ledger.
    assert isinstance(ledger, Ledger)
    owner = make_user()
    supporter = make_user()
    work = _published_work(owner["user_id"], test_dsn)
    receipt = SupportStore(dsn=test_dsn).support(
        supporter["user_id"],
        work["id"],
        5_000,
        ledger=ledger,
        agent_id=supporter["agent_id"],
    )
    assert (
        ledger.events(receipt["id"], event_type="evidence_recorded")[0]["content"][
            "credits_granted"
        ]
        == 8
    )
