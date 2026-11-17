"""Tes metrik alur (§3.8) + panel Alur di PWA."""

from puspa.workflow.composer import card_step
from puspa.workflow.metrics import metrik_workflow
from puspa.workflow.panel import result_jalan_html, card_step_html, panel_workflow_html
from puspa.workflow.runner import aktifkan_workflow, create_workflow, jalankan_workflow, dry_run
from puspa.workflow.templates import create_dari_templat


def test_metrik_workflow_start_tertunda():
    m = metrik_workflow()
    assert m["user_dengan_workflow_active"] == 0
    assert m["status_user_active"] == "tertunda"
    assert "pengguna uji" in m["reason_user_active"]
    assert m["jalan_total"] == 0
    assert m["persen_jalan_without_campur"] is None


def test_metrik_workflow_menghitung_jalan_without_campur(make_user, test_dsn,
                                                  permission_user_penuh):
    u = make_user()
    user_id, agent_id = u["user_id"], u["agent_id"]
    workflow = create_dari_templat("rekap_bill_weekly", user_id=user_id,
                             agent_id=agent_id, dsn=test_dsn)
    dry = dry_run(workflow["id"], user_id=user_id, dsn=test_dsn)
    aktifkan_workflow(workflow["id"], user_id=user_id, permission_user=permission_user_penuh,
                  result_dry=dry, dsn=test_dsn)
    jalankan_workflow(workflow["id"], user_id=user_id, dsn=test_dsn)
    m = metrik_workflow(dsn=test_dsn)
    assert m["user_dengan_workflow_active"] == 1
    # 2 jalan tercatat: 1 uji kering + 1 jalan nyata — keduanya selesai.
    assert m["jalan_total"] == 2
    assert m["jalan_completed_without_campur"] == 2
    assert m["persen_jalan_without_campur"] == 100.0
    assert m["status_user_active"] == "tertunda"  # butuh 5 pengguna uji


def test_panel_card_step_escape_html():
    card = card_step({"block": "send_notification",
                           "masukan": {"message": "<b>hai</b>"},
                           "title": "Ingatkan <aku>"})
    html = card_step_html(card)
    assert "<b>hai</b>" not in html
    assert "&lt;b&gt;hai&lt;/b&gt;" not in html  # pesan tidak dirender di kartu
    assert "Ingatkan &lt;aku&gt;" in html
    assert "send_notification" in html


def test_panel_workflow_and_result_jalan():
    html = panel_workflow_html(
        name="Tagihan bulanan", trigger={"type": "schedule"},
        card=[card_step({"block": "check_bill",
                              "masukan": {"provider": "PLN",
                                          "number_pelanggan": ""},
                              "title": "Cek PLN"})],
        manifest_teks="Konektor: tidak ada", message="Siap diaktifkan.")
    assert "Tagihan bulanan" in html and "check_bill" in html
    assert "Siap diaktifkan." in html
    result = result_jalan_html({
        "status": "completed",
        "step_results": [{"block": "check_bill", "success": True},
                          {"block": "prepare_payment", "success": True,
                           "simulasi": True}],
        "cost_rp": 0, "reason": ""})
    assert "simulasi (uji kering)" in result
    assert "Rp0" in result
