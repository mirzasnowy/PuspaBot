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
    "ontology_review",
    "amnesia_check",
    "longterm_consolidation",
    "initiative_digest",
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
    *,
    sink=None,
    dsn=None,
    now: datetime | None = None,
    budget=None,
    kill_switch=None,
    turn_loop=None,
) -> list[dict]:
    """Saat bangun: jalankan sekali tiap pemicu terlewat + laporkan.

    Pemicu dianggap terlewat bila `pemicu_berikutnya` sudah lewat
    lebih dari 1 jam dari sekarang (bukan sekadar baru jatuh tempo —
    yang baru jatuh tempo ditangani `tick` biasa). Dengan `budget`,
    pemicu proaktif yang terlewat membayar dari budget seperti
    pemicu biasa (lihat `run_trigger`).

    Like ``tick``, every fired trigger also runs its job here
    through :func:`run_trigger_job` (a routine runs through
    ``turn_loop`` when one is wired in); the job outcome is
    attached to each returned run dict under ``"job"``.
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
            result.append(_execute_run(run, turn_loop=turn_loop, dsn=dsn, now=now))
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


def _trigger_row(trigger_id: str, dsn=None) -> dict | None:
    """One triggers row as a dict, or None when it does not exist."""
    with _connect(dsn) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT id, user_id, type, schedule, workflow, next_trigger_at, active"
            " FROM triggers WHERE id = %s",
            (trigger_id,),
        )
        return cur.fetchone()


def _routine_schedule_of(row: dict):
    """The schedule dict of a routine trigger row, or None.

    Imported lazily: ``puspa.scheduler.routines`` imports this
    module, so a top-level import would be circular.
    """
    from .routines import routine_schedule

    trigger = Trigger(
        id=row["id"],
        user_id=str(row["user_id"]),
        event_type=row["type"],
        schedule=row["schedule"] or "",
        workflow=row["workflow"] or "",
        next_trigger_at=row["next_trigger_at"],
        active=row["active"],
    )
    return routine_schedule(trigger)


def _advance_routine(trigger_id: str, schedule: dict, *, now: datetime, dsn=None) -> None:
    """Move a routine to its next fire time from its own schedule.

    ``run_trigger`` always moves a trigger one day forward, which
    is wrong for an hourly or weekday routine and for a one-shot.
    This overwrites that step with the schedule's real next
    occurrence; a one-shot (no next occurrence) is deactivated
    instead so it never fires again.
    """
    from .schedules import next_occurrence

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            nxt = next_occurrence(schedule, now)
            if nxt is None:
                cur.execute("UPDATE triggers SET active = FALSE WHERE id = %s", (trigger_id,))
            else:
                cur.execute(
                    "UPDATE triggers SET next_trigger_at = %s WHERE id = %s", (nxt, trigger_id)
                )
        conn.commit()


def run_trigger_job(run: dict, *, turn_loop=None, dsn=None, now: datetime | None = None) -> dict:
    """Execute the job of one fired trigger run.

    This is the production caller ``run_scheduled_job`` was
    missing: system jobs (backup, consolidation, dream, sentinel
    scan, trust-root and revocation refresh, ontology review,
    amnesia check) run here, directly. A user routine (a ``schedule`` trigger
    whose schedule field holds a schedule dict) runs through
    the given turn loop instead: its task text becomes the
    turn's first message, so it runs with no human present,
    through the same loop, budget, and safety checks as a chat
    turn. With no turn loop wired in, the routine is recorded
    as not run, never silently pretended to have run.

    A plain trigger that is neither a system job nor a routine
    (for example a workflow trigger, which ``tick_workflow``
    runs) is recorded as skipped. One failing job never raises
    into the tick: the outcome carries ``status: "failed"``
    and the error text, and the tick moves on to the next
    trigger. The outcome is returned (and attached to the run
    dict by the tick); it is NOT sent through the tick's sink,
    which stays reserved for the trigger ledger events
    (``trigger_fired`` / ``trigger_missed``).
    """
    now = now or _now()
    outcome: dict = {
        "trigger_id": run["trigger_id"],
        "task_id": run["task_id"],
        "job": "unknown",
        "status": "failed",
    }
    try:
        job_result = run_scheduled_job(run, dsn=dsn)
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        return outcome
    job = job_result.get("job", "unknown")
    outcome["job"] = job
    if job != "regular_task":
        outcome["status"] = "completed"
        outcome["result"] = job_result.get("result")
        return outcome
    row = _trigger_row(run["trigger_id"], dsn=dsn)
    if row is None:
        outcome["status"] = "skipped"
        outcome["reason"] = "trigger row is gone"
        return outcome
    schedule = _routine_schedule_of(row)
    task_text = (row["workflow"] or "").strip()
    if schedule is None or not task_text:
        outcome["status"] = "skipped"
        outcome["reason"] = "not a routine; handled by its own runner"
        return outcome
    with contextlib.suppress(Exception):
        _advance_routine(run["trigger_id"], schedule, now=now, dsn=dsn)
    if turn_loop is None:
        outcome["status"] = "not_run"
        outcome["reason"] = "no turn loop wired in; the routine text was not run"
        return outcome
    try:
        from puspa.turns.messages import InternalMessage

        result = turn_loop.run_turn(
            [InternalMessage.user(task_text)],
            user_id=str(row["user_id"]),
            task_id=run["task_id"],
        )
    except Exception as exc:
        outcome["status"] = "failed"
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        return outcome
    outcome["turn_status"] = result.status
    outcome["text"] = result.text
    if result.status == "completed":
        outcome["status"] = "completed"
    else:
        outcome["status"] = "failed"
        outcome["error"] = result.message or f"turn ended {result.status}"
    return outcome


def _execute_run(run: dict, *, turn_loop=None, dsn=None, now=None) -> dict:
    """Run one fired trigger's job and attach the outcome to the run.

    The job runner never raises into the tick: an unexpected
    failure is recorded as a failed outcome instead.
    """
    try:
        outcome = run_trigger_job(run, turn_loop=turn_loop, dsn=dsn, now=now)
    except Exception as exc:  # belt and braces around an injected runner
        outcome = {
            "trigger_id": run["trigger_id"],
            "task_id": run["task_id"],
            "job": "unknown",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    run["job"] = outcome
    return run


def tick(
    *,
    sink=None,
    dsn=None,
    now: datetime | None = None,
    curriculum=None,
    budget=None,
    kill_switch=None,
    turn_loop=None,
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

    Every fired trigger now also runs its job through
    :func:`run_trigger_job` (the production caller): system jobs
    execute here, and a user routine runs through ``turn_loop``
    (a :class:`puspa.turns.loop.TurnLoop`; with none wired in,
    the routine is recorded as not run). The job outcome is
    attached to each returned run dict under ``"job"``. A job
    failure never stops the tick or the other triggers.
    """
    if is_kill_switch_active(kill_switch):
        return []
    heartbeat(dsn=dsn, now=now)
    result = []
    due_triggers = list_due_triggers(dsn=dsn, now=now)
    if due_triggers:
        # E-18: queue age is the oldest due trigger's lateness.
        with contextlib.suppress(Exception):
            from puspa.observability import metrics as observability_metrics

            from .due import lateness

            oldest_s = max(
                lateness(trigger.next_trigger_at, now).total_seconds() for trigger in due_triggers
            )
            observability_metrics.get_metrics().record_queue_age(oldest_s)
    for trigger in due_triggers:
        run = run_trigger(
            trigger, sink=sink, dsn=dsn, now=now, budget=budget, kill_switch=kill_switch
        )
        if run is not None:
            result.append(_execute_run(run, turn_loop=turn_loop, dsn=dsn, now=now))
    if curriculum is not None:
        try:
            from puspa.scheduler.curriculum import run_curriculum_tick

            run_curriculum_tick(runner=curriculum, sink=sink, now=now)
        except Exception:
            pass
    return result


def schedule_backup_daily(user_id: str, *, hour: int = 3, dsn=None) -> Trigger:
    """Daftarkan job backup harian Fase 1 (C126) sebagai pemicu.

    Jalan pemicu ini memanggil `puspa.backup.ports.create_backup`; penjadwal
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


def schedule_consolidation(user_id: str, *, hour: int = 3, dsn=None) -> Trigger:
    """Register the daily sleep consolidation (K9) as a trigger.

    Running this trigger calls ``consolidate_idle`` through
    ``run_scheduled_job`` below, which also prunes stale causal
    hypotheses and records the run in the ledger. The scheduler
    moves the trigger one day forward on each run (see
    ``run_trigger``), so this registration gives one
    consolidation a day at the given hour.
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(
        user_id, "consolidation", tomorrow, schedule=f"daily {hour:02d}:00", dsn=dsn
    )


def schedule_ontology_review(user_id: str, *, day: int = 1, hour: int = 3, dsn=None) -> Trigger:
    """Register the monthly ontology review (K9) as a trigger.

    The trigger itself fires daily (``run_trigger`` moves it one
    day forward on each run); the monthly cadence is a gate
    inside ``run_scheduled_job``: the review only runs when the
    current calendar month has no review recorded yet (see
    ``reviewed_this_month``). ``day`` picks the first run: the
    next date on or after tomorrow whose day of month is
    ``day`` (1..28), so the default runs on the 1st of next
    month. A full monthly trigger step is a Fase 4 improvement,
    like the cron schedule noted in ``run_trigger``.
    """
    if not 1 <= day <= 28:
        raise SchedulerError(f"day must be within 1..28, got {day!r}")
    first = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    while first.day != day:
        first += timedelta(days=1)
    return create_trigger(
        user_id, "ontology_review", first, schedule=f"daily {hour:02d}:00", dsn=dsn
    )


def schedule_amnesia_check(user_id: str, *, hour: int = 3, dsn=None) -> Trigger:
    """Register the nightly amnesia check (V2) as a trigger.

    Running this trigger calls ``puspa.memory.amnesia.
    run_amnesia_check`` through ``run_scheduled_job`` below:
    20 pinned items are recalled cold, any miss is recorded
    as an incident in the ledger and triggers an index repair
    (a durable-memory rebuild), followed by a recheck. The
    scheduler moves the trigger one day forward on each run
    (see ``run_trigger``), so this registration gives one
    check a night at the given hour.
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(
        user_id, "amnesia_check", tomorrow, schedule=f"daily {hour:02d}:00", dsn=dsn
    )


def schedule_initiative_digest(user_id: str, *, hour: int = 7, dsn=None) -> Trigger:
    """Register the daily initiative digest (K14) as a trigger.

    Running this trigger calls ``puspa.self.initiative.
    run_daily_digest_job`` through ``run_scheduled_job`` below:
    the standing goals and pending commitments are read from
    durable memory, candidates are derived, and the digest is
    rendered. The job has no executor, so it only ever
    proposes — nothing runs silently from the scheduled job.
    The scheduler moves the trigger one day forward on each
    run (see ``run_trigger``), so this registration gives one
    digest a morning at the given hour.
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(
        user_id, "initiative_digest", tomorrow, schedule=f"daily {hour:02d}:00", dsn=dsn
    )


def schedule_longterm_consolidation(user_id: str, *, hour: int = 4, dsn=None) -> Trigger:
    """Register the nightly long-term consolidation (K16) as a trigger.

    Running this trigger calls ``puspa.memory.longterm.
    consolidate_longterm`` with ``run_k9=True`` through
    ``run_scheduled_job`` below: the K9 idle consolidation runs
    first, then the K16 pass applies the promotion rules, the
    forgetting curve and the tier moves (including archiving).
    The scheduler moves the trigger one day forward on each run
    (see ``run_trigger``), so this registration gives one pass a
    night at the given hour. Hour 4 keeps it clear of the
    amnesia check at hour 3.
    """
    tomorrow = (_now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return create_trigger(
        user_id, "longterm_consolidation", tomorrow, schedule=f"daily {hour:02d}:00", dsn=dsn
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
        from puspa.backup.ports import create_backup

        result = create_backup(dsn=dsn)
        return {"job": "backup_daily", "result": str(result)}
    if event_type == "consolidation":
        from puspa.ledger.ledger import Ledger
        from puspa.memory.consolidate import consolidate_idle
        from puspa.memory.ontology_review import prune_stale_hypotheses

        result = consolidate_idle(str(user_id), dsn=dsn)
        hypotheses_expired = prune_stale_hypotheses(str(user_id), dsn=dsn)
        Ledger(dsn).append_event(
            task_id=run["task_id"],
            agent_id="puspa",
            event_type="task_completed",
            content={
                "job": "consolidation",
                "user_id": str(user_id),
                "summaries_created": result["summaries_created"],
                "procedures_promoted": result["procedures_promoted"],
                "proposals_created": result["proposals_created"],
                "hypotheses_expired": hypotheses_expired,
            },
        )
        return {
            "job": "consolidation",
            "result": {**result, "hypotheses_expired": hypotheses_expired},
        }
    if event_type == "ontology_review":
        from puspa.memory.ontology_review import reviewed_this_month, run_ontology_review

        if reviewed_this_month(str(user_id), dsn=dsn):
            return {
                "job": "ontology_review",
                "result": {"status": "skipped", "reason": "already reviewed this month"},
            }
        result = run_ontology_review(str(user_id), dsn=dsn, task_id=run["task_id"])
        return {"job": "ontology_review", "result": result}
    if event_type == "amnesia_check":
        from puspa.memory.amnesia import run_amnesia_check

        result = run_amnesia_check(str(user_id), dsn=dsn, task_id=run["task_id"])
        return {"job": "amnesia_check", "result": result}
    if event_type == "longterm_consolidation":
        from puspa.ledger.ledger import Ledger
        from puspa.memory.longterm import consolidate_longterm

        result = consolidate_longterm(str(user_id), run_k9=True, dsn=dsn)
        Ledger(dsn).append_event(
            task_id=run["task_id"],
            agent_id="puspa",
            event_type="task_completed",
            content={
                "job": "longterm_consolidation",
                "user_id": str(user_id),
                "promoted": result["promoted"],
                "pinned": result["pinned"],
                "archived": result["archived"],
                "restored": result["restored"],
                "tiers": result["tiers"],
            },
        )
        return {"job": "longterm_consolidation", "result": result}
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
    if event_type == "initiative_digest":
        from puspa.self.initiative import run_daily_digest_job

        result = run_daily_digest_job(str(user_id), dsn=dsn)
        return {"job": "initiative_digest", "result": result}
    return {"job": "regular_task", "result": run["task_id"]}
