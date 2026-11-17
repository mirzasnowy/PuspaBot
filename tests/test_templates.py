"""Tes templat bawaan (§2.12) + tugas acuan 8 dan 9 sebagai alur."""

import pytest

from puspa.workflow.blocks import periksa_workflow
from puspa.workflow.runner import (
    aktifkan_workflow, jalankan_workflow, dry_run,
)
from puspa.workflow.templates import (
    TASK_REFERENCE_WORKFLOW, take_templat, create_dari_templat, items_templat,
)
from puspa.workflow.triggers import validasi_trigger


def test_tepat_3_templat_bawaan():
    templat = items_templat()
    assert len(templat) == 3
    assert {t["key"] for t in templat} == {
        "bill_monthly", "watch_prices", "rekap_bill_weekly"}


def test_all_templat_passed_validasi():
    for t in items_templat():
        assert validasi_trigger(t["triggers"]) == [], t["key"]
        assert periksa_workflow(t["step"]) == [], t["key"]


def test_task_reference_8_and_9_memetakan_to_templat():
    assert TASK_REFERENCE_WORKFLOW[8] == "bill_monthly"
    assert TASK_REFERENCE_WORKFLOW[9] == "rekap_bill_weekly"


def _aktifkan(make_user, test_dsn, permission_user_penuh, key, trigger=None):
    u = make_user()
    user_id, agent_id = u["user_id"], u["agent_id"]
    workflow = create_dari_templat(key, user_id=user_id, agent_id=agent_id,
                             trigger=trigger, dsn=test_dsn)
    dry = dry_run(workflow["id"], user_id=user_id, dsn=test_dsn)
    assert dry["success"] is True, dry
    aktifkan_workflow(workflow["id"], user_id=user_id, permission_user=permission_user_penuh,
                  result_dry=dry, dsn=test_dsn)
    return user_id, workflow


def test_task_reference_8_reminder_bill_sebagai_workflow(make_user, test_dsn,
                                                     permission_user_penuh):
    # Tugas 8: "Buatkan pengingat untuk tagihan." — pemicu manual.
    user_id, workflow = _aktifkan(make_user, test_dsn, permission_user_penuh,
                              "bill_monthly", trigger={"type": "manual"})
    result = jalankan_workflow(workflow["id"], user_id=user_id, trigger_jenis="manual",
                          dsn=test_dsn)
    # Berhenti di siapkan bayar (aksi keluar -> antrean persetujuan).
    assert result["status"] == "pending_approval"
    check = [l for l in result["step_results"] if l["block"] == "check_bill"]
    assert len(check) == 2
    assert check[0]["result"]["amount"] == 132500  # PLN fixture
    assert check[1]["result"]["amount"] == 87500   # PDAM fixture
    assert any(l["block"] == "send_notification" and l["success"]
               for l in result["step_results"])


def test_task_reference_9_rekap_weekly_sebagai_workflow(make_user, test_dsn,
                                                  permission_user_penuh):
    # Tugas 9: "Ingatkan semua tagihan yang jatuh tempo minggu ini."
    user_id, workflow = _aktifkan(make_user, test_dsn, permission_user_penuh,
                              "rekap_bill_weekly",
                              trigger={"type": "manual"})
    result = jalankan_workflow(workflow["id"], user_id=user_id, trigger_jenis="manual",
                          dsn=test_dsn)
    assert result["success"] is True, result
    check = [l for l in result["step_results"] if l["block"] == "check_bill"]
    assert len(check) == 4  # PLN, PDAM, pulsa, paket data
    amount = {l["result"]["amount"] for l in check}
    assert amount == {132500, 87500, 50000, 75000}
    assert result["step_results"][-1]["block"] == "send_notification"


def test_templat_watch_price_tertahan_gerbang_tos(make_user, test_dsn):
    # Jujur: pantau harga live ditahan keputusan 15 (ToS). Uji kering
    # templat ini GAGAL TERLIHAT dengan alasan syarat layanan — bukan
    # sukses palsu, bukan upaya HTTP.
    u = make_user()
    user_id, agent_id = u["user_id"], u["agent_id"]
    workflow = create_dari_templat("watch_prices", user_id=user_id, agent_id=agent_id,
                             dsn=test_dsn)
    result = dry_run(workflow["id"], user_id=user_id, dsn=test_dsn)
    assert result["success"] is False
    assert "keputusan 15" in result["reason"]
