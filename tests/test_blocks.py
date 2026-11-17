"""Tes pustaka blok (§2.12): 15 blok tetap, skema, batas MVP."""

import pytest

from puspa.workflow.blocks import (
    BLOK, BlockError, items_block, definisi_block, kedalaman_cabang,
    periksa_workflow, periksa_step, periksa_masukan,
)

BLOCK_WAJIB = (
    "check_bill", "compare_prices", "watch_prices", "search_email",
    "read_email", "create_draft", "create_event", "prepare_payment",
    "send_notification", "ask_user", "wait_for_approval", "condition",
    "for_each", "save_memory", "save_to_module_table",
)


def test_pustaka_tepat_15_block():
    assert set(items_block()) == set(BLOCK_WAJIB)
    assert len(BLOK) == 15


def test_each_block_punya_skema_klass_and_permission():
    for name in BLOCK_WAJIB:
        d = definisi_block(name)
        assert d["masukan"] and d["output"], name
        assert d["permission_class"] in ("Pembaca", "Pelaksana", "Pemeriksa"), name
        assert set(d["permission"]) == {"connectors", "scope", "domain", "batas_belanja"}, name


def test_block_read_email_past_pembaca():
    assert definisi_block("read_email")["permission_class"] == "Pembaca"
    assert definisi_block("search_email")["permission"]["connectors"] == ["gmail"]
    assert definisi_block("read_email")["permission"]["scope"] == ["gmail.readonly"]


def test_block_action_logout_hanya_create_event_and_siapkan_pay():
    logout = {n for n, d in BLOK.items() if d["action_logout"]}
    assert logout == {"create_event", "prepare_payment"}


def test_block_not_dikenal_rejected():
    with pytest.raises(BlockError):
        definisi_block("send_whatsapp")
    assert periksa_step({"block": "send_whatsapp", "masukan": {}})


def test_periksa_masukan_less_field():
    assert periksa_masukan("check_bill", {"provider": "PLN"}) == ["number_pelanggan"]
    assert periksa_masukan("check_bill",
                            {"provider": "PLN", "number_pelanggan": "1"}) == []


def test_batas_maks_10_step():
    step = [{"block": "send_notification", "masukan": {"message": "x"}}] * 11
    masalah = periksa_workflow(step)
    assert any("maks 10" in m for m in masalah)


def test_batas_maks_2_level_percabangan():
    dalam = {"block": "send_notification", "masukan": {"message": "x"}}
    tiga = {"block": "condition", "masukan": {"ungkapan": "a == b"},
            "maka": [{"block": "condition", "masukan": {"ungkapan": "a == b"},
                      "maka": [{"block": "condition", "masukan": {"ungkapan": "a == b"},
                                "maka": [dalam], "kalau_not": []}],
                      "kalau_not": []}],
            "kalau_not": []}
    assert kedalaman_cabang([tiga]) == 3
    assert any("maks 2" in m for m in periksa_workflow([tiga]))


def test_cabang_2_level_passed():
    dalam = {"block": "send_notification", "masukan": {"message": "x"}}
    dua = {"block": "condition", "masukan": {"ungkapan": "a == b"},
           "maka": [{"block": "condition", "masukan": {"ungkapan": "a == b"},
                     "maka": [dalam], "kalau_not": []}],
           "kalau_not": []}
    assert kedalaman_cabang([dua]) == 2
    assert periksa_workflow([dua]) == []


def test_for_each_maks_20_item_dicek_di_runner():
    step = {"block": "for_each", "masukan": {"items": list(range(21))},
               "step_dalam": [{"block": "send_notification", "masukan": {"message": "x"}}]}
    # Skema langkah lolos (batas item dicek saat jalan, bukan saat susun).
    assert periksa_step(step) == []
