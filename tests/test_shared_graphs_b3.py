"""Shared anti-fraud and price graphs, per-user privacy (ticket B3).

Acceptance, each proven here:

1. Flagging a seller once changes the answer for other users
   (shared flag -> B1 answer flips to ``do_not_act`` for a
   second user who never flagged anyone).
2. Price estimates improve measurably with more checks
   (standard error shrinks as observations grow, and the
   median error against a known true price shrinks from 5 to
   50 seeded observations, both in the pure estimator and
   through the database).
3. No user can read another user's private records
   (per-user listing, per-record fetch with the wrong user,
   and shared reads that carry no private row at all).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). Shared tables
carry no user id; private records are keyed by the user id
created by the ``make_user`` fixture.
"""

from __future__ import annotations

import random
import statistics
import uuid

import pytest

from puspa.check_before_buy import (
    RecordStore,
    SellerRecord,
    check_before_buy,
    handle_check_before_buy,
)
from puspa.graphs.shared import (
    SharedGraphError,
    contribute_price,
    create_shared_schema,
    estimate_fair_price,
    flag_seller,
    get_private_record,
    list_private_records,
    list_shared_price_points,
    record_private,
    seller_reputation,
    shared_fair_price,
)

LINK = "https://www.tokopedia.com/toko/galaxy-s24"
TRUE_PRICE = 10_000_000


@pytest.fixture(autouse=True)
def _shared_schema(test_dsn):
    create_shared_schema(test_dsn)
    yield


def _user_id() -> str:
    return str(uuid.uuid4())


def _seeded_prices(seed: int, count: int) -> list[float]:
    """Deterministic noisy prices around a known true price."""
    rng = random.Random(seed)
    return [TRUE_PRICE + rng.gauss(0, 500_000) for _ in range(count)]


# ---------------------------------------------------------------------------
# Fair-price estimate (pure)
# ---------------------------------------------------------------------------


def test_estimate_empty_returns_none():
    assert estimate_fair_price(LINK, []) is None


def test_estimate_single_observation_has_no_standard_error():
    estimate = estimate_fair_price(LINK, [12_499_000], sources=["Shopee"])
    assert estimate is not None
    assert estimate.value == 12_499_000
    assert estimate.observations == 1
    assert estimate.standard_error is None
    assert estimate.low is None and estimate.high is None


def test_estimate_rejects_bad_prices():
    with pytest.raises(SharedGraphError):
        estimate_fair_price(LINK, [12_499_000, 0])
    with pytest.raises(SharedGraphError):
        estimate_fair_price(LINK, [12_499_000, -5])
    with pytest.raises(SharedGraphError):
        estimate_fair_price(LINK, ["not-a-price"])


def test_standard_error_shrinks_with_more_observations():
    """Same spread, more observations: SE falls like 1/sqrt(n)."""
    offsets = [-300_000, -100_000, 100_000, 300_000]
    few = [TRUE_PRICE + offset for offset in offsets]
    many = [TRUE_PRICE + offset for offset in offsets * 4]
    small = estimate_fair_price(LINK, few)
    large = estimate_fair_price(LINK, many)
    assert small is not None and large is not None
    assert small.value == large.value == TRUE_PRICE
    assert large.standard_error is not None and small.standard_error is not None
    assert large.standard_error < small.standard_error / 1.5


def test_estimate_error_shrinks_with_more_observations():
    """Seeded noise: the median lands closer to the true price."""
    prices = _seeded_prices(seed=11, count=50)
    few = estimate_fair_price(LINK, prices[:5])
    many = estimate_fair_price(LINK, prices)
    assert few is not None and many is not None
    assert abs(many.value - TRUE_PRICE) < abs(few.value - TRUE_PRICE)
    assert many.standard_error is not None and few.standard_error is not None
    assert many.standard_error < few.standard_error


# ---------------------------------------------------------------------------
# Shared seller reputation
# ---------------------------------------------------------------------------


def test_flag_once_protects_every_other_user(test_dsn):
    ana, budi = _user_id(), _user_id()
    assert seller_reputation(test_dsn, "Murah Banget Shop") is None

    reputation = flag_seller(
        test_dsn,
        user_id=ana,
        seller_name="Murah Banget Shop",
        flag_reason="payment taken, no goods shipped",
    )
    assert reputation.flagged
    assert reputation.flag_count == 1

    # Budi never flagged anyone, but the shared signal sees it.
    seen_by_budi = seller_reputation(test_dsn, "Murah Banget Shop")
    assert seen_by_budi is not None
    assert seen_by_budi.flagged
    assert seen_by_budi.flag_count == 1
    assert seen_by_budi.first_flagged_at is not None
    assert seen_by_budi.latest_flagged_at is not None

    # Ana flagging again does not inflate the count.
    assert flag_seller(test_dsn, user_id=ana, seller_name="Murah Banget Shop").flag_count == 1
    # A second distinct user flagging does count.
    assert flag_seller(test_dsn, user_id=budi, seller_name="Murah Banget Shop").flag_count == 2


def test_seller_key_is_case_and_space_insensitive(test_dsn):
    flag_seller(test_dsn, user_id=_user_id(), seller_name="  Murah   Banget Shop ")
    reputation = seller_reputation(test_dsn, "murah banget shop")
    assert reputation is not None and reputation.flag_count == 1


def test_flag_rejects_bad_input(test_dsn):
    with pytest.raises(SharedGraphError):
        flag_seller(test_dsn, user_id="", seller_name="Some Shop")
    with pytest.raises(SharedGraphError):
        flag_seller(test_dsn, user_id=_user_id(), seller_name="   ")


def test_shared_reputation_exposes_no_identity(test_dsn):
    flag_seller(test_dsn, user_id=_user_id(), seller_name="Murah Banget Shop")
    reputation = seller_reputation(test_dsn, "Murah Banget Shop")
    assert reputation is not None
    data = reputation.to_dict()
    assert set(data) == {
        "seller_key",
        "flag_count",
        "flagged",
        "first_flagged_at",
        "latest_flagged_at",
        "flag_reason",
    }
    blob = repr(data).lower()
    assert "user_id" not in blob
    assert "reporter" not in blob
    assert "hash" not in blob


# ---------------------------------------------------------------------------
# Shared price observations
# ---------------------------------------------------------------------------


def test_shared_fair_price_improves_with_more_checks(test_dsn):
    prices = _seeded_prices(seed=11, count=50)
    sources = ["Tokopedia", "Shopee", "Blibli"]
    for index, price in enumerate(prices[:5]):
        estimate = contribute_price(
            test_dsn,
            user_id=_user_id(),
            product_key=LINK,
            source=sources[index % 3],
            price=price,
        )
    assert estimate is not None
    assert estimate.observations == 5
    few_error = abs(estimate.value - TRUE_PRICE)
    few_se = estimate.standard_error

    for index, price in enumerate(prices[5:]):
        contribute_price(
            test_dsn,
            user_id=_user_id(),
            product_key=LINK,
            source=sources[index % 3],
            price=price,
        )
    estimate = shared_fair_price(test_dsn, LINK)
    assert estimate is not None
    assert estimate.observations == 50
    assert abs(estimate.value - TRUE_PRICE) < few_error
    assert estimate.standard_error is not None and few_se is not None
    assert estimate.standard_error < few_se
    assert estimate.sources == ["Blibli", "Shopee", "Tokopedia"]


def test_shared_fair_price_none_without_observations(test_dsn):
    assert shared_fair_price(test_dsn, LINK) is None
    assert list_shared_price_points(test_dsn, LINK) == []


def test_same_user_same_source_contributes_once(test_dsn):
    user = _user_id()
    contribute_price(test_dsn, user_id=user, product_key=LINK, source="Shopee", price=12_000_000)
    estimate = contribute_price(
        test_dsn, user_id=user, product_key=LINK, source="Shopee", price=9_000_000
    )
    assert estimate is not None
    assert estimate.observations == 1
    assert estimate.value == 12_000_000  # the first contribution stands


def test_contribute_rejects_bad_price(test_dsn):
    with pytest.raises(SharedGraphError):
        contribute_price(test_dsn, user_id=_user_id(), product_key=LINK, source="Shopee", price=0)
    with pytest.raises(SharedGraphError):
        contribute_price(
            test_dsn, user_id=_user_id(), product_key=LINK, source="Shopee", price="free"
        )


def test_shared_price_points_expose_no_identity(test_dsn):
    contribute_price(
        test_dsn, user_id=_user_id(), product_key=LINK, source="Shopee", price=12_499_000
    )
    points = list_shared_price_points(test_dsn, LINK)
    assert points == [
        {"product_key": LINK, "source": "Shopee", "price": 12_499_000.0},
    ]
    assert "user_id" not in repr(points)
    assert "hash" not in repr(points).lower()


def test_median_is_robust_to_one_outlier(test_dsn):
    for price in [10_000_000, 10_100_000, 9_900_000, 10_050_000, 99_000_000]:
        contribute_price(
            test_dsn, user_id=_user_id(), product_key=LINK, source="Shopee", price=price
        )
    estimate = shared_fair_price(test_dsn, LINK)
    assert estimate is not None
    assert estimate.value == 10_050_000
    assert estimate.mean > 20_000_000  # the mean is dragged by the outlier


# ---------------------------------------------------------------------------
# Private records: per-user, never pooled
# ---------------------------------------------------------------------------


def test_private_records_are_per_user(test_dsn, make_user):
    ana = make_user()
    budi = make_user()
    record_private(
        test_dsn,
        user_id=ana["user_id"],
        kind="price_observation",
        subject_key=LINK,
        payload={"price": 12_499_000, "source": "Shopee"},
    )
    record_private(
        test_dsn,
        user_id=ana["user_id"],
        kind="seller_note",
        subject_key="gadget store",
        payload={"note": "slow to reply"},
    )
    record_private(
        test_dsn,
        user_id=budi["user_id"],
        kind="price_observation",
        subject_key=LINK,
        payload={"price": 13_000_000, "source": "Tokopedia"},
    )

    ana_records = list_private_records(test_dsn, ana["user_id"])
    assert len(ana_records) == 2
    assert {row["user_id"] for row in ana_records} == {ana["user_id"]}
    assert {row["kind"] for row in ana_records} == {"price_observation", "seller_note"}

    budi_records = list_private_records(test_dsn, budi["user_id"])
    assert len(budi_records) == 1
    assert budi_records[0]["payload"] == {"price": 13_000_000, "source": "Tokopedia"}

    assert list_private_records(test_dsn, ana["user_id"], kind="price_observation") != []
    assert len(list_private_records(test_dsn, ana["user_id"], kind="price_observation")) == 1
    assert list_private_records(test_dsn, make_user()["user_id"]) == []


def test_get_private_record_refuses_other_user(test_dsn, make_user):
    ana = make_user()
    budi = make_user()
    record = record_private(
        test_dsn,
        user_id=ana["user_id"],
        kind="seller_note",
        subject_key="gadget store",
        payload={"note": "only ana may read this"},
    )
    assert get_private_record(test_dsn, record["id"], ana["user_id"]) is not None
    assert get_private_record(test_dsn, record["id"], budi["user_id"]) is None
    assert get_private_record(test_dsn, str(uuid.uuid4()), ana["user_id"]) is None
    assert get_private_record(test_dsn, "not-a-uuid", ana["user_id"]) is None


def test_private_records_never_appear_in_shared_reads(test_dsn, make_user):
    ana = make_user()
    budi = make_user()
    marker = "secret-note-only-ana-knows-4821"
    record_private(
        test_dsn,
        user_id=ana["user_id"],
        kind="seller_note",
        subject_key="murah banget shop",
        payload={"note": marker},
    )
    flag_seller(test_dsn, user_id=ana["user_id"], seller_name="Murah Banget Shop")
    contribute_price(
        test_dsn, user_id=ana["user_id"], product_key=LINK, source="Shopee", price=12_499_000
    )

    shared_blob = repr(
        [
            seller_reputation(test_dsn, "Murah Banget Shop"),
            shared_fair_price(test_dsn, LINK),
            list_shared_price_points(test_dsn, LINK),
        ]
    )
    assert marker not in shared_blob
    assert marker not in repr(list_private_records(test_dsn, budi["user_id"]))
    assert marker in repr(list_private_records(test_dsn, ana["user_id"]))


def test_record_private_rejects_bad_input(test_dsn, make_user):
    user = make_user()
    with pytest.raises(SharedGraphError):
        record_private(test_dsn, user_id=user["user_id"], kind="", subject_key=LINK)
    with pytest.raises(SharedGraphError):
        record_private(test_dsn, user_id=user["user_id"], kind="note", subject_key="  ")
    with pytest.raises(SharedGraphError):
        record_private(
            test_dsn, user_id=user["user_id"], kind="note", subject_key=LINK, payload=["not-a-dict"]
        )


# ---------------------------------------------------------------------------
# Wiring into B1: the shared signal changes another user's answer
# ---------------------------------------------------------------------------


def test_flagged_seller_changes_b1_answer_for_other_user(test_dsn):
    ana = _user_id()
    text = f"{LINK} toko: Murah Banget Shop"
    before = check_before_buy(text, store=RecordStore())
    assert before.seller.status == "cannot_verify"
    assert before.advice == "cannot_advise"

    flag_seller(test_dsn, user_id=ana, seller_name="Murah Banget Shop")

    after = check_before_buy(text, store=RecordStore(), shared_dsn=test_dsn)
    assert after.seller.status == "flagged"
    assert after.seller.reports == 1
    assert after.advice == "do_not_act"
    assert "shared graph" in after.seller.note
    # No flagger is named anywhere in the answer.
    assert ana not in repr(after.to_dict())


def test_shared_flag_overrides_a_locally_clean_record(test_dsn):
    store = RecordStore()
    store.add_seller_record(
        SellerRecord(name="Gadget Store", flagged=False, reports=12, note="clean on record")
    )
    flag_seller(test_dsn, user_id=_user_id(), seller_name="Gadget Store")
    answer = check_before_buy(f"{LINK} toko: Gadget Store", store=store, shared_dsn=test_dsn)
    assert answer.seller.status == "flagged"
    assert answer.advice == "do_not_act"


def test_shared_fair_price_fallback_in_b1(test_dsn):
    contribute_price(
        test_dsn, user_id=_user_id(), product_key=LINK, source="Tokopedia", price=12_999_000
    )
    contribute_price(
        test_dsn, user_id=_user_id(), product_key=LINK, source="Shopee", price=12_499_000
    )

    without_shared = check_before_buy(LINK, store=RecordStore())
    assert without_shared.fair_price.status == "cannot_verify"
    assert without_shared.fair_price.value is None

    with_shared = check_before_buy(LINK, store=RecordStore(), shared_dsn=test_dsn)
    assert with_shared.fair_price.status == "uncertain"
    assert with_shared.fair_price.value == statistics.median([12_999_000, 12_499_000])
    assert with_shared.fair_price.sources == ["Shopee", "Tokopedia"]
    assert "shared" in with_shared.fair_price.basis


def test_handle_check_before_buy_passes_shared_dsn(test_dsn):
    flag_seller(test_dsn, user_id=_user_id(), seller_name="Murah Banget Shop")
    result = handle_check_before_buy(
        {"text": f"{LINK} toko: Murah Banget Shop"},
        store=RecordStore(),
        shared_dsn=test_dsn,
    )
    assert result["answer"]["seller"]["status"] == "flagged"
    assert result["answer"]["advice"] == "do_not_act"


# ---------------------------------------------------------------------------
# local fraud patterns (15.5), B14
# ---------------------------------------------------------------------------


def test_fraud_pattern_table_covers_plan_15_5():
    from puspa.graphs.fraud_patterns import FRAUD_PATTERNS

    ids = {pattern.pattern_id for pattern in FRAUD_PATTERNS}
    assert ids == {
        "courier_apk",
        "bank_ojk_impersonation",
        "mama_minta_pulsa",
        "new_number",
        "part_time_like_subscribe",
        "illegal_investment_loan",
        "wrong_transfer_refund",
        "mule_account",
        "fake_cod",
        "account_trading",
        "online_gambling",
    }
    for pattern in FRAUD_PATTERNS:
        assert pattern.triggers
        assert pattern.warning


def test_match_fraud_patterns_names_pattern_and_warning():
    from puspa.graphs.fraud_patterns import match_fraud_patterns

    matches = match_fraud_patterns("tolong unduh apk buat cek resi paket kurir")
    assert [match.pattern_id for match in matches] == ["courier_apk"]
    assert "Never install a file" in matches[0].warning
    assert matches[0].matched_trigger == "apk"
    assert match_fraud_patterns("halo, cek harga dong") == []
    assert match_fraud_patterns("") == []


def test_match_fraud_patterns_deduplicates_per_pattern():
    from puspa.graphs.fraud_patterns import match_fraud_patterns

    matches = match_fraud_patterns("apk apk apk, paket kurir")
    assert len(matches) == 1
    assert matches[0].pattern_id == "courier_apk"


def test_classify_flag_reason_tags_free_text():
    from puspa.graphs.fraud_patterns import classify_flag_reason

    assert classify_flag_reason("seller asked me to install an apk") == ["courier_apk"]
    assert classify_flag_reason("slow shipping") == []
