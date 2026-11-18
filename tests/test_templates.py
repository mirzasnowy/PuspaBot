"""Tes templat bawaan (§2.12) + tugas acuan 8 dan 9 sebagai alur."""

from puspa.workflow.blocks import validate_workflow
from puspa.workflow.runner import (
    activate_workflow,
    dry_run,
    run_workflow,
)
from puspa.workflow.templates import (
    TASK_REFERENCE_WORKFLOW,
    create_from_template,
    list_templates,
)
from puspa.workflow.triggers import validate_trigger


def test_exactly_3_default_templates():
    template = list_templates()
    assert len(template) == 3
    assert {t["key"] for t in template} == {"bill_monthly", "watch_prices", "rekap_bill_weekly"}


def test_all_templates_pass_validation():
    for t in list_templates():
        assert validate_trigger(t["triggers"]) == [], t["key"]
        assert validate_workflow(t["step"]) == [], t["key"]


def test_task_references_8_and_9_map_to_templates():
    assert TASK_REFERENCE_WORKFLOW[8] == "bill_monthly"
    assert TASK_REFERENCE_WORKFLOW[9] == "rekap_bill_weekly"


def _aktifkan(make_user, test_dsn, permission_user_penuh, key, trigger=None):
    u = make_user()
    user_id, agent_id = u["user_id"], u["agent_id"]
    workflow = create_from_template(
        key, user_id=user_id, agent_id=agent_id, trigger=trigger, dsn=test_dsn
    )
    dry = dry_run(workflow["id"], user_id=user_id, dsn=test_dsn)
    assert dry["success"] is True, dry
    activate_workflow(
        workflow["id"],
        user_id=user_id,
        permission_user=permission_user_penuh,
        dry_run_result=dry,
        dsn=test_dsn,
    )
    return user_id, workflow


def test_task_reference_8_reminder_bill_as_workflow(make_user, test_dsn, permission_user_penuh):
    # Tugas 8: "Buatkan pengingat untuk tagihan." — pemicu manual.
    user_id, workflow = _aktifkan(
        make_user, test_dsn, permission_user_penuh, "bill_monthly", trigger={"type": "manual"}
    )
    result = run_workflow(workflow["id"], user_id=user_id, trigger_type="manual", dsn=test_dsn)
    # Berhenti di siapkan bayar (aksi keluar -> antrean persetujuan).
    assert result["status"] == "pending_approval"
    check = [
        step_entry for step_entry in result["step_results"] if step_entry["block"] == "check_bill"
    ]
    assert len(check) == 2
    assert check[0]["result"]["amount"] == 132500  # PLN fixture
    assert check[1]["result"]["amount"] == 87500  # PDAM fixture
    assert any(
        step_entry["block"] == "send_notification" and step_entry["success"]
        for step_entry in result["step_results"]
    )


def test_task_reference_9_recap_weekly_as_workflow(make_user, test_dsn, permission_user_penuh):
    # Tugas 9: "Ingatkan semua tagihan yang jatuh tempo minggu ini."
    user_id, workflow = _aktifkan(
        make_user, test_dsn, permission_user_penuh, "rekap_bill_weekly", trigger={"type": "manual"}
    )
    result = run_workflow(workflow["id"], user_id=user_id, trigger_type="manual", dsn=test_dsn)
    assert result["success"] is True, result
    check = [
        step_entry for step_entry in result["step_results"] if step_entry["block"] == "check_bill"
    ]
    assert len(check) == 4  # PLN, PDAM, pulsa, paket data
    amount = {step_entry["result"]["amount"] for step_entry in check}
    assert amount == {132500, 87500, 50000, 75000}
    assert result["step_results"][-1]["block"] == "send_notification"


def test_watch_price_template_held_by_tos_gate(make_user, test_dsn):
    # Jujur: pantau harga live ditahan keputusan 15 (ToS). Uji kering
    # templat ini GAGAL TERLIHAT dengan alasan syarat layanan — bukan
    # sukses palsu, bukan upaya HTTP.
    u = make_user()
    user_id, agent_id = u["user_id"], u["agent_id"]
    workflow = create_from_template(
        "watch_prices", user_id=user_id, agent_id=agent_id, dsn=test_dsn
    )
    result = dry_run(workflow["id"], user_id=user_id, dsn=test_dsn)
    assert result["success"] is False
    assert "keputusan 15" in result["reason"]
