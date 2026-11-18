"""General cron scheduler (G8, hermes-agent pattern).

One job model carries four schedule formats:

- relative delay: ``"30m"`` / ``"2h"`` / ``"1d"`` (runs once, after
  the delay),
- interval: ``"every 2h"``,
- cron expression: ``"0 9 * * 1-5"``,
- ISO timestamp: ``"2026-11-20T07:00:00+00:00"`` (runs once).

A job also carries a prompt, skills to inject, a delivery target
(``telegram:<id>`` and friends), an optional model / provider /
script, and a per-job timezone as a top-level field, validated at
the create/update gate. Readers use ``.get("timezone", "UTC")``
so records written without the field keep working.

Execution always uses a fresh agent with no history (see
``execution``): the prompt is composed, completed through an
injected ``llm_complete`` callable (the live path resolves it
lazily from ``puspa/router/complete.py`` — the only live-LLM
entry point), and the result is sent to the delivery target.
Missed runs, quota holds, incidents, and a separate delivery
queue are all handled here. Job state lives in one JSON file,
written atomically (temp file, then rename).

``cronjob_manage`` is the single model tool (create, list,
update, pause, resume, run, remove) as a plain Python function,
not an HTTP route.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import due as due_mod
from .execution import build_execution
from .recovery import handle_failure
from .schedules import (
    DEFAULT_TIMEZONE,
    RoutineScheduleInput,
    is_valid_timezone,
    next_occurrence,
    validate_schedule,
)

__all__ = [
    "CronError",
    "CronJob",
    "CronJobStore",
    "cronjob_manage",
    "execute_job",
    "parse_delay",
    "parse_schedule_spec",
    "run_due_jobs",
]

#: A run this late still executes; older runs are marked missed.
MISSED_GRACE = due_mod.LATE_GRACE

#: Delay before a requeued run is tried again.
REQUEUE_DELAY = timedelta(minutes=1)

_DELAY_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
_EVERY_RE = re.compile(r"^every\s+(\d+)\s*([smhd])$", re.IGNORECASE)
_DELAY_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_JOB_STATUSES = ("active", "paused", "completed")


class CronError(Exception):
    """Cron job store or management call failed."""


def parse_delay(text: str) -> int:
    """Parse a relative delay (``30m`` / ``2h`` / ``1d``) to seconds."""
    match = _DELAY_RE.match(str(text).strip())
    if not match:
        raise CronError(f"not a relative delay: {text!r}")
    return int(match.group(1)) * _DELAY_SECONDS[match.group(2).lower()]


def parse_schedule_spec(
    spec, *, now: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE
) -> dict:
    """Parse one of the four schedule formats into a union dict.

    A dict is validated as-is (the union schema from
    ``schedules``). A string is tried, in order, as a relative
    delay (``30m``), an interval (``every 2h``), an ISO timestamp
    (one-shot), and finally a 5-field cron expression.
    """
    if isinstance(spec, dict):
        problems = validate_schedule(spec)
        if problems:
            raise CronError("; ".join(problems))
        return dict(spec)
    text = str(spec).strip()
    now = now or datetime.now(UTC)
    if _DELAY_RE.match(text):
        at = now + timedelta(seconds=parse_delay(text))
        return {"type": "once", "at": at.isoformat(), "timezone": timezone_name}
    match = _EVERY_RE.match(text)
    if match:
        seconds = int(match.group(1)) * _DELAY_SECONDS[match.group(2).lower()]
        return {"type": "interval", "seconds": seconds, "timezone": timezone_name}
    parsed_at: datetime | None = None
    with contextlib.suppress(ValueError):
        parsed_at = datetime.fromisoformat(text)
    if parsed_at is not None:
        if parsed_at.tzinfo is None:
            parsed_at = parsed_at.replace(tzinfo=UTC)
        return {"type": "once", "at": parsed_at.isoformat(), "timezone": timezone_name}
    schedule = {"type": "cron", "expression": text, "timezone": timezone_name}
    problems = validate_schedule(schedule)
    if problems:
        raise CronError("; ".join(problems))
    return schedule


@dataclass
class CronJob:
    """One scheduled job: prompt + skills + delivery + schedule."""

    id: str
    prompt: str
    schedule: dict
    deliver_to: str = ""
    skills: list[str] = field(default_factory=list)
    timezone: str = DEFAULT_TIMEZONE
    model: str | None = None
    provider: str | None = None
    script: str | None = None
    status: str = "active"
    next_run: datetime | None = None
    last_run: datetime | None = None
    last_result: str | None = None
    attempts: int = 0
    missed_runs: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_record(cls, record: dict) -> CronJob:
        """Build a job from a stored record (``.get()`` tolerant).

        Records written before ``timezone`` existed read as UTC;
        a missing ``status`` reads as active; a missing
        ``next_run`` is recomputed by the caller.
        """
        schedule = record.get("schedule") or {}
        if isinstance(schedule, str):
            schedule = parse_schedule_spec(schedule)
        timezone_name = record.get("timezone") or DEFAULT_TIMEZONE
        job = cls(
            id=str(record.get("id", f"cron-{uuid.uuid4().hex[:12]}")),
            prompt=str(record.get("prompt", "")),
            schedule=dict(schedule),
            deliver_to=str(record.get("deliver_to") or ""),
            skills=[str(s) for s in (record.get("skills") or [])],
            timezone=timezone_name,
            model=record.get("model"),
            provider=record.get("provider"),
            script=record.get("script"),
            status=record.get("status") or "active",
            attempts=int(record.get("attempts") or 0),
            missed_runs=int(record.get("missed_runs") or 0),
        )
        job.last_result = record.get("last_result")
        for attr, key in (
            ("next_run", "next_run"),
            ("last_run", "last_run"),
            ("created_at", "created_at"),
        ):
            raw = record.get(key)
            if isinstance(raw, str):
                with contextlib.suppress(ValueError):
                    setattr(job, attr, datetime.fromisoformat(raw))
        if job.next_run is None and job.status == "active":
            job.next_run = next_occurrence(job.schedule, job.created_at)
        return job

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "schedule": dict(self.schedule),
            "deliver_to": self.deliver_to,
            "skills": list(self.skills),
            "timezone": self.timezone,
            "model": self.model,
            "provider": self.provider,
            "script": self.script,
            "status": self.status,
            "next_run": (self.next_run.isoformat() if self.next_run else None),
            "last_run": (self.last_run.isoformat() if self.last_run else None),
            "last_result": self.last_result,
            "attempts": self.attempts,
            "missed_runs": self.missed_runs,
            "created_at": self.created_at.isoformat(),
        }

    def to_execution_dict(self) -> dict:
        """The dict ``execution.build_execution`` consumes."""
        return {
            "id": self.id,
            "prompt": self.prompt,
            "skills": list(self.skills),
            "deliver_to": self.deliver_to,
            "model": self.model,
            "provider": self.provider,
            "script": self.script,
        }


class CronJobStore:
    """File-backed job store with atomic saves.

    The file holds ``{"jobs": [...], "deliveries": [...]}``.
    Every save writes a temp file in the same directory, flushes
    + fsyncs it, then renames it over the store, so a crash can
    never leave a half-written jobs file behind.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.jobs: dict[str, CronJob] = {}
        self.deliveries: list[dict] = []
        self.load()

    def load(self) -> None:
        """Load jobs + delivery queue from disk (empty when absent)."""
        if not self.path.exists():
            self.jobs = {}
            self.deliveries = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise CronError(f"cannot read cron store {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise CronError(f"cron store {self.path} is not an object")
        self.jobs = {}
        for record in data.get("jobs") or []:
            if isinstance(record, dict) and record.get("id"):
                job = CronJob.from_record(record)
                self.jobs[job.id] = job
        self.deliveries = [dict(d) for d in (data.get("deliveries") or []) if isinstance(d, dict)]

    def save(self) -> None:
        """Persist jobs + delivery queue atomically."""
        data = {
            "jobs": [job.to_dict() for job in self.jobs.values()],
            "deliveries": self.deliveries,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, self.path)

    def get(self, job_id: str) -> CronJob | None:
        return self.jobs.get(str(job_id))

    def put(self, job: CronJob) -> None:
        self.jobs[job.id] = job
        self.save()

    def remove(self, job_id: str) -> bool:
        if self.jobs.pop(str(job_id), None) is None:
            return False
        self.save()
        return True

    def queue_delivery(self, *, job_id: str, run_id: str, target: str, text: str) -> dict:
        """Queue a delivery that could not be sent immediately."""
        delivery = {
            "id": f"delivery-{uuid.uuid4().hex[:12]}",
            "job_id": job_id,
            "run_id": run_id,
            "target": target,
            "text": text,
            "status": "queued",
            "attempts": 0,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.deliveries.append(delivery)
        self.save()
        return delivery

    def flush_deliveries(self, deliver) -> list[dict]:
        """Retry queued deliveries; return the ones still queued.

        A delivery that sends is dropped from the queue (the run
        already recorded the result); one that still fails stays
        queued for the next flush.
        """
        remaining: list[dict] = []
        for delivery in self.deliveries:
            if delivery.get("status") != "queued":
                continue
            delivery["attempts"] = int(delivery.get("attempts") or 0) + 1
            try:
                deliver(delivery["target"], delivery["text"])
            except Exception:
                remaining.append(delivery)
                continue
        self.deliveries = remaining
        self.save()
        return remaining


def _resolve_llm_complete(job: CronJob, llm_complete):
    """The live-LLM entry point, resolved lazily (router only)."""
    if llm_complete is not None:
        return llm_complete
    from puspa.router.complete import default_llm_complete

    kwargs: dict = {}
    if job.model:
        kwargs["model"] = job.model
    return default_llm_complete(role="reader", **kwargs)


def execute_job(job: CronJob, *, run_id: str, llm_complete=None, due_state: str = "due") -> str:
    """Execute one job with a fresh agent (no history).

    The execution prompt is composed by ``execution`` and the text
    comes back from ``llm_complete`` (injected in tests; the live
    router completion otherwise). No live call is faked: without
    a key the router raises, and that failure is real.
    """
    complete = _resolve_llm_complete(job, llm_complete)
    request = build_execution(job.to_execution_dict(), run_id=run_id, due_state=due_state)
    return complete(request.prompt)


def run_due_jobs(
    store: CronJobStore,
    *,
    now: datetime | None = None,
    llm_complete=None,
    deliver=None,
    sink=None,
    quota_hold=None,
    notify_owner=None,
) -> list[dict]:
    """Run every active job whose ``next_run`` has arrived.

    Missed runs (past the 12-hour grace) never execute: they are
    counted, reported through ``sink`` as ``run_missed``, and the
    job advances to its next occurrence. A job on quota hold is
    skipped the same way (reported as ``quota_hold``). A failed
    execution is requeued once (one minute later); a second
    failure is terminal, recorded, and the owner is notified.
    A delivery that fails is queued separately for retry instead
    of failing the run. Returns one outcome dict per job touched.
    """
    now = now or datetime.now(UTC)
    outcomes: list[dict] = []
    for job in sorted(store.jobs.values(), key=lambda j: (j.next_run or now, j.id)):
        if job.status != "active" or job.next_run is None:
            continue
        if job.next_run > now:
            continue
        state = due_mod.classify_due(job.next_run, now)
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        if state == "missed":
            job.missed_runs += 1
            job.last_run = now
            job.next_run = next_occurrence(job.schedule, now)
            if job.next_run is None:
                job.status = "completed"
            store.put(job)
            if sink is not None:
                with contextlib.suppress(Exception):
                    sink(
                        "run_missed",
                        {"job_id": job.id, "run_id": run_id, "due_at": job.last_run.isoformat()},
                    )
            outcomes.append({"job_id": job.id, "run_id": run_id, "status": "missed"})
            continue
        if quota_hold is not None:
            try:
                held = bool(quota_hold(job))
            except Exception:
                held = False
            if held:
                job.next_run = next_occurrence(job.schedule, now)
                if job.next_run is None:
                    job.status = "completed"
                store.put(job)
                if sink is not None:
                    with contextlib.suppress(Exception):
                        sink("quota_hold", {"job_id": job.id, "run_id": run_id})
                outcomes.append({"job_id": job.id, "run_id": run_id, "status": "quota_hold"})
                continue
        try:
            result = execute_job(job, run_id=run_id, llm_complete=llm_complete, due_state=state)
        except Exception as exc:
            record = handle_failure(
                job_id=job.id,
                run_id=run_id,
                error=str(exc),
                attempts=job.attempts,
                sink=sink,
                notify_owner=notify_owner,
                requeue=(lambda _j, _r: None),
            )
            job.attempts += 1
            if record.requeued:
                job.next_run = now + REQUEUE_DELAY
            else:
                job.next_run = next_occurrence(job.schedule, now)
                if job.next_run is None:
                    job.status = "completed"
            job.last_run = now
            store.put(job)
            outcomes.append(
                {
                    "job_id": job.id,
                    "run_id": run_id,
                    "status": "failed",
                    "requeued": record.requeued,
                    "error": str(exc),
                }
            )
            continue
        job.attempts = 0
        job.last_run = now
        job.last_result = result
        job.next_run = next_occurrence(job.schedule, now)
        if job.next_run is None:
            job.status = "completed"
        store.put(job)
        delivery: dict | None = None
        if deliver is not None and job.deliver_to:
            try:
                deliver(job.deliver_to, result)
            except Exception:
                delivery = store.queue_delivery(
                    job_id=job.id, run_id=run_id, target=job.deliver_to, text=result
                )
        if sink is not None:
            with contextlib.suppress(Exception):
                sink(
                    "run_completed",
                    {"job_id": job.id, "run_id": run_id, "delivery_queued": delivery is not None},
                )
        outcomes.append(
            {
                "job_id": job.id,
                "run_id": run_id,
                "status": "completed",
                "delivery_queued": delivery is not None,
            }
        )
    return outcomes


def cronjob_manage(store: CronJobStore, action: str, **kwargs) -> dict:
    """The one model tool for cron jobs.

    Actions: ``create``, ``list``, ``update``, ``pause``,
    ``resume``, ``run``, ``remove``. ``create`` takes ``prompt``
    plus a schedule (``schedule`` string/dict or the separate
    fields ``type``/``at``/``time``/``weekdays``/``seconds``/
    ``expression``) and an optional ``timezone``; the timezone is
    validated at this gate. ``run`` executes a job immediately
    through ``llm_complete``/``deliver`` callables passed in
    ``kwargs``.
    """
    action = str(action)
    if action == "list":
        jobs = [job.to_dict() for job in sorted(store.jobs.values(), key=lambda j: j.id)]
        return {"action": "list", "jobs": jobs}
    if action == "create":
        prompt = str(kwargs.get("prompt") or "").strip()
        if not prompt:
            raise CronError("create needs a non-empty 'prompt'")
        timezone_name = str(kwargs.get("timezone") or DEFAULT_TIMEZONE)
        if not is_valid_timezone(timezone_name):
            raise CronError(f"unknown timezone: {timezone_name!r}")
        raw_schedule = kwargs.get("schedule")
        now = kwargs.get("now") or datetime.now(UTC)
        if raw_schedule is None:
            user_input = {
                "type": kwargs.get("type"),
                "at": kwargs.get("at"),
                "time": kwargs.get("time"),
                "weekdays": kwargs.get("weekdays"),
                "seconds": kwargs.get("seconds"),
                "expression": kwargs.get("expression"),
                "timezone": timezone_name,
            }
            schedule, problems = RoutineScheduleInput.from_user_input(user_input)
            if problems:
                raise CronError("; ".join(problems))
            if schedule is None:
                raise CronError("invalid schedule")
        else:
            schedule = parse_schedule_spec(raw_schedule, now=now, timezone_name=timezone_name)
            schedule["timezone"] = timezone_name
        job = CronJob(
            id=f"cron-{uuid.uuid4().hex[:12]}",
            prompt=prompt,
            schedule=schedule,
            deliver_to=str(kwargs.get("deliver_to") or ""),
            skills=[str(s) for s in (kwargs.get("skills") or [])],
            timezone=timezone_name,
            model=kwargs.get("model"),
            provider=kwargs.get("provider"),
            script=kwargs.get("script"),
        )
        job.next_run = next_occurrence(schedule, now)
        if job.next_run is None:
            raise CronError("schedule has no future occurrence")
        store.put(job)
        return {"action": "create", "job": job.to_dict()}
    if action in ("update", "pause", "resume", "run", "remove"):
        existing = store.get(str(kwargs.get("job_id") or ""))
        if existing is None:
            raise CronError(f"no such cron job: {kwargs.get('job_id')!r}")
        job = existing
    if action == "update":
        new_timezone = kwargs.get("timezone")
        if new_timezone is not None:
            if not is_valid_timezone(str(new_timezone)):
                raise CronError(f"unknown timezone: {new_timezone!r}")
            job.timezone = str(new_timezone)
        if kwargs.get("schedule") is not None:
            schedule = parse_schedule_spec(kwargs["schedule"], timezone_name=job.timezone)
            schedule["timezone"] = job.timezone
            job.schedule = schedule
            now = kwargs.get("now") or datetime.now(UTC)
            job.next_run = next_occurrence(schedule, now)
        for field_name in ("prompt", "deliver_to", "model", "provider", "script"):
            if kwargs.get(field_name) is not None:
                setattr(job, field_name, kwargs[field_name])
        if kwargs.get("skills") is not None:
            job.skills = [str(s) for s in kwargs["skills"]]
        store.put(job)
        return {"action": "update", "job": job.to_dict()}
    if action == "pause":
        job.status = "paused"
        store.put(job)
        return {"action": "pause", "job": job.to_dict()}
    if action == "resume":
        job.status = "active"
        if job.next_run is None:
            job.next_run = next_occurrence(job.schedule, kwargs.get("now") or datetime.now(UTC))
        store.put(job)
        return {"action": "resume", "job": job.to_dict()}
    if action == "run":
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        result = execute_job(job, run_id=run_id, llm_complete=kwargs.get("llm_complete"))
        job.last_run = kwargs.get("now") or datetime.now(UTC)
        job.last_result = result
        store.put(job)
        deliver = kwargs.get("deliver")
        if deliver is not None and job.deliver_to:
            try:
                deliver(job.deliver_to, result)
            except Exception:
                store.queue_delivery(
                    job_id=job.id, run_id=run_id, target=job.deliver_to, text=result
                )
        return {"action": "run", "job_id": job.id, "run_id": run_id, "result": result}
    if action == "remove":
        store.remove(job.id)
        return {"action": "remove", "job_id": job.id}
    raise CronError(f"unknown cronjob_manage action: {action!r}")
