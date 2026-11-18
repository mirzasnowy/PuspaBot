"""Tes Graf Identitas dan Harga (§3.2 revisi 7)."""

import pytest

from puspa.graphs import identity as graf


def test_normalization_number_model_deterministic():
    assert graf.normalize_model_number("sm-s921b") == "SMS921B"
    assert graf.normalize_model_number("SM S921-B") == "SMS921B"
    assert graf.normalize_model_number("MQKP3ID/A") == "MQKP3IDA"


def test_normalization_variant_only_key_price():
    assert graf.normalize_variant({"storage": "256GB", "color": "Hitam", "berat": "200g"}) == {
        "storage": "256gb",
        "color": "hitam",
    }
    assert graf.normalize_variant(None) == {}


def test_add_and_search_product(make_user, test_dsn):
    make_user()
    product = graf.add_product(
        brand="Samsung",
        series="Galaxy S24",
        model_number="SM-S921B",
        category="phone",
        variant={"storage": "256GB"},
        dsn=test_dsn,
    )
    assert product["model_number"] == "SMS921B"
    assert product["variant"] == {"storage": "256gb"}
    # Idempotent: tambah lagi -> produk yang sama.
    lagi = graf.add_product(
        brand="Samsung", model_number="sm-s921b", category="phone", dsn=test_dsn
    )
    assert lagi["id"] == product["id"]
    assert graf.search_product("SM-S921B", dsn=test_dsn)["id"] == product["id"]
    assert graf.search_product("TIDAK-ADA", dsn=test_dsn) is None


def test_category_outside_scope_rejected(make_user, test_dsn):
    make_user()
    with pytest.raises(graf.IdentityError):
        graf.add_product(brand="Nike", model_number="AIRMAX90", category="fashion", dsn=test_dsn)


def test_link_status_and_user_confirmation(make_user, test_dsn):
    user = make_user()
    product = graf.add_product(
        brand="Samsung", model_number="SM-S921B", category="phone", dsn=test_dsn
    )
    tautan = graf.link_listing(
        product_id=product["id"],
        user_id=user["user_id"],
        marketplace="Tokopedia",
        listing_url="https://www.tokopedia.com/toko/x",
        dsn=test_dsn,
    )
    assert tautan["status"] == "automatic"
    dikonfirmasi = graf.confirm_link(
        tautan["id"], user_id=user["user_id"], accepted=True, dsn=test_dsn
    )
    assert dikonfirmasi["status"] == "user_confirmed"
    rejected = graf.confirm_link(
        tautan["id"], user_id=user["user_id"], accepted=False, dsn=test_dsn
    )
    assert rejected["status"] == "user_rejected"
    with pytest.raises(KeyError):
        graf.confirm_link(
            "00000000-0000-0000-0000-000000000000",
            user_id=user["user_id"],
            accepted=True,
            dsn=test_dsn,
        )


def test_price_without_evidence_rejected(make_user, test_dsn):
    user = make_user()
    product = graf.add_product(
        brand="Samsung", model_number="SM-S921B", category="phone", dsn=test_dsn
    )
    tautan = graf.link_listing(
        product_id=product["id"],
        user_id=user["user_id"],
        marketplace="Shopee",
        listing_url="https://shopee.co.id/x",
        dsn=test_dsn,
    )
    with pytest.raises(graf.IdentityError):
        graf.record_price(
            link_id=tautan["id"],
            user_id=user["user_id"],
            price=12000000,
            evidence_id="",
            dsn=test_dsn,
        )


def test_price_latest_and_history_30_day(make_user, test_dsn):
    user = make_user()
    product = graf.add_product(
        brand="Samsung", model_number="SM-S921B", category="phone", dsn=test_dsn
    )
    for marketplace, url, price in (
        ("Tokopedia", "https://www.tokopedia.com/toko/a", 12999000),
        ("Shopee", "https://shopee.co.id/a", 12499000),
        ("Blibli", "https://www.blibli.com/p/a", 12750000),
    ):
        tautan = graf.link_listing(
            product_id=product["id"],
            user_id=user["user_id"],
            marketplace=marketplace,
            listing_url=url,
            dsn=test_dsn,
        )
        graf.record_price(
            link_id=tautan["id"],
            user_id=user["user_id"],
            price=price,
            shipping=15000,
            store_rating=4.9,
            evidence_id=f"bukti-{marketplace}",
            dsn=test_dsn,
        )
    terkini = graf.current_prices(product["id"], dsn=test_dsn)
    assert {t["marketplace"]: t["price"] for t in terkini} == {
        "Tokopedia": 12999000.0,
        "Shopee": 12499000.0,
        "Blibli": 12750000.0,
    }
    assert all(t["evidence_id"] for t in terkini)
    history = graf.history_price(product["id"], day=30, dsn=test_dsn)
    assert len(history) == 3
    assert all(r["evidence_id"] for r in history)


def test_match_automatic_without_guess(make_user, test_dsn):
    make_user()
    graf.add_product(
        brand="Samsung",
        series="Galaxy S24",
        model_number="SM-S921B",
        category="phone",
        dsn=test_dsn,
    )
    match = graf.match_automatic(model_number="SM-S921B", dsn=test_dsn)
    assert match["match"] is not None and match["perlu_ask"] is False
    ragu = graf.match_automatic(model_number="SM-S999Z", brand="Samsung", dsn=test_dsn)
    assert ragu["match"] is None and ragu["perlu_ask"] is True
    # Kandidat ditampilkan, bukan dipilih diam-diam.
    assert isinstance(ragu["kandidat"], list)
