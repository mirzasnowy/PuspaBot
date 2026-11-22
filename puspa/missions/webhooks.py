"""Wake a mission from outside with a webhook (V2-42, issue #219).

Mechanism studied from nouverse/nouride cron webhooks
(FSL-1.1-ALv2, verified 22 Nov 2026) and rewritten as Puspa
Python, never verbatim; the TypeScript code itself was not taken.

Each mission carries a random webhook token. A POST to
``/api/missions/webhook/<token>`` wakes that mission: the
payload is recorded in the ledger as evidence, and the next
step is queued — an immediate one-shot scheduler trigger, so
the serve loop runs it in the background like any other
trigger, never inline in the webhook request. A caller that
already has a turn loop (the scheduler tick, tests) may pass
it in and the step runs right here instead.

The token is the credential: an unknown token is refused
without saying whether any mission exists, and rotating the
token invalidates the old one at once.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta

import psycopg

from .missions import (
    Mission,
    MissionError,
    _connect,
    _ledger,
    _row_to_mission,
    get_mission,
)

#: How much of a webhook payload is kept in the ledger.
PAYLOAD_MAX_CHARS = 2000


def _mission_by_token(token: str, *, dsn=None) -> Mission:
    """The mission a webhook token belongs to, or raise."""
    with _connect(dsn) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT id, user_id, title, goal, status, steps, webhook_token,"
            " created_at, updated_at FROM missions WHERE webhook_token = %s",
            (str(token),),
        )
        row = cur.fetchone()
    if row is None:
        raise MissionError("unknown webhook token")
    return _row_to_mission(row)


def wake_mission_by_token(
    token: str,
    payload,
    *,
    dsn=None,
    turn_loop=None,
    step_runner=None,
) -> dict:
    """Wake a mission with a webhook payload.

    Records the payload in the ledger, then either runs the
    next step now (when a turn loop or step runner is given)
    or queues an immediate one-shot scheduler trigger for it
    (the PWA webhook endpoint: the serve loop runs the step
    in the background). A finished or waiting mission is not
    woken: the wake is recorded, and the outcome says so.
    """
    mission = _mission_by_token(token, dsn=dsn)
    if isinstance(payload, str):
        payload_text = payload[:PAYLOAD_MAX_CHARS]
    else:
        try:
            payload_text = json.dumps(payload, ensure_ascii=False)[:PAYLOAD_MAX_CHARS]
        except (TypeError, ValueError):
            payload_text = str(payload)[:PAYLOAD_MAX_CHARS]
    _ledger(
        mission.id,
        "evidence_recorded",
        {"kind": "mission_webhook", "mission_id": mission.id, "payload": payload_text},
        dsn=dsn,
    )
    if mission.status in ("done", "failed", "stopped"):
        return {"status": "already_finished", "mission_id": mission.id}
    if mission.status == "waiting":
        return {"status": "waiting", "mission_id": mission.id}
    if turn_loop is None and step_runner is None:
        from puspa.scheduler.mission_triggers import schedule_mission

        # A few seconds ahead: a "once" schedule only fires
        # when its time is still ahead, and "now" is already
        # past by the time the trigger row is written.
        trigger = schedule_mission(
            mission.user_id,
            mission.id,
            {
                "type": "once",
                "at": (datetime.now(UTC) + timedelta(seconds=10)).isoformat(),
            },
            dsn=dsn,
        )
        return {"status": "queued", "mission_id": mission.id, "trigger_id": trigger.id}
    from .runner import advance_mission

    outcome = advance_mission(
        mission.id,
        mission.user_id,
        dsn=dsn,
        turn_loop=turn_loop,
        step_runner=step_runner,
    )
    return {"status": outcome["status"], "mission_id": mission.id, "outcome": outcome}


def rotate_webhook_token(mission_id: str, user_id: str, *, dsn=None) -> str:
    """Give a mission a fresh webhook token; the old one stops working."""
    mission = get_mission(mission_id, user_id, dsn=dsn)
    token = secrets.token_urlsafe(24)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE missions SET webhook_token = %s, updated_at = %s"
                " WHERE id = %s AND user_id = %s",
                (token, datetime.now(UTC), mission.id, mission.user_id),
            )
        conn.commit()
    return token
