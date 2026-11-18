"""Postgres persistence for the learning loop corrections.

The learning loop itself is in-memory plain data
(``LearningLoop.loop_state()``), so persistence is a snapshot of
that state: ``save_loop_state`` writes it to Postgres,
``load_loop_state`` reads it back, and ``load_learning_loop``
does the whole round trip into a fresh loop. The API mirrors
``puspa.world.store`` (``create_schema`` / save / load / delete),
but the storage shape is different on purpose, which is why this
is a separate module instead of an extension of the world store:
the world model is one JSON snapshot row per (user, agent), while
the loop keeps one row per correction in ``loop_corrections`` plus
one row per correction-carrying experience in
``loop_experiences`` (``infra/schema_learning_loop.sql``).

What is stored: the correction table (latest correction per task,
with its count and the experience ids that built it) and the
experiences that carry a correction (every misprediction
experience, and every user-feedback experience whose correction
was applied). What is NOT stored: experiences without a
correction (a "correct" verdict, a "wrong" verdict without a
correction), draft skills, manual edits, and feedback proposals.
Gaps are not stored either: they are rebuilt from the corrections
on restore (count and last error), so ``propose_next_task`` offers
the same retry after a restart.

Connection pattern follows ``puspa.world.store``: DSN from the
argument, else ``PUSPA_TEST_DSN``, else the peer-auth default. No
new dependency (``psycopg`` only).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .learning_loop import LearningLoop

#: DSN default (peer auth as the OS user, no password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def _resolve_dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


def _to_json(value) -> str:
    """A value as canonical JSON text (odd values via str, never a crash)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def create_schema(dsn: str | None = None) -> None:
    """Run `infra/schema_learning_loop.sql` (idempotent).

    Prerequisite: the Phase 0 schema (users, agents) already exists,
    because both tables reference them.
    """
    sql = (
        Path(__file__).resolve().parent.parent.parent / "infra" / "schema_learning_loop.sql"
    ).read_text(encoding="utf-8")
    with psycopg.connect(_resolve_dsn(dsn), autocommit=True) as conn:
        conn.execute(sql)


def serialize_loop(loop) -> dict:
    """A loop (or a raw loop state dict) -> JSON-safe state dict.

    Only the experiences that carry a correction are kept: an
    experience is kept when its id appears in some correction's
    ``experience_ids``. The rest change no prediction and are
    dropped here, so a round trip through Postgres never stores
    them.
    """
    state = loop.loop_state() if isinstance(loop, LearningLoop) else loop
    corrections = state.get("corrections") or {}
    experiences = state.get("experiences") or []
    kept_ids = {
        experience_id
        for correction in corrections.values()
        for experience_id in (correction.get("experience_ids") or [])
    }
    clean_corrections = {
        str(task): json.loads(_to_json(correction)) for task, correction in corrections.items()
    }
    clean_experiences = [
        json.loads(_to_json(experience))
        for experience in experiences
        if experience.get("id") in kept_ids
    ]
    return {"corrections": clean_corrections, "experiences": clean_experiences}


def deserialize_loop(data: dict) -> LearningLoop:
    """A stored state dict -> a fresh LearningLoop with it restored."""
    loop = LearningLoop()
    loop.restore_loop_state(data or {})
    return loop


def save_loop_state(user_id, agent_id, loop, *, dsn=None) -> dict:
    """Save a loop's corrections (one row per task, upsert).

    ``loop`` is a ``LearningLoop`` or a raw state dict. Correction
    rows are upserted (a later save overwrites the earlier row for
    the same task); experience rows are inserted once and ignored
    on conflict (experiences are append-only). Returns the saved
    counts (user_id, agent_id, corrections, experiences).
    """
    state = serialize_loop(loop)
    corrections = state["corrections"]
    experiences = state["experiences"]
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            for task, correction in corrections.items():
                cur.execute(
                    "INSERT INTO loop_corrections"
                    " (user_id, agent_id, task, predicted, observed,"
                    " error, source, feedback_source, correction_count,"
                    " experience_ids)"
                    " VALUES (%s, %s, %s, %s::jsonb, %s::jsonb,"
                    " %s, %s, %s, %s, %s::jsonb)"
                    " ON CONFLICT (user_id, agent_id, task) DO UPDATE"
                    " SET predicted = EXCLUDED.predicted,"
                    " observed = EXCLUDED.observed,"
                    " error = EXCLUDED.error,"
                    " source = EXCLUDED.source,"
                    " feedback_source = EXCLUDED.feedback_source,"
                    " correction_count = EXCLUDED.correction_count,"
                    " experience_ids = EXCLUDED.experience_ids,"
                    " updated_at = now()",
                    (
                        str(user_id),
                        str(agent_id),
                        task,
                        _to_json(correction.get("predicted")),
                        _to_json(correction.get("observed")),
                        correction.get("error"),
                        correction.get("source"),
                        correction.get("feedback_source"),
                        int(correction.get("count", 1)),
                        _to_json(correction.get("experience_ids") or []),
                    ),
                )
            for position, experience in enumerate(experiences):
                cur.execute(
                    "INSERT INTO loop_experiences"
                    " (id, user_id, agent_id, task, kind, source,"
                    " feedback_source, verdict, predicted, observed,"
                    " error, context, position)"
                    " VALUES (%s, %s, %s, %s, %s, %s,"
                    " %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s)"
                    " ON CONFLICT (id) DO NOTHING",
                    (
                        str(experience["id"]),
                        str(user_id),
                        str(agent_id),
                        str(experience.get("task")),
                        experience.get("kind"),
                        experience.get("source"),
                        experience.get("feedback_source"),
                        experience.get("verdict"),
                        _to_json(experience.get("predicted")),
                        _to_json(experience.get("observed")),
                        experience.get("error"),
                        _to_json(experience.get("context") or {}),
                        position,
                    ),
                )
        conn.commit()
    return {
        "user_id": str(user_id),
        "agent_id": str(agent_id),
        "corrections": len(corrections),
        "experiences": len(experiences),
    }


def load_loop_state(user_id, agent_id, *, dsn=None) -> dict | None:
    """Load a saved loop state, or None when nothing was saved."""
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT task, predicted, observed, error, source,"
            " feedback_source, correction_count, experience_ids"
            " FROM loop_corrections"
            " WHERE user_id = %s AND agent_id = %s",
            (str(user_id), str(agent_id)),
        )
        correction_rows = cur.fetchall()
        cur.execute(
            "SELECT id, task, kind, source, feedback_source,"
            " verdict, predicted, observed, error, context"
            " FROM loop_experiences"
            " WHERE user_id = %s AND agent_id = %s"
            " ORDER BY position",
            (str(user_id), str(agent_id)),
        )
        experience_rows = cur.fetchall()
    if not correction_rows and not experience_rows:
        return None
    corrections = {}
    for row in correction_rows:
        corrections[row["task"]] = {
            "task": row["task"],
            "predicted": row["predicted"],
            "observed": row["observed"],
            "error": row["error"],
            "count": row["correction_count"],
            "experience_ids": list(row["experience_ids"] or []),
        }
        if row["source"] is not None:
            corrections[row["task"]]["source"] = row["source"]
        if row["feedback_source"] is not None:
            corrections[row["task"]]["feedback_source"] = row["feedback_source"]
    experiences = []
    for row in experience_rows:
        experience = {
            "id": row["id"],
            "task": row["task"],
            "predicted": row["predicted"],
            "observed": row["observed"],
            "error": row["error"],
            "context": row["context"] or {},
        }
        for key in ("kind", "source", "feedback_source", "verdict"):
            if row[key] is not None:
                experience[key] = row[key]
        experiences.append(experience)
    return {"corrections": corrections, "experiences": experiences}


def load_learning_loop(user_id, agent_id, *, dsn=None) -> LearningLoop | None:
    """Load a saved learning loop, or None when nothing was saved.

    The returned loop is fresh (new instance) with the saved
    corrections restored, so `predict` answers with the corrected
    values right away.
    """
    state = load_loop_state(user_id, agent_id, dsn=dsn)
    if state is None:
        return None
    return deserialize_loop(state)


def delete_loop_state(user_id, agent_id, *, dsn=None) -> bool:
    """Delete the saved rows for one (user, agent). Tests only."""
    with psycopg.connect(_resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM loop_experiences WHERE user_id = %s AND agent_id = %s",
                (str(user_id), str(agent_id)),
            )
            experiences_deleted = cur.rowcount
            cur.execute(
                "DELETE FROM loop_corrections WHERE user_id = %s AND agent_id = %s",
                (str(user_id), str(agent_id)),
            )
            corrections_deleted = cur.rowcount
        conn.commit()
    return (experiences_deleted + corrections_deleted) > 0
