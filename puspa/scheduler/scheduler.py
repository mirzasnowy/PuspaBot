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

import contextlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg

from puspa.ledger.kill_switch import is_kill_switch_active
from puspa.proactivity.budget import Initiative, InterruptionBudget

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
HEARTBEAT_STALE_SECONDS = 120
TRIGGER_TYPES = (
    "schedule",
    "due_date_bill",
    "price_drop",
    "manual",
    "backup_daily",
    "consolidation",
    "trust_root_refresh",
    "revocation_list_refresh",
    "dream",
    "sentinel_scan",
)

#: Proactive trigger types pay from the interruption budget (K10):
#: trigger type -> (initiative kind, assessed value). Only
#: system-initiated pings pay. A user-created schedule, a manual
#: trigger, and the silent system jobs (backup, consolidation,
#: dream, trust-root refresh, sentinel scan) never pay: the user
#: asked for the first two, and the rest never interrupt anyone.
#: The value is fixed per type because the scheduler has no
#: per-event benefit signal yet (see docs/interruption-budget.md):
#: a bill about to fall due (0.85) is time-critical and costly
#: to miss, so it also clears the scarcity bar; a price drop
#: (0.70) saves money but is rarely urgent, so it is suppressed
#: first when the budget thins.
PROACTIVE_TRIGGER_TYPES = {
    "price_drop": ("price_drop", 0.70),
    "due_date_bill": ("bill_due", 0.85),
}


class SchedulerError(Exception):
    """Penjadwal gagal / pemicu tidak sah."""


def _now() -> datetime:
    return datetime.now(UTC)


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


def create_trigger(
    user_id: str,
    event_type: str,
    next_trigger_at: datetime,
    schedule: str = "",
    workflow: str = "",
    dsn=None,
) -> Trigger:
    """Buat pemicu baru (idempotent per id yang dibuat di sini)."""
    if event_type not in TRIGGER_TYPES:
        raise SchedulerError(f"jenis pemicu tidak dikenal: {event_type!r}")
    trigger = Trigger(
        id=f"pemicu-{uuid.uuid4().hex[:12]}",
        user_id=str(user_id),
        event_type=event_type,
        schedule=schedule,
        workflow=workflow,
        next_trigger_at=next_trigger_at,
    )
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO triggers (id, user_id, type, schedule, workflow, next_trigger_at, active)"
                " VALUES (%s, %s, %s, %s, %s, %s, TRUE)",
                (
                    trigger.id,
                    trigger.user_id,
                    trigger.event_type,
                    trigger.schedule,
                    trigger.workflow,
                    trigger.next_trigger_at,
                ),
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
    return [
        Trigger(
            id=r["id"],
            user_id=str(r["user_id"]),
            event_type=r["type"],
            schedule=r["schedule"] or "",
            workflow=r["workflow"] or "",
            next_trigger_at=r["next_trigger_at"],
            active=r["active"],
        )
        for r in rows
    ]


def run_trigger(
    trigger: Trigger,
    *,
    sink=None,
    missed: bool = False,
    dsn=None,
    now: datetime | None = None,
    budget: InterruptionBudget | None = None,
    kill_switch=None,
) -> dict | None:
    """Jalankan satu pemicu (sekali, idempotent).

    Kembalikan dict jalan (dengan `tugas_id`) atau None bila kunci
    idempotensi sudah pernah dipakai (pemicu yang sama tidak pernah
    menjalankan dua tugas). Mencatat `pemicu_terpicu` (atau
    `pemicu_terlewat`) ke Buku Kerja lewat `sink(jenis, isi)`.

    With `budget` (K10), a proactive trigger (`price_drop`,
    `due_date_bill`) pays from the daily interruption budget
    first: when the budget denies it, return None WITHOUT
    writing a run row and WITHOUT moving `pemicu_berikutnya` —
    the trigger stays due and is evaluated again on the next
    round (or tomorrow, when the budget resets). The denial is
    recorded in `budget.spend_log()`, not in the ledger: there
    is no ledger event type for "held back", and the 31 revisi-7
    event types are closed.

    With `kill_switch` (K12), an active switch stops the trigger
    before anything runs: return None WITHOUT writing a run row,
    WITHOUT moving `pemicu_berikutnya`, and WITHOUT calling the
    sink — the trigger stays due and runs after the switch is
    disarmed.
    """
    now = now or _now()
    if is_kill_switch_active(kill_switch):
        return None
    if budget is not None:
        spec = PROACTIVE_TRIGGER_TYPES.get(trigger.event_type)
        if spec is not None:
            kind, value = spec
            decision = budget.try_spend(
                Initiative(kind=kind, value=value, label=trigger.id), day=now.date()
            )
            if not decision.allowed:
                return None
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
                " WHERE id = %s",
                (trigger.id,),
            )
        conn.commit()
    if sink is not None:
        with contextlib.suppress(Exception):
            sink(
                "trigger_missed" if missed else "trigger_fired",
                {
                    "trigger_id": trigger.id,
                    "task_id": task_id,
                    "type": trigger.event_type,
                    "missed": missed,
                },
            )
    return {"trigger_id": trigger.id, "task_id": task_id, "missed": missed}


def run_missed(
    *, sink=None, dsn=None, now: datetime | None = None, budget=None, kill_switch=None
) -> list[dict]:
    """Saat bangun: jalankan sekali tiap pemicu terlewat + laporkan.

    Pemicu dianggap terlewat bila `pemicu_berikutnya` sudah lewat
    lebih dari 1 jam dari sekarang (bukan sekadar baru jatuh tempo —
    yang baru jatuh tempo ditangani `tick` biasa). Dengan `budget`,
    pemicu proaktif yang terlewat membayar dari budget seperti
    pemicu biasa (lihat `run_trigger`).
    """
    now = now or _now()
    cutoff = now - timedelta(hours=1)
    with _connect(dsn) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT id, user_id, type, schedule, workflow, next_trigger_at, active"
            " FROM triggers WHERE active AND next_trigger_at <= %s"
            " ORDER BY next_trigger_at ASC",
            (cutoff,),
        )
        rows = cur.fetchall()
    result = []
    for r in rows:
        trigger = Trigger(
            id=r["id"],
            user_id=str(r["user_id"]),
            event_type=r["type"],
            schedule=r["schedule"] or "",
            workflow=r["workflow"] or "",
            next_trigger_at=r["next_trigger_at"],
            active=r["active"],
        )
        run = run_trigger(
            trigger,
            sink=sink,
            missed=True,
            dsn=dsn,
            now=now,
            budget=budget,
            kill_switch=kill_switch,
        )
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
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT created_at FROM system_heartbeats WHERE komponen = 'penjadwal'")
        row = cur.fetchone()
    if row is None:
        return False
    return (now - row[0]).total_seconds() < HEARTBEAT_STALE_SECONDS


def tick(
    *,
    sink=None,
    dsn=None,
    now: datetime | None = None,
    curriculum=None,
    budget=None,
    kill_switch=None,
) -> list[dict]:
    """Satu putaran penjadwal: detak + jalankan pemicu jatuh tempo.

    When ``curriculum`` is given (a ``CurriculumRunner`` instance
    from ``puspa.scheduler.curriculum``), one idle-time learning
    task runs after the triggers through ``run_curriculum_tick``.
    The default (``curriculum=None``) runs no curriculum work, so
    trigger behaviour is unchanged. Curriculum work never changes
    the returned trigger runs and never raises into the tick.

    When ``budget`` is given (an ``InterruptionBudget``), the
    proactive triggers (``price_drop``, ``due_date_bill``) pay
    from it first (see ``run_trigger``); a denied trigger is
    simply not run, and the budget's spend log records why.

    When ``kill_switch`` is given (K12) and active, the whole
    tick is a no-op: no heartbeat (a stopped scheduler must not
    report itself healthy), no trigger runs, no curriculum work.
    """
    if is_kill_switch_active(kill_switch):
        return []
    heartbeat(dsn=dsn, now=now)
    result = []
    for trigger in list_due_triggers(dsn=dsn, now=now):
        run = run_trigger(
            trigger, sink=sink, dsn=dsn, now=now, budget=budget, kill_switch=kill_switch
        )
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
    return create_trigger(
        user_id, "backup_daily", tomorrow, schedule=f"harian {hour:02d}:00", dsn=dsn
    )


def schedule_trust_root_refresh(user_id: str, *, hour: int = 3, dsn=None) -> Trigger:
    """Register the daily Sigstore trust-root refresh job as a trigger.

    Running this trigger calls `puspa.policy.trust_root.run_trust_root_refresh_job`
    through `run_scheduled_job` below. The refresh runs outside the
    verify path: verification stays offline, using the cached root
    (or the bundled root when no cache exists yet).
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(
        user_id, "trust_root_refresh", tomorrow, schedule=f"daily {hour:02d}:00", dsn=dsn
    )


def schedule_revocation_list_refresh(user_id: str, *, hour: int = 3, dsn=None) -> Trigger:
    """Register the daily skill revocation-list refresh job as a trigger (E-12, plan 6.7).

    Running this trigger calls
    ``puspa.policy.revocation.run_revocation_refresh_job`` through
    ``run_scheduled_job`` below. The URL comes from
    ``PUSPA_REVOCATION_LIST_URL`` and the issuer public key from
    ``PUSPA_REVOCATION_ISSUER_KEY``; with either unset the job
    reports ``unconfigured`` / ``unavailable`` and fetches nothing.
    The refresh runs outside the verify path: skill loading stays
    offline, using the cached list (or no list when no cache
    exists yet).
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(
        user_id, "revocation_list_refresh", tomorrow, schedule=f"daily {hour:02d}:00", dsn=dsn
    )


def schedule_dream(user_id: str, *, hour: int = 3, dsn=None) -> Trigger:
    """Register the nightly DREAM memory pass (G7) as a trigger.

    Running this trigger calls ``puspa.memory.dream.run_dream_job``
    through ``run_scheduled_job`` below. The dream learns from the
    last 7 days of episodic rows; without an LLM key it reports
    ``"no_llm"`` and applies nothing.
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(user_id, "dream", tomorrow, schedule=f"daily {hour:02d}:00", dsn=dsn)


def schedule_sentinel_scan(user_id: str, *, hour: int = 3, dsn=None) -> Trigger:
    """Register the periodic sentinel scan as a trigger.

    Running this trigger calls
    ``puspa.sentinel.scanner.run_sentinel_scan`` through
    ``run_scheduled_job`` below. The scheduler moves the trigger
    one day forward on each run (see ``run_trigger``), so this
    registration gives a daily scan at most; the 30-minute
    rescan interval lives inside the scanner state (a scan
    requested sooner is a no-op unless forced). A 30-minute
    cadence needs a caller that invokes ``run_sentinel_scan``
    every 30 minutes — for example a G8 cron job
    (``every 30m``) — not this daily trigger.
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(
        user_id, "sentinel_scan", tomorrow, schedule=f"daily {hour:02d}:00", dsn=dsn
    )


def run_scheduled_job(run: dict, *, dsn=None) -> dict:
    """Eksekusi job sistem untuk satu jalan pemicu (backup/konsolidasi).

    Kembalikan {"job": ..., "hasil": ...}. Job yang tidak dikenal
    dikembalikan sebagai tugas biasa (alur pengguna) tanpa eksekusi
    sistem — pemanggil (runtime tugas) yang menanganinya.
    """
    with _connect(dsn) as conn, conn.cursor() as cur:
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
    if event_type == "revocation_list_refresh":
        from puspa.policy.revocation import run_revocation_refresh_job

        result = run_revocation_refresh_job()
        return {"job": "revocation_list_refresh", "result": result}
    if event_type == "dream":
        from puspa.memory.dream import run_dream_job

        result = run_dream_job(str(user_id), dsn=dsn)
        return {"job": "dream", "result": result}
    if event_type == "sentinel_scan":
        from puspa.sentinel.scanner import run_sentinel_scan

        result = run_sentinel_scan(user_id=str(user_id), dsn=dsn)
        return {"job": "sentinel_scan", "result": result}
    return {"job": "regular_task", "result": run["task_id"]}
