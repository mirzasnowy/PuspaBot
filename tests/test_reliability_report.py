"""Tes Skor Keandalan (§3.4) + Lapor Salah (§3.8 revisi 7)."""

import psycopg

from puspa.moat import reliability, report


def _content_pembacaan(test_dsn, user_id, source, category, verified, total):
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            for i in range(total):
                verdict = "Verified" if i < verified else "Uncertain"
                cur.execute(
                    "INSERT INTO source_readings (user_id, sumber, category, vonis)"
                    " VALUES (%s, %s, %s, %s)", (user_id, source, category, verdict))
        conn.commit()


def test_source_score_percent_and_age(make_user, test_dsn):
    user = make_user()
    _content_pembacaan(test_dsn, user["user_id"], "Shopee", "TV", 46, 50)
    score = reliability.source_score("Shopee", "TV", dsn=test_dsn)
    assert score["percent_verified"] == 92.0
    assert score["amount"] == 50
    assert score["downgrade_verdict"] is False
    assert score["last_reading_age_seconds"] is not None
    label = reliability.label_panel("Shopee", "TV", dsn=test_dsn)
    assert "92.0% terverifikasi dari 50 pembacaan terakhir" in label


def test_source_score_below_80_lowers_verdict(make_user, test_dsn):
    user = make_user()
    _content_pembacaan(test_dsn, user["user_id"], "Blibli", "phone", 30, 50)
    score = reliability.source_score("Blibli", "phone", dsn=test_dsn)
    assert score["percent_verified"] == 60.0
    assert score["downgrade_verdict"] is True


def test_source_score_without_reading(make_user, test_dsn):
    make_user()
    score = reliability.source_score("Tokopedia", "phone", dsn=test_dsn)
    assert score["percent_verified"] is None
    assert score["amount"] == 0
    assert "belum ada pembacaan" in reliability.label_panel("Tokopedia", "phone", dsn=test_dsn)


def test_procedure_score_from_runs(make_user, test_dsn):
    from puspa.graphs import service as graf

    user = make_user()
    procedure = graf.write_procedure(user_id=user["user_id"], service="PLN",
                                   step=[{"step": "read"}], agent_id=user["agent_id"],
                                   dsn=test_dsn)
    score = reliability.procedure_score(procedure["id"], user["user_id"], dsn=test_dsn)
    assert score["percent_verified"] == 100.0  # 1 jalan sukses saat tulis
    graf.record_run(procedure["id"], user["user_id"], False, agent_id=user["agent_id"], dsn=test_dsn)
    score = reliability.procedure_score(procedure["id"], user["user_id"], dsn=test_dsn)
    assert score["percent_verified"] == 50.0
    assert score["downgrade_verdict"] is True


def test_wrong_report_lowers_score_and_is_recorded_in_ledger(make_user, test_dsn, ledger):
    user = make_user()
    _content_pembacaan(test_dsn, user["user_id"], "Shopee", "phone", 50, 50)
    assert reliability.source_score("Shopee", "phone", dsn=test_dsn)["percent_verified"] == 100.0
    result = report.report_wrong(
        evidence_id="bukti-1", user_id=user["user_id"], reason="harga salah",
        source="Shopee", category="phone", task_id="tugas-lapor-1",
        agent_id=user["agent_id"], dsn=test_dsn, ledger=ledger)
    assert result["reread_requested"] is True
    # Satu laporan "Tidak bisa dipastikan" masuk 50 terakhir -> 49/50 = 98%.
    assert reliability.source_score("Shopee", "phone", dsn=test_dsn)["percent_verified"] == 98.0
    items = report.list_reports("bukti-1", dsn=test_dsn)
    assert len(items) == 1 and items[0]["reason"] == "harga salah"
    event = ledger.events("tugas-lapor-1", event_type="false_report")
    assert len(event) == 1
    assert event[0]["content"]["evidence_id"] == "bukti-1"
