"""Tes tick penjadwal -> jalan alur (§2.14): pemicu jadwal jatuh tempo
menjalankan alur, jadwal berikutnya dihitung dari definisi alur
(bukan +1 hari Fase 2), pemicu terlewat dijalankan sekali + dilaporkan,
idempotensi."""

from datetime import datetime, timezone

from puspa.workflow.runner import aktifkan_workflow, tick_workflow, dry_run
from puspa.workflow.templates import create_dari_templat
from puspa.workflow.triggers import save_to_penjadwal


def _workflow_bill_active(make_user, test_dsn, permission_user_penuh):
    u = make_user()
    user_id, agent_id = u["user_id"], u["agent_id"]
    workflow = create_dari_templat("bill_monthly", user_id=user_id, agent_id=agent_id,
                             dsn=test_dsn)
    dry = dry_run(workflow["id"], user_id=user_id, dsn=test_dsn)
    aktifkan_workflow(workflow["id"], user_id=user_id, permission_user=permission_user_penuh,
                  result_dry=dry, dsn=test_dsn)
    trigger = save_to_penjadwal(
        user_id=user_id, workflow_id=str(workflow["id"]),
        trigger={"type": "schedule", "mode": "date", "date": 25,
                "start_time": "07:00"},
        sekarang=datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc),
        dsn=test_dsn)
    return user_id, workflow, trigger


def test_tick_jalankan_workflow_schedule_and_geser_to_month_next(
        make_user, test_dsn, permission_user_penuh):
    user_id, workflow, trigger = _workflow_bill_active(make_user, test_dsn,
                                               permission_user_penuh)
    result = tick_workflow(
        dsn=test_dsn,
        sekarang=datetime(2026, 11, 25, 7, 5, tzinfo=timezone.utc))
    assert len(result) == 1
    assert result[0]["workflow_id"] == str(workflow["id"])
    assert result[0]["terlewat"] is False
    # Jalan alur tercatat (berhenti di siapkan bayar -> menunggu persetujuan).
    assert result[0]["jalan"]["status"] == "pending_approval"
    # Jadwal berikutnya = 25 Desember (bukan 26 November / +1 hari).
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT next_trigger_at FROM triggers WHERE id = %s",
                        (trigger.id,))
            berikut = cur.fetchone()[0]
    assert (berikut.year, berikut.month, berikut.day) == (2026, 12, 25)


def test_tick_idempotent_not_jalan_dua_kali(make_user, test_dsn,
                                            permission_user_penuh):
    user_id, workflow, trigger = _workflow_bill_active(make_user, test_dsn,
                                               permission_user_penuh)
    sekarang = datetime(2026, 11, 25, 7, 5, tzinfo=timezone.utc)
    assert len(tick_workflow(dsn=test_dsn, sekarang=sekarang)) == 1
    assert tick_workflow(dsn=test_dsn, sekarang=sekarang) == []


def test_tick_terlewat_dijalankan_sekali_and_dilaporkan(make_user, test_dsn,
                                                       permission_user_penuh):
    user_id, workflow, trigger = _workflow_bill_active(make_user, test_dsn,
                                               permission_user_penuh)
    # Host "mati" 2 hari: bangun 27 Nov — pemicu 25 Nov terlewat.
    result = tick_workflow(
        dsn=test_dsn,
        sekarang=datetime(2026, 11, 27, 7, 5, tzinfo=timezone.utc),
        terlewat=True)
    assert len(result) == 1
    assert result[0]["terlewat"] is True
    # Dijalankan sekali (bukan berkali-kali): tick terlewat kedua kosong.
    assert tick_workflow(
        dsn=test_dsn,
        sekarang=datetime(2026, 11, 27, 7, 6, tzinfo=timezone.utc),
        terlewat=True) == []
    # Kejadian pemicu_terlewat tercatat di Buku Kerja.
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM events WHERE type = 'trigger_missed'")
            assert cur.fetchone()[0] >= 1
