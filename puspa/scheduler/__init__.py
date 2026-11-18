"""Puspa scheduler package: triggers, curriculum, and the general
cron scheduler (G8).

Public surface:

- ``scheduler`` — legacy Postgres triggers (schedule, bill due,
  price drop, manual) plus the dream / trust-root-refresh jobs.
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
from .schedules import RoutineScheduleInput  # noqa: F401
