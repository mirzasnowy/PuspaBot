"""Puspa scheduler package: triggers, curriculum, and the general
cron scheduler (G8).

Public surface:

- ``scheduler`` — legacy Postgres triggers (schedule, bill due,
  price drop, manual) plus the dream / trust-root-refresh jobs.
  ``tick`` fires due triggers and runs their jobs through
  ``run_trigger_job``; ``serve`` runs the tick on a timer.
- ``routines`` — user-defined recurring tasks on top of the
  triggers: create, list, pause, resume, remove. A due routine
  runs through the turn loop with no human present.
- ``curriculum`` — the idle-time learning runner (policy on top of
  the general scheduler: max 5 tasks/day, Rp2.000/task ceiling).
- ``schedules`` — the explicit schedule union + timezone handling.
- ``due`` — due / late / missed classification (12h grace).
- ``execution`` — fresh-agent execution-prompt composition.
- ``recovery`` — requeue-once failure handling + owner notice.
- ``holds`` — holds, quiet hours, and check pacing.
- ``receipts`` — idempotency receipts for events and webhooks.
- ``claim`` — multi-replica work claiming (SKIP LOCKED + lease).
- ``cron`` — the general cron scheduler + ``cronjob_manage`` tool.
"""

from .claim import InMemoryWorkQueue, WorkItem  # noqa: F401
from .cron import CronJob, CronJobStore, cronjob_manage  # noqa: F401
from .curriculum import CurriculumRunner, run_curriculum_tick  # noqa: F401
from .routines import (  # noqa: F401
    Routine,
    RoutineError,
    create_routine,
    list_routines,
    pause_routine,
    remove_routine,
    resume_routine,
)
from .scheduler import run_trigger_job, tick  # noqa: F401
from .schedules import RoutineScheduleInput  # noqa: F401
from .serve import run_scheduler_loop  # noqa: F401
