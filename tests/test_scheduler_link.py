"""Tes tick penjadwal -> jalan alur (§2.14): pemicu jadwal jatuh tempo
menjalankan alur, jadwal berikutnya dihitung dari definisi alur
(bukan +1 hari Fase 2), pemicu terlewat dijalankan sekali + dilaporkan,
idempotensi."""

from datetime import UTC, datetime

from puspa.workflow.runner import activate_workflow, dry_run, tick_workflow
from puspa.workflow.templates import create_from_template
from puspa.workflow.triggers import save_to_scheduler


def _workflow_bill_active(make_user, test_dsn, permission_user_penuh):
    u = make_user()
    user_id, agent_id = u["user_id"], u["agent_id"]
    workflow = create_from_template(
        "bill_monthly", user_id=user_id, agent_id=agent_id, dsn=test_dsn
    )
    dry = dry_run(workflow["id"], user_id=user_id, dsn=test_dsn)
    activate_workflow(
        workflow["id"],
        user_id=user_id,
        permission_user=permission_user_penuh,
        dry_run_result=dry,
        dsn=test_dsn,
    )
    trigger = save_to_scheduler(
        user_id=user_id,
        workflow_id=str(workflow["id"]),
        trigger={"type": "schedule", "mode": "date", "date": 25, "start_time": "07:00"},
        now=datetime(2026, 11, 17, 12, 0, tzinfo=UTC),
        dsn=test_dsn,
    )
    return user_id, workflow, trigger


def test_tick_runs_scheduled_workflow_and_shifts_to_next_month(
    make_user, test_dsn, permission_user_penuh
):
    _user_id, workflow, trigger = _workflow_bill_active(make_user, test_dsn, permission_user_penuh)
    result = tick_workflow(dsn=test_dsn, now=datetime(2026, 11, 25, 7, 5, tzinfo=UTC))
    assert len(result) == 1
    assert result[0]["workflow_id"] == str(workflow["id"])
    assert result[0]["missed"] is False
    # Jalan alur tercatat (berhenti di siapkan bayar -> menunggu persetujuan).
    assert result[0]["run"]["status"] == "pending_approval"
    # Jadwal berikutnya = 25 Desember (bukan 26 November / +1 hari).
    import psycopg

    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT next_trigger_at FROM triggers WHERE id = %s", (trigger.id,))
        next_time = cur.fetchone()[0]
    assert (next_time.year, next_time.month, next_time.day) == (2026, 12, 25)


def test_tick_idempotent_does_not_run_twice(make_user, test_dsn, permission_user_penuh):
    _user_id, _workflow, _trigger = _workflow_bill_active(
        make_user, test_dsn, permission_user_penuh
    )
    now = datetime(2026, 11, 25, 7, 5, tzinfo=UTC)
    assert len(tick_workflow(dsn=test_dsn, now=now)) == 1
    assert tick_workflow(dsn=test_dsn, now=now) == []


def test_missed_tick_runs_once_and_is_reported(make_user, test_dsn, permission_user_penuh):
    _user_id, _workflow, _trigger = _workflow_bill_active(
        make_user, test_dsn, permission_user_penuh
    )
    # Host "mati" 2 hari: bangun 27 Nov — pemicu 25 Nov terlewat.
    result = tick_workflow(dsn=test_dsn, now=datetime(2026, 11, 27, 7, 5, tzinfo=UTC), missed=True)
    assert len(result) == 1
    assert result[0]["missed"] is True
    # Dijalankan sekali (bukan berkali-kali): tick terlewat kedua kosong.
    assert (
        tick_workflow(dsn=test_dsn, now=datetime(2026, 11, 27, 7, 6, tzinfo=UTC), missed=True) == []
    )
    # Kejadian pemicu_terlewat tercatat di Buku Kerja.
    import psycopg

    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM events WHERE type = 'trigger_missed'")
        assert cur.fetchone()[0] >= 1
