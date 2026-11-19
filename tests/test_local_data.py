"""Local Indonesian data layer (ticket B5): seeded shipping rates,
payment rail naming, and everyday Indonesian language handling,
plus the wiring into the Check Before You Buy answer.
"""

from __future__ import annotations

import json

import pytest

from puspa.check_before_buy import (
    PriceObservation,
    RecordStore,
    check_before_buy,
)
from puspa.local_data import (
    JsonRateLoader,
    RateStore,
    SeedRateLoader,
    UnknownCityError,
    UnknownCourierError,
    detect_rail,
    extract_courier,
    extract_destination_city,
    format_rupiah,
    normalize_id_text,
    parse_rupiah_amount,
    rail_name,
)
from puspa.local_data.rates import SEED_SOURCE_LABEL, normalize_city, normalize_courier

# ---------------------------------------------------------------- rates


def test_seed_quote_known_lane_is_deterministic() -> None:
    store = RateStore()
    first = store.quote(origin="Jakarta", destination="Bandung", courier="JNE")
    second = store.quote(origin="Jakarta", destination="Bandung", courier="JNE")
    assert first == second
    assert first.cost_idr == 12000
    assert first.billed_kg == 1
    assert first.cod_available is True
    assert first.source_label == SEED_SOURCE_LABEL


def test_seed_quote_bills_per_started_kilogram() -> None:
    store = RateStore()
    quote = store.quote(origin="Jakarta", destination="Surabaya", courier="JNE", weight_kg=2.5)
    assert quote.billed_kg == 3
    assert quote.cost_idr == 3 * 20000


def test_seed_quote_exact_kilogram_is_not_rounded_up() -> None:
    store = RateStore()
    quote = store.quote(origin="Jakarta", destination="Bandung", courier="JNE", weight_kg=2.0)
    assert quote.billed_kg == 2
    assert quote.cost_idr == 24000


def test_seed_quote_zero_weight_is_rejected() -> None:
    store = RateStore()
    with pytest.raises(ValueError, match="weight_kg"):
        store.quote(origin="Jakarta", destination="Bandung", courier="JNE", weight_kg=0)


def test_seed_quote_unknown_city_fails_loud() -> None:
    store = RateStore()
    with pytest.raises(UnknownCityError, match="unknown city"):
        store.quote(origin="Jakarta", destination="Palembang", courier="JNE")
    with pytest.raises(UnknownCityError, match="unknown city"):
        store.quote(origin="Palembang", destination="Bandung", courier="JNE")


def test_seed_quote_unknown_courier_fails_loud() -> None:
    store = RateStore()
    with pytest.raises(UnknownCourierError, match="unknown courier"):
        store.quote(origin="Jakarta", destination="Bandung", courier="DHL")


def test_seed_quote_courier_without_lane_rate_fails_loud() -> None:
    # GoSend is a same-city instant courier: no seeded rate to Bandung.
    store = RateStore()
    with pytest.raises(UnknownCourierError, match="no rate on record"):
        store.quote(origin="Jakarta", destination="Bandung", courier="GoSend")


def test_city_and_courier_aliases_normalise() -> None:
    assert normalize_city("bdg") == "Bandung"
    assert normalize_city("jogja") == "Yogyakarta"
    assert normalize_city("JKT") == "Jakarta"
    assert normalize_courier("jnt") == "J&T"
    assert normalize_courier("anter aja") == "AnterAja"
    assert normalize_courier("pos") == "POS Indonesia"


def test_cod_flag_varies_by_lane_and_courier() -> None:
    store = RateStore()
    jne_bandung = store.quote(origin="Jakarta", destination="Bandung", courier="JNE")
    assert jne_bandung.cod_available is True
    pos_bandung = store.quote(origin="Jakarta", destination="Bandung", courier="POS Indonesia")
    assert pos_bandung.cod_available is False
    gosend_jakarta = store.quote(origin="Jakarta", destination="Jakarta", courier="GoSend")
    assert gosend_jakarta.cod_available is False
    anteraja_medan = store.quote(origin="Jakarta", destination="Medan", courier="AnterAja")
    assert anteraja_medan.cod_available is False


def test_cod_couriers_lists_only_cod_lanes() -> None:
    store = RateStore()
    cod = store.cod_couriers(origin="Jakarta", destination="Bandung")
    assert "JNE" in cod
    assert "POS Indonesia" not in cod
    assert "GoSend" not in cod


def test_cheapest_courier_on_a_lane() -> None:
    store = RateStore()
    cheapest = store.cheapest(origin="Jakarta", destination="Bandung")
    assert cheapest.courier == "POS Indonesia"
    assert cheapest.rate_per_kg_idr == 10000


def test_rates_for_lane_lists_every_courier() -> None:
    store = RateStore()
    rows = store.rates_for(origin="Jakarta", destination="Jakarta")
    assert {row.courier for row in rows} == {
        "JNE",
        "J&T",
        "SiCepat",
        "AnterAja",
        "POS Indonesia",
        "Ninja Xpress",
        "GoSend",
        "GrabExpress",
    }


def test_seed_loader_rows_carry_provenance() -> None:
    rows = SeedRateLoader().load()
    assert rows
    assert all(row.source_label == SEED_SOURCE_LABEL for row in rows)
    assert all(row.origin == "Jakarta" for row in rows)
    assert all(row.rate_per_kg_idr > 0 for row in rows)


def test_duplicate_rate_rows_are_rejected() -> None:
    class DuplicateLoader:
        def load(self) -> list:
            rows = SeedRateLoader().load()
            return [*rows, rows[0]]

    with pytest.raises(ValueError, match="duplicate rate row"):
        RateStore(loader=DuplicateLoader())


# ------------------------------------------------------- refresh seam


def test_json_loader_is_the_refresh_seam(tmp_path) -> None:
    path = tmp_path / "rates.json"
    path.write_text(
        json.dumps(
            {
                "rates": [
                    {
                        "origin": "Jakarta",
                        "destination": "Bandung",
                        "courier": "JNE",
                        "rate_per_kg_idr": 13000,
                        "cod_available": False,
                        "source_label": "courier tariff 2026-11",
                        "seeded_at": "2026-11-19",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = RateStore(loader=JsonRateLoader(path))
    quote = store.quote(origin="Jakarta", destination="Bandung", courier="JNE")
    assert quote.cost_idr == 13000
    assert quote.cod_available is False
    assert quote.source_label == "courier tariff 2026-11"


def test_json_loader_rejects_a_bad_row(tmp_path) -> None:
    path = tmp_path / "rates.json"
    path.write_text(
        json.dumps({"rates": [{"origin": "Jakarta", "destination": "Bandung"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing or has a bad field"):
        JsonRateLoader(path).load()


def test_json_loader_rejects_a_non_object_file(tmp_path) -> None:
    path = tmp_path / "rates.json"
    path.write_text(json.dumps({"rates": "not a list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="must hold an object with a 'rates' list"):
        JsonRateLoader(path).load()


def test_json_loader_rejects_a_missing_file(tmp_path) -> None:
    with pytest.raises(ValueError, match="cannot load rate file"):
        JsonRateLoader(tmp_path / "nope.json").load()


# ---------------------------------------------------------------- rails


def test_detect_rail_qris() -> None:
    rail = detect_rail("bayar pake qris ya kak")
    assert rail is not None
    assert rail.rail_id == "qris"
    assert rail.display_name == "QRIS"
    assert rail.kind == "qris"


def test_detect_rail_virtual_account_with_bank() -> None:
    rail = detect_rail("bayar via va bca")
    assert rail is not None
    assert rail.kind == "virtual_account"
    assert rail.bank == "BCA"
    assert rail.display_name == "Virtual account BCA"


def test_detect_rail_virtual_account_without_bank() -> None:
    rail = detect_rail("nanti saya kirim nomor virtual accountnya")
    assert rail is not None
    assert rail.kind == "virtual_account"
    assert rail.bank == ""


def test_detect_rail_bank_transfer_with_bank() -> None:
    rail = detect_rail("tf mandiri aja")
    assert rail is not None
    assert rail.kind == "bank_transfer"
    assert rail.bank == "Mandiri"
    assert rail.display_name == "Bank transfer (Mandiri)"


@pytest.mark.parametrize("text,rail_id", [("bayar gopay", "gopay"), ("pake ovo ya", "ovo")])
def test_detect_rail_wallets(text: str, rail_id: str) -> None:
    rail = detect_rail(text)
    assert rail is not None
    assert rail.rail_id == rail_id
    assert rail.kind == "e_wallet"


def test_detect_rail_dana_needs_payment_context() -> None:
    assert detect_rail("bayar pake dana") is not None
    assert detect_rail("bayar pake dana").rail_id == "dana"
    # "dana" as the ordinary word for funds is not the wallet.
    assert detect_rail("butuh dana buat beli ini") is None


def test_detect_rail_cod() -> None:
    rail = detect_rail("bisa cod ga kak")
    assert rail is not None
    assert rail.rail_id == "cash_on_delivery"
    assert rail.kind == "cash_on_delivery"


def test_detect_rail_no_mention_is_none() -> None:
    assert detect_rail("cek harga iphone 13 berapa kak") is None
    assert detect_rail("") is None


def test_rail_name_returns_display_name() -> None:
    assert rail_name("bayar pake qris") == "QRIS"
    assert rail_name("hello") is None


def test_detect_rail_qris_wins_over_wallet() -> None:
    rail = detect_rail("qris atau gopay boleh")
    assert rail is not None
    assert rail.rail_id == "qris"


# ------------------------------------------------------------- language


def test_normalize_everyday_indonesian_price_check() -> None:
    result = normalize_id_text("cek harga iphone 13, ongkir ke bandung pake jne, bisa cod ga?")
    assert "check price" in result.canonical
    assert "shipping cost" in result.canonical
    assert "cash on delivery" in result.canonical
    assert "ongkir" in result.terms_found
    assert "cod" in result.terms_found


def test_normalize_po_and_dp_terms() -> None:
    result = normalize_id_text("PO 2 minggu, DP 500rb dulu kak")
    assert "pre-order" in result.canonical
    assert "down payment" in result.canonical
    assert "po" in result.terms_found
    assert "dp" in result.terms_found


def test_normalize_drops_address_words() -> None:
    result = normalize_id_text("kak ready ga gan barangnya min")
    assert "kak" not in result.canonical.split()
    assert "gan" not in result.canonical.split()
    assert "min" not in result.canonical.split()
    assert "in stock" in result.canonical


def test_normalize_mixed_indonesian_english() -> None:
    result = normalize_id_text("harga pas ya, no nego, ready stock")
    assert "fixed price" in result.canonical
    assert "negotiable" in result.canonical
    assert "in stock" in result.canonical


def test_normalize_unknown_words_pass_through() -> None:
    result = normalize_id_text("iphone 13 pro max 256gb")
    assert result.canonical == "iphone 13 pro max 256gb"
    assert result.terms_found == ()


def test_extract_destination_city_prefers_ke_city() -> None:
    assert extract_destination_city("ongkir ke bandung berapa kak") == "Bandung"
    assert extract_destination_city("kirim ke Surabaya pake JNE") == "Surabaya"
    assert extract_destination_city("ongkir ke bdg") == "Bandung"


def test_extract_destination_city_falls_back_to_any_city() -> None:
    assert extract_destination_city("toko di Yogyakarta jual ini") == "Yogyakarta"


def test_extract_destination_city_unknown_is_none() -> None:
    assert extract_destination_city("ongkir ke Palembang berapa") is None
    assert extract_destination_city("cek harga iphone") is None


def test_extract_courier() -> None:
    assert extract_courier("pake jne aja") == "JNE"
    assert extract_courier("kirim via sicepat") == "SiCepat"
    assert extract_courier("ongkir ke Bandung") is None


# --------------------------------------------------- B1 answer wiring


def _store_with_price() -> RecordStore:
    store = RecordStore()
    store.add_price_observation(
        PriceObservation(
            product_key="https://tokopedia.com/p/iphone-13",
            source="Tokopedia",
            price=12000000,
            source_url="https://tokopedia.com/p/iphone-13",
        )
    )
    store.add_price_observation(
        PriceObservation(
            product_key="https://tokopedia.com/p/iphone-13",
            source="Shopee",
            price=12500000,
            source_url="https://shopee.co.id/p/iphone-13",
        )
    )
    return store


def test_price_check_quotes_shipping_and_cod() -> None:
    answer = check_before_buy(
        "https://tokopedia.com/p/iphone-13 ongkir ke Bandung pake JNE, bisa COD",
        store=_store_with_price(),
    )
    assert answer.fair_price.status == "verified"
    assert answer.fair_price.value == 12250000
    assert answer.shipping.status == "verified"
    assert answer.shipping.city == "Bandung"
    assert answer.shipping.courier == "JNE"
    assert answer.shipping.cost_idr == 12000
    assert answer.shipping.cod_available is True
    assert answer.shipping.source_label == SEED_SOURCE_LABEL
    assert "Rp12.000" in answer.text
    assert "COD is available" in answer.text
    assert "sample rates" in answer.text


def test_price_check_without_courier_quotes_cheapest_and_names_it() -> None:
    answer = check_before_buy(
        "https://tokopedia.com/p/iphone-13 ongkir ke Bandung",
        store=_store_with_price(),
    )
    assert answer.shipping.status == "verified"
    assert answer.shipping.courier == "POS Indonesia"
    assert answer.shipping.cost_idr == 10000
    assert answer.shipping.cod_available is False
    assert "COD is not available" in answer.text


def test_price_check_names_the_payment_rail() -> None:
    answer = check_before_buy(
        "https://tokopedia.com/p/iphone-13 bayar pake qris ya",
        store=_store_with_price(),
    )
    assert answer.payment_rail is not None
    assert answer.payment_rail.display_name == "QRIS"
    assert answer.payment_rail.kind == "qris"
    assert "QRIS" in answer.text


def test_price_check_va_rail_names_the_bank() -> None:
    answer = check_before_buy(
        "https://tokopedia.com/p/iphone-13 bayar via va BCA",
        store=_store_with_price(),
    )
    assert answer.payment_rail is not None
    assert answer.payment_rail.display_name == "Virtual account BCA"


def test_price_check_without_city_has_no_shipping_section() -> None:
    answer = check_before_buy(
        "https://tokopedia.com/p/iphone-13",
        store=_store_with_price(),
    )
    assert answer.shipping.status == "not_applicable"
    assert answer.shipping.cost_idr is None
    assert answer.payment_rail is None
    assert "Shipping" not in answer.text


def test_price_check_courier_without_lane_rate_is_cannot_verify() -> None:
    answer = check_before_buy(
        "https://tokopedia.com/p/iphone-13 ongkir ke Bandung pake GoSend",
        store=_store_with_price(),
    )
    assert answer.shipping.status == "cannot_verify"
    assert answer.shipping.cost_idr is None
    assert "shipping" in answer.cannot_verify
    assert "no rate on record" in answer.shipping.reason


def test_everyday_indonesian_input_is_understood() -> None:
    answer = check_before_buy(
        "kak cek harga https://tokopedia.com/p/iphone-13 dong, ongkir ke bdg pake jne bisa cod ga",
        store=_store_with_price(),
    )
    assert answer.fair_price.status == "verified"
    assert answer.shipping.status == "verified"
    assert answer.shipping.city == "Bandung"
    assert answer.payment_rail is not None
    assert answer.payment_rail.rail_id == "cash_on_delivery"


def test_answer_dict_carries_shipping_and_rail() -> None:
    answer = check_before_buy(
        "https://tokopedia.com/p/iphone-13 ongkir ke Bandung pake JNE bayar qris",
        store=_store_with_price(),
    )
    data = answer.to_dict()
    assert data["shipping"]["status"] == "verified"
    assert data["shipping"]["cost_idr"] == 12000
    assert data["shipping"]["cod_available"] is True
    assert data["payment_rail"]["rail_id"] == "qris"


# ---------------------------------------------------------------------------
# money amounts as users write them (15.1, B14)
# ---------------------------------------------------------------------------


def test_parse_rupiah_amount_numeric_forms() -> None:
    assert parse_rupiah_amount("Rp1.250.000") == 1_250_000
    assert parse_rupiah_amount("Rp 1.250.000,50") == 1_250_000
    assert parse_rupiah_amount("Rp1250000") == 1_250_000
    assert parse_rupiah_amount("harganya 25.000") == 25_000
    assert parse_rupiah_amount("Rp25.000") == 25_000


def test_parse_rupiah_amount_shorthand_suffixes() -> None:
    assert parse_rupiah_amount("harganya 50rb") == 50_000
    assert parse_rupiah_amount("50k") == 50_000
    assert parse_rupiah_amount("1,5 jt") == 1_500_000
    assert parse_rupiah_amount("1.5 jt") == 1_500_000
    assert parse_rupiah_amount("2 juta") == 2_000_000
    assert parse_rupiah_amount("2jt") == 2_000_000
    assert parse_rupiah_amount("500 ribu") == 500_000
    assert parse_rupiah_amount("1.500 rb") == 1_500_000


def test_parse_rupiah_amount_number_words() -> None:
    assert parse_rupiah_amount("seratus ribu") == 100_000
    assert parse_rupiah_amount("dua jeti") == 2_000_000
    assert parse_rupiah_amount("satu juta lima ratus ribu") == 1_500_000
    assert parse_rupiah_amount("dua ratus lima puluh ribu") == 250_000
    assert parse_rupiah_amount("lima belas ribu") == 15_000
    assert parse_rupiah_amount("seribu") == 1_000
    assert parse_rupiah_amount("sejuta") == 1_000_000


def test_parse_rupiah_amount_never_guesses() -> None:
    assert parse_rupiah_amount("no amount here") is None
    assert parse_rupiah_amount("akun 1234567890") is None
    assert parse_rupiah_amount("50 barang") is None
    assert parse_rupiah_amount("") is None
    assert parse_rupiah_amount("Rp0") is None


def test_format_rupiah_uses_dot_thousands() -> None:
    assert format_rupiah(1_250_000) == "Rp1.250.000"
    assert format_rupiah(50_000) == "Rp50.000"
    assert format_rupiah(0) == "Rp0"
    with pytest.raises(ValueError):
        format_rupiah(-1)
    with pytest.raises(ValueError):
        format_rupiah(12.5)  # type: ignore[arg-type]
