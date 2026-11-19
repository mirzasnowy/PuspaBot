"""Tests for the Check Before You Buy slice (ticket B1).

All price data is recorded (seeded store / cassette); nothing here
touches the network, and live mode is tested as held, not executed.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from puspa.check_before_buy import (
    AccountRecord,
    PriceObservation,
    RecordStore,
    SellerRecord,
    check_before_buy,
    classify_input,
    handle_check_before_buy,
    load_cassette,
)
from puspa.check_before_buy.answer import mask_account_number
from puspa.turns.intercepts import INTERCEPTED_TOOLS, InterceptedTools

LINK = "https://www.tokopedia.com/toko/galaxy-s24"
KEY = "https://www.tokopedia.com/toko/galaxy-s24"


def _seeded_store() -> RecordStore:
    store = RecordStore()
    store.add_price_observation(
        PriceObservation(
            product_key=KEY,
            source="Tokopedia",
            price=12999000,
            source_url="https://www.tokopedia.com/toko/galaxy-s24",
            excerpt="[DATA: Galaxy S24 Rp12.999.000]",
        )
    )
    store.add_price_observation(
        PriceObservation(
            product_key=KEY,
            source="Shopee",
            price=12499000,
            source_url="https://shopee.co.id/galaxy-s24",
            excerpt="[DATA: Galaxy S24 Rp12.499.000]",
        )
    )
    store.add_seller_record(
        SellerRecord(
            name="Gadget Store", flagged=False, reports=12, note="no adverse reports on record"
        )
    )
    store.add_seller_record(
        SellerRecord(
            name="Murah Banget Shop",
            flagged=True,
            flag_reason="three recorded reports of payment taken with no goods shipped",
            reports=3,
        )
    )
    store.add_account_record(
        AccountRecord(number="1234567890", bank="BCA", status_note="no adverse reports on record")
    )
    store.add_account_record(
        AccountRecord(
            number="9876543210",
            bank="Mandiri",
            flagged=True,
            flag_reason="two recorded reports of fraud linked to this account",
        )
    )
    return store


def _link_text(seller: str = "Gadget Store") -> str:
    return f"{LINK} toko: {seller}"


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def test_classify_product_link():
    result = classify_input(LINK)
    assert result.kind == "product_link"
    assert result.product_url == LINK


def test_classify_seller_chat_with_link_and_account():
    text = (
        "Seller: Gadget Store\n"
        "Buyer: kak, ready?\n"
        "Seller: ready kak, transfer ke BCA 1234567890 ya\n"
        f"{LINK}"
    )
    result = classify_input(text)
    assert result.kind == "seller_chat"
    assert result.product_url == LINK
    assert result.account_number == "1234567890"
    assert result.seller_name == "Gadget Store"


def test_classify_account_number_only():
    result = classify_input("Please transfer to BCA 1234567890")
    assert result.kind == "account_number"
    assert result.account_number == "1234567890"


def test_classify_unknown_input():
    result = classify_input("hello, how are you?")
    assert result.kind == "unknown"
    assert result.product_url is None
    assert result.account_number is None


def test_price_is_never_mistaken_for_account_number():
    result = classify_input("The price is Rp12.499.000, is that fair?")
    assert result.account_number is None
    assert result.kind == "unknown"


def test_seller_name_is_never_guessed_from_url():
    result = classify_input(LINK)
    assert result.seller_name is None


def test_mask_account_number():
    assert mask_account_number("1234567890") == "12******90"


# ---------------------------------------------------------------------------
# fair price: evidence rules
# ---------------------------------------------------------------------------


def test_fair_price_verified_from_two_sources_median():
    answer = check_before_buy(_link_text(), store=_seeded_store())
    assert answer.fair_price.status == "verified"
    assert answer.fair_price.value == 12749000.0
    assert answer.fair_price.sources == ["Shopee", "Tokopedia"]
    assert "fair_price" not in answer.cannot_verify


def test_fair_price_uncertain_with_one_source():
    store = RecordStore()
    store.add_price_observation(
        PriceObservation(
            product_key=KEY,
            source="Tokopedia",
            price=12999000,
            source_url="https://www.tokopedia.com/toko/galaxy-s24",
        )
    )
    answer = check_before_buy(LINK, store=store)
    assert answer.fair_price.status == "uncertain"
    assert answer.fair_price.value == 12999000.0


def test_missing_price_is_cannot_verify_with_no_number():
    answer = check_before_buy(_link_text(), store=RecordStore())
    assert answer.fair_price.status == "cannot_verify"
    assert answer.fair_price.value is None
    assert "fair_price" in answer.cannot_verify


def test_missing_seller_and_account_are_cannot_verify_not_guessed():
    answer = check_before_buy(LINK, store=_seeded_store())
    assert answer.seller.status == "cannot_verify"
    assert answer.seller.name is None
    assert answer.account.status == "not_applicable"


# ---------------------------------------------------------------------------
# advice
# ---------------------------------------------------------------------------


def test_act_when_price_verified_and_seller_clean():
    answer = check_before_buy(_link_text(), store=_seeded_store())
    assert answer.advice == "act"
    assert answer.seller.status == "recorded_clean"


def test_do_not_act_when_seller_flagged():
    answer = check_before_buy(_link_text("Murah Banget Shop"), store=_seeded_store())
    assert answer.advice == "do_not_act"
    assert answer.seller.status == "flagged"


def test_do_not_act_when_account_flagged():
    answer = check_before_buy("transfer to 9876543210", store=_seeded_store())
    assert answer.input_kind == "account_number"
    assert answer.account.status == "flagged"
    assert answer.account.masked_number == "98******10"
    assert answer.advice == "do_not_act"


def test_act_for_clean_account_only_input():
    answer = check_before_buy("transfer to BCA 1234567890", store=_seeded_store())
    assert answer.account.status == "recorded_clean"
    assert answer.advice == "act"


def test_cannot_advise_when_account_unknown():
    answer = check_before_buy("transfer to 1111222233", store=_seeded_store())
    assert answer.account.status == "cannot_verify"
    assert answer.account.masked_number == "11******33"
    assert answer.advice == "cannot_advise"


def test_cannot_advise_for_unknown_input():
    answer = check_before_buy("hello, how are you?", store=_seeded_store())
    assert answer.advice == "cannot_advise"
    assert "fair_price" in answer.cannot_verify


def test_reply_text_states_gaps_and_no_send():
    answer = check_before_buy(LINK, store=RecordStore())
    assert "could not verify" in answer.text
    assert "did not send any message or money" in answer.text


# ---------------------------------------------------------------------------
# live-execution hold (decision 15)
# ---------------------------------------------------------------------------


def test_live_mode_is_held_and_reads_nothing():
    answer = check_before_buy(_link_text(), store=_seeded_store(), mode="live")
    assert answer.live_execution_held is True
    assert answer.fair_price.status == "cannot_verify"
    assert answer.fair_price.value is None
    assert answer.advice == "cannot_advise"
    assert answer.cannot_verify == ["fair_price", "seller", "account"]


def test_slice_has_no_network_or_pay_path():
    package = Path(__file__).resolve().parent.parent / "puspa" / "check_before_buy"
    forbidden = ("httpx", "requests", "urllib", "socket", "subprocess", "playwright")
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{token} in {path.name}"
        assert "def send" not in source
        assert "def pay" not in source


# ---------------------------------------------------------------------------
# cassette
# ---------------------------------------------------------------------------


def test_load_sample_cassette():
    cassette = Path(__file__).resolve().parent.parent / "cassettes" / "check_before_buy_sample.json"
    store = load_cassette(cassette)
    answer = check_before_buy(_link_text(), store=store)
    assert answer.fair_price.status == "verified"
    assert answer.fair_price.value == 12749000.0
    assert answer.advice == "act"


# ---------------------------------------------------------------------------
# outcome recording: existing evidence + ledger paths
# ---------------------------------------------------------------------------


def test_outcome_writes_evidence_rows_and_ledger_event(make_user, test_dsn):
    user = make_user()
    result = handle_check_before_buy(
        {"text": _link_text()},
        store=_seeded_store(),
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        task_id="task-b1-1",
        dsn=test_dsn,
    )
    assert result["recorded"] is True
    assert len(result["evidence_ids"]) == 2
    assert result["answer"]["advice"] == "act"
    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM evidence WHERE task_id = 'task-b1-1'")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT type, content FROM events WHERE task_id = 'task-b1-1'")
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "evidence_recorded"
    assert rows[0][1]["slice"] == "check_before_buy"
    assert rows[0][1]["advice"] == "act"
    assert rows[0][1]["fair_price"]["status"] == "verified"


def test_outcome_without_dsn_computes_only(make_user):
    result = handle_check_before_buy(
        {"text": _link_text()},
        store=_seeded_store(),
        user_id=make_user()["user_id"],
    )
    assert result["recorded"] is False
    assert result["evidence_ids"] == []
    assert result["answer"]["advice"] == "act"


def test_handle_rejects_empty_text():
    with pytest.raises(ValueError, match="non-empty 'text'"):
        handle_check_before_buy({"text": "   "}, store=_seeded_store())


# ---------------------------------------------------------------------------
# turn-loop wiring (intercepted tool)
# ---------------------------------------------------------------------------


def test_check_before_buy_is_an_intercepted_tool():
    assert "check_before_buy" in INTERCEPTED_TOOLS


def test_intercepted_tool_answers_and_records(make_user, test_dsn):
    user = make_user()
    tools = InterceptedTools(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        check_store=_seeded_store(),
        check_dsn=test_dsn,
    )
    result = tools.try_intercept("check_before_buy", {"text": _link_text()}, task_id="task-b1-2")
    assert result is not None
    assert result["intercepted"] is True
    assert result["result"]["answer"]["advice"] == "act"
    assert result["result"]["recorded"] is True
    assert len(result["result"]["evidence_ids"]) == 2


def test_intercepted_tool_without_store_cannot_verify():
    tools = InterceptedTools(user_id="user-1", agent_id="puspa")
    result = tools.try_intercept("check_before_buy", {"text": _link_text()}, task_id="task-b1-3")
    assert result["result"]["answer"]["advice"] == "cannot_advise"
    assert result["result"]["answer"]["fair_price"]["status"] == "cannot_verify"
    assert result["result"]["recorded"] is False


def test_intercepted_tool_empty_text_is_error_result_not_crash():
    tools = InterceptedTools(user_id="user-1", agent_id="puspa")
    result = tools.try_intercept("check_before_buy", {"text": ""}, task_id="task-b1-4")
    assert result["intercepted"] is True
    assert "error" in result


def test_unknown_tool_still_passes_through():
    tools = InterceptedTools(user_id="user-1", agent_id="puspa")
    assert tools.try_intercept("not_a_tool", {"text": "x"}) is None


# ---------------------------------------------------------------------------
# local scam patterns (15.5) and Rupiah display (15.6), B14
# ---------------------------------------------------------------------------


def test_forwarded_chat_matching_scam_pattern_is_do_not_act():
    text = (
        "seller: kak, paket kurirnya sudah jalan, tolong unduh apk ini buat cek resi ya\nbuyer: oke"
    )
    answer = check_before_buy(text, store=_seeded_store())
    assert answer.advice == "do_not_act"
    assert len(answer.fraud_patterns) == 1
    assert answer.fraud_patterns[0]["pattern_id"] == "courier_apk"
    assert "courier package with an APK file" in answer.text
    assert "Never install a file" in answer.text


def test_wrong_transfer_pattern_is_do_not_act_and_named():
    text = "halo kak, saya salah transfer ke rekening kakak, tolong kembalikan ya"
    answer = check_before_buy(text, store=RecordStore())
    assert answer.advice == "do_not_act"
    assert any(entry["pattern_id"] == "wrong_transfer_refund" for entry in answer.fraud_patterns)
    assert "wrong transfer" in answer.text


def test_clean_forward_has_no_fraud_patterns():
    answer = check_before_buy(LINK, store=_seeded_store())
    assert answer.fraud_patterns == []
    assert "Warning" not in answer.text


def test_fraud_patterns_ride_in_answer_dict():
    text = "mama minta pulsa dong, ini mama"
    data = check_before_buy(text, store=RecordStore()).to_dict()
    assert data["fraud_patterns"][0]["pattern_id"] == "mama_minta_pulsa"
    assert data["advice"] == "do_not_act"


def test_reply_renders_rupiah_with_dot_thousands():
    answer = check_before_buy(LINK, store=_seeded_store())
    assert answer.fair_price.value == 12_749_000
    assert "Rp12.749.000" in answer.text
    assert "Rp12,749,000" not in answer.text
