"""Tes Skor Keandalan (§3.4) + Lapor Salah (§3.8 revisi 7)."""

import psycopg

from puspa.moat import reliability, report


def _content_pembacaan(test_dsn, user_id, sumber, category, verified, total):
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            for i in range(total):
                vonis = "Verified" if i < verified else "Uncertain"
                cur.execute(
                    "INSERT INTO source_readings (user_id, sumber, category, vonis)"
                    " VALUES (%s, %s, %s, %s)", (user_id, sumber, category, vonis))
        conn.commit()


def test_skor_sumber_persen_and_umur(make_user, test_dsn):
    user = make_user()
    _content_pembacaan(test_dsn, user["user_id"], "Shopee", "TV", 46, 50)
    skor = reliability.skor_sumber("Shopee", "TV", dsn=test_dsn)
    assert skor["persen_verified"] == 92.0
    assert skor["amount"] == 50
    assert skor["turunkan_vonis"] is False
    assert skor["umur_pembacaan_terakhir_detik"] is not None
    label = reliability.label_panel("Shopee", "TV", dsn=test_dsn)
    assert "92.0% terverifikasi dari 50 pembacaan terakhir" in label


def test_skor_sumber_di_bawah_80_turunkan_vonis(make_user, test_dsn):
    user = make_user()
    _content_pembacaan(test_dsn, user["user_id"], "Blibli", "phone", 30, 50)
    skor = reliability.skor_sumber("Blibli", "phone", dsn=test_dsn)
    assert skor["persen_verified"] == 60.0
    assert skor["turunkan_vonis"] is True


def test_skor_sumber_without_pembacaan(make_user, test_dsn):
    make_user()
    skor = reliability.skor_sumber("Tokopedia", "phone", dsn=test_dsn)
    assert skor["persen_verified"] is None
    assert skor["amount"] == 0
    assert "belum ada pembacaan" in reliability.label_panel("Tokopedia", "phone", dsn=test_dsn)


def test_skor_prosedur_dari_jalan(make_user, test_dsn):
    from puspa.graphs import service as graf

    user = make_user()
    prosedur = graf.tulis_prosedur(user_id=user["user_id"], service="PLN",
                                   step=[{"step": "read"}], agent_id=user["agent_id"],
                                   dsn=test_dsn)
    skor = reliability.skor_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)
    assert skor["persen_verified"] == 100.0  # 1 jalan sukses saat tulis
    graf.record_jalan(prosedur["id"], user["user_id"], False, agent_id=user["agent_id"], dsn=test_dsn)
    skor = reliability.skor_prosedur(prosedur["id"], user["user_id"], dsn=test_dsn)
    assert skor["persen_verified"] == 50.0
    assert skor["turunkan_vonis"] is True


def test_report_wrong_menurunkan_skor_and_dicatat_ledger(make_user, test_dsn, ledger):
    user = make_user()
    _content_pembacaan(test_dsn, user["user_id"], "Shopee", "phone", 50, 50)
    assert reliability.skor_sumber("Shopee", "phone", dsn=test_dsn)["persen_verified"] == 100.0
    result = report.report_wrong(
        evidence_id="bukti-1", user_id=user["user_id"], reason="harga salah",
        sumber="Shopee", category="phone", task_id="tugas-lapor-1",
        agent_id=user["agent_id"], dsn=test_dsn, ledger=ledger)
    assert result["reread_requested"] is True
    # Satu laporan "Tidak bisa dipastikan" masuk 50 terakhir -> 49/50 = 98%.
    assert reliability.skor_sumber("Shopee", "phone", dsn=test_dsn)["persen_verified"] == 98.0
    items = report.items_laporan("bukti-1", dsn=test_dsn)
    assert len(items) == 1 and items[0]["reason"] == "harga salah"
    event = ledger.events("tugas-lapor-1", event_type="false_report")
    assert len(event) == 1
    assert event[0]["content"]["evidence_id"] == "bukti-1"
