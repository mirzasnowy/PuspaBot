"""Penjadwal (§2.14 revisi 7).

- Tabel pemicu di Postgres (jenis, jadwal, alur, pengguna,
  pemicu_berikutnya). Satu proses penjadwal memicu tugas lewat
  antrean yang sama (tabel `tugas` + SKIP LOCKED).
- Pemicu tercatat di Buku Kerja (`pemicu_terpicu`) dengan kunci
  idempotensi (`pemicu_jalan`), jadi satu pemicu tidak pernah
  menjalankan dua tugas.
- Pemicu terlewat: dijalankan sekali saat bangun + dilaporkan
  (`pemicu_terlewat`); pengingat yang sudah lewat jatuh tempo
  ditandai terlambat, tidak disamarkan.
- Detak penjadwal ke Kantor: mati > 2 menit -> tampil + beri tahu
  operator (`sehat()`).
- Job bawaan yang disambungkan di sini: backup harian Fase 1 (C126)
  dan konsolidasi saat sepi Fase 1 (C60) — keduanya dipanggil lewat
  pemicu, bukan cron terpisah.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
HEARTBEAT_STALE_SECONDS = 120
TRIGGER_TYPES = ("schedule", "due_date_bill", "price_drop", "manual",
                "backup_daily", "consolidation", "trust_root_refresh")


class SchedulerError(Exception):
    """Penjadwal gagal / pemicu tidak sah."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Trigger:
    """Satu pemicu terjadwal."""

    id: str
    user_id: str
    event_type: str
    schedule: str = ""
    workflow: str = ""
    next_trigger_at: datetime = field(default_factory=_now)
    active: bool = True


def _connect(dsn=None):
    return psycopg.connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def create_trigger(user_id: str, event_type: str, next_trigger_at: datetime,
                schedule: str = "", workflow: str = "", dsn=None) -> Trigger:
    """Buat pemicu baru (idempotent per id yang dibuat di sini)."""
    if event_type not in TRIGGER_TYPES:
        raise SchedulerError(f"jenis pemicu tidak dikenal: {event_type!r}")
    trigger = Trigger(id=f"pemicu-{uuid.uuid4().hex[:12]}", user_id=str(user_id),
                    event_type=event_type, schedule=schedule, workflow=workflow,
                    next_trigger_at=next_trigger_at)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO triggers (id, user_id, type, schedule, workflow, next_trigger_at, active)"
                " VALUES (%s, %s, %s, %s, %s, %s, TRUE)",
                (trigger.id, trigger.user_id, trigger.event_type, trigger.schedule,
                 trigger.workflow, trigger.next_trigger_at),
            )
        conn.commit()
    return trigger


def list_due_triggers(dsn=None, now: datetime | None = None) -> list[Trigger]:
    """Pemicu aktif yang sudah jatuh tempo, diklaim SKIP LOCKED."""
    now = now or _now()
    with _connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT id, user_id, type, schedule, workflow, next_trigger_at, active"
                " FROM triggers WHERE active AND next_trigger_at <= %s"
                " ORDER BY next_trigger_at ASC FOR UPDATE SKIP LOCKED",
                (now,),
            )
            rows = cur.fetchall()
        conn.commit()
    return [Trigger(id=r["id"], user_id=str(r["user_id"]), event_type=r["type"],
                   schedule=r["schedule"] or "", workflow=r["workflow"] or "",
                   next_trigger_at=r["next_trigger_at"], active=r["active"])
            for r in rows]


def run_trigger(trigger: Trigger, *, sink=None, missed: bool = False,
                 dsn=None, now: datetime | None = None) -> dict | None:
    """Jalankan satu pemicu (sekali, idempotent).

    Kembalikan dict jalan (dengan `tugas_id`) atau None bila kunci
    idempotensi sudah pernah dipakai (pemicu yang sama tidak pernah
    menjalankan dua tugas). Mencatat `pemicu_terpicu` (atau
    `pemicu_terlewat`) ke Buku Kerja lewat `sink(jenis, isi)`.
    """
    now = now or _now()
    key = f"{trigger.id}:{trigger.next_trigger_at.isoformat()}"
    task_id = f"tugas-{uuid.uuid4().hex[:12]}"
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM trigger_runs WHERE idempotency_key = %s", (key,))
            if cur.fetchone() is not None:
                return None
            cur.execute(
                "INSERT INTO trigger_runs (idempotency_key, trigger_id, task_id, terlewat)"
                " VALUES (%s, %s, %s, %s)",
                (key, trigger.id, task_id, missed),
            )
            # Jadwal berikutnya: geser 1 hari (jadwal harian; jadwal
            # cron penuh adalah peningkatan Fase 4 — dicatat di STATUS).
            cur.execute(
                "UPDATE triggers SET next_trigger_at = next_trigger_at + INTERVAL '1 day'"
                " WHERE id = %s", (trigger.id,),
            )
        conn.commit()
    if sink is not None:
        try:
            sink("trigger_missed" if missed else "trigger_fired",
                 {"trigger_id": trigger.id, "task_id": task_id,
                  "type": trigger.event_type, "missed": missed})
        except Exception:
            pass
    return {"trigger_id": trigger.id, "task_id": task_id, "missed": missed}


def run_missed(*, sink=None, dsn=None,
                           now: datetime | None = None) -> list[dict]:
    """Saat bangun: jalankan sekali tiap pemicu terlewat + laporkan.

    Pemicu dianggap terlewat bila `pemicu_berikutnya` sudah lewat
    lebih dari 1 jam dari sekarang (bukan sekadar baru jatuh tempo —
    yang baru jatuh tempo ditangani `tick` biasa).
    """
    now = now or _now()
    cutoff = now - timedelta(hours=1)
    with _connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT id, user_id, type, schedule, workflow, next_trigger_at, active"
                " FROM triggers WHERE active AND next_trigger_at <= %s"
                " ORDER BY next_trigger_at ASC",
                (cutoff,),
            )
            rows = cur.fetchall()
    result = []
    for r in rows:
        trigger = Trigger(id=r["id"], user_id=str(r["user_id"]), event_type=r["type"],
                        schedule=r["schedule"] or "", workflow=r["workflow"] or "",
                        next_trigger_at=r["next_trigger_at"], active=r["active"])
        run = run_trigger(trigger, sink=sink, missed=True, dsn=dsn, now=now)
        if run is not None:
            result.append(run)
    return result


def heartbeat(dsn=None, now: datetime | None = None) -> None:
    """Kirim detak penjadwal (ditulis ke `detak_sistem`)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO system_heartbeats (komponen, created_at) VALUES ('penjadwal', %s)"
                " ON CONFLICT (komponen) DO UPDATE SET created_at = EXCLUDED.created_at",
                (now or _now(),),
            )
        conn.commit()


def is_healthy(dsn=None, now: datetime | None = None) -> bool:
    """True bila detak penjadwal terakhir < 2 menit lalu."""
    now = now or _now()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT created_at FROM system_heartbeats WHERE komponen = 'penjadwal'")
            row = cur.fetchone()
    if row is None:
        return False
    return (now - row[0]).total_seconds() < HEARTBEAT_STALE_SECONDS


def tick(*, sink=None, dsn=None, now: datetime | None = None,
         curriculum=None) -> list[dict]:
    """Satu putaran penjadwal: detak + jalankan pemicu jatuh tempo.

    When ``curriculum`` is given (a ``CurriculumRunner`` instance
    from ``puspa.scheduler.curriculum``), one idle-time learning
    task runs after the triggers through ``run_curriculum_tick``.
    The default (``curriculum=None``) runs no curriculum work, so
    trigger behaviour is unchanged. Curriculum work never changes
    the returned trigger runs and never raises into the tick.
    """
    heartbeat(dsn=dsn, now=now)
    result = []
    for trigger in list_due_triggers(dsn=dsn, now=now):
        run = run_trigger(trigger, sink=sink, dsn=dsn, now=now)
        if run is not None:
            result.append(run)
    if curriculum is not None:
        try:
            from puspa.scheduler.curriculum import run_curriculum_tick

            run_curriculum_tick(runner=curriculum, sink=sink, now=now)
        except Exception:
            pass
    return result


def schedule_backup_daily(user_id: str, *, hour: int = 3, dsn=None) -> Trigger:
    """Daftarkan job backup harian Fase 1 (C126) sebagai pemicu.

    Jalan pemicu ini memanggil `puspa.backup.create_backup`; penjadwal
    yang menjalankan job memakai `jalankan_job_terjadwal` di bawah.
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(user_id, "backup_daily", tomorrow, schedule=f"harian {hour:02d}:00", dsn=dsn)


def schedule_trust_root_refresh(user_id: str, *, hour: int = 3, dsn=None) -> Trigger:
    """Register the daily Sigstore trust-root refresh job as a trigger.

    Running this trigger calls `puspa.policy.trust_root.run_trust_root_refresh_job`
    through `run_scheduled_job` below. The refresh runs outside the
    verify path: verification stays offline, using the cached root
    (or the bundled root when no cache exists yet).
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(user_id, "trust_root_refresh", tomorrow, schedule=f"daily {hour:02d}:00", dsn=dsn)


def run_scheduled_job(run: dict, *, dsn=None) -> dict:
    """Eksekusi job sistem untuk satu jalan pemicu (backup/konsolidasi).

    Kembalikan {"job": ..., "hasil": ...}. Job yang tidak dikenal
    dikembalikan sebagai tugas biasa (alur pengguna) tanpa eksekusi
    sistem — pemanggil (runtime tugas) yang menanganinya.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT type, user_id FROM triggers WHERE id = %s", (run["trigger_id"],))
            row = cur.fetchone()
    if row is None:
        return {"job": "unknown", "result": None}
    event_type, user_id = row
    if event_type == "backup_daily":
        from puspa.backup.backup import create_backup

        result = create_backup(dsn=dsn)
        return {"job": "backup_daily", "result": str(result)}
    if event_type == "consolidation":
        from puspa.memory.consolidate import consolidate_idle

        result = consolidate_idle(str(user_id), dsn=dsn)
        return {"job": "consolidation", "result": result}
    if event_type == "trust_root_refresh":
        from puspa.policy.trust_root import run_trust_root_refresh_job

        result = run_trust_root_refresh_job()
        return {"job": "trust_root_refresh", "result": result}
    return {"job": "regular_task", "result": run["task_id"]}
