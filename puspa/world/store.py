"""Postgres persistence for the world model (one row per agent).

The world model itself is in-memory plain data (``WorldModel.state()``),
so persistence is a JSON snapshot: ``save_world_state`` serializes the
state, ``load_world_state`` deserializes it back into a state dict
that ``WorldModel._restore_state`` accepts, and ``load_world_model``
does the whole round trip. One row per (user_id, agent_id); a later
save overwrites the earlier row (upsert).

What is stored: entities, relations, day, price beliefs, bill
beliefs, schedule beliefs, capability beliefs, contact beliefs,
habit beliefs, and belief contradictions. That is the durable
knowledge: beliefs and the corrections folded into them. No
schema change was needed for the contact and habit beliefs:
``world_beliefs.state`` is one JSONB snapshot, so the new keys
ride in the same column (see ``infra/schema_world.sql``). What is NOT stored: the transient
prediction stats (``stats``, ``episode_accuracies``,
``mispredictions``), the pending prediction, and the session
learning loop (its corrections live in ``LearningLoop`` in memory;
persisting them is a separate step). The model's ``learning_loop``
wiring attribute is runtime state, never part of ``state()``.

Belief values in ``state()`` are dataclass instances, not JSON, so
serialization converts each belief with ``dataclasses.asdict`` and
deserialization rebuilds the dataclass from the stored dict. The
round trip is exact for plain JSON values; anything else is stored
through ``default=str`` so a save never crashes on an odd entity
attribute (and is documented as lossy for that attribute only).

Connection pattern follows ``puspa.ledger``: DSN from the argument,
else ``PUSPA_TEST_DSN``, else the peer-auth default. No new
dependency (``psycopg`` only).

Bitemporal beliefs (Kosmos K3, ``puspa.world.bitemporal``) are
persisted separately from the snapshot, in table
``belief_versions`` (one row per belief version, two time pairs
per row; see ``infra/schema_world.sql`` and
``infra/migrate_bitemporal.sql``). The snapshot in
``world_beliefs.state`` keeps working exactly as before — the
version table is additive, and existing callers never see it
unless they ask for it.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .bitemporal import BeliefVersion, BitemporalBeliefStore
from .model import (
    WorldModel,
    _BillBelief,
    _CapabilityBelief,
    _ContactBelief,
    _HabitBelief,
    _PriceBelief,
    _ScheduleBelief,
)

#: DSN default (peer auth sebagai user OS, tanpa password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

_BELIEF_TYPES = {
    "price_beliefs": _PriceBelief,
    "bill_beliefs": _BillBelief,
    "schedule_beliefs": _ScheduleBelief,
    "capability_beliefs": _CapabilityBelief,
    "contact_beliefs": _ContactBelief,
    "habit_beliefs": _HabitBelief,
}

#: Keys of a world state dict (see ``WorldModel.state``).
_STATE_KEYS = (
    "entities",
    "relations",
    "day",
    "price_beliefs",
    "bill_beliefs",
    "schedule_beliefs",
    "capability_beliefs",
    "contact_beliefs",
    "habit_beliefs",
    "belief_contradictions",
)


def _resolve_dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


def create_schema(dsn: str | None = None) -> None:
    """Run `infra/schema_world.sql` (idempotent).

    Prerequisite: the Phase 0 schema (users, agents) already exists,
    because `world_beliefs` references both tables.
    """
    sql = (Path(__file__).resolve().parent.parent.parent / "infra" / "schema_world.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(_resolve_dsn(dsn), autocommit=True) as conn:
        conn.execute(sql)


def serialize_state(state: dict) -> dict:
    """World state dict -> JSON-safe dict (belief dataclasses to dicts)."""
    out: dict[str, Any] = {}
    for key in _STATE_KEYS:
        value = state.get(key)
        if key in _BELIEF_TYPES:
            out[key] = {name: asdict(belief) for name, belief in (value or {}).items()}
        else:
            out[key] = value
    return out


def deserialize_state(data: dict) -> dict:
    """Stored JSON dict -> state dict for ``WorldModel._restore_state``."""
    out: dict[str, Any] = {}
    for key in _STATE_KEYS:
        value = data.get(key)
        if key in _BELIEF_TYPES:
            belief_type = _BELIEF_TYPES[key]
            out[key] = {name: belief_type(**belief) for name, belief in (value or {}).items()}
        else:
            out[key] = value
    if out.get("entities") is None:
        out["entities"] = {}
    if out.get("relations") is None:
        out["relations"] = []
    if out.get("day") is None:
        out["day"] = 0
    if out.get("belief_contradictions") is None:
        out["belief_contradictions"] = []
    return out


def state_to_json(state: dict) -> str:
    """Canonical JSON for one world state (stable key order)."""
    return json.dumps(
        serialize_state(state),
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def save_world_state(user_id, agent_id, model, *, dsn=None) -> dict:
    """Save a world model's state (one row per user/agent, upsert).

    ``model`` is a ``WorldModel`` or a raw state dict. Returns the
    saved row (user_id, agent_id, updated_at).
    """
    state = model.state if isinstance(model, WorldModel) else model
    payload = state_to_json(state)
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO world_beliefs (user_id, agent_id, state)"
                " VALUES (%s, %s, %s::jsonb)"
                " ON CONFLICT (user_id, agent_id) DO UPDATE"
                " SET state = EXCLUDED.state, updated_at = now()"
                " RETURNING user_id, agent_id, updated_at",
                (str(user_id), str(agent_id), payload),
            )
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError("world state save returned no row")
    return {
        "user_id": str(row["user_id"]),
        "agent_id": row["agent_id"],
        "updated_at": row["updated_at"].isoformat(),
    }


def load_world_state(user_id, agent_id, *, dsn=None) -> dict | None:
    """Load a saved world state, or None when nothing was saved."""
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT state FROM world_beliefs WHERE user_id = %s AND agent_id = %s",
            (str(user_id), str(agent_id)),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return deserialize_state(row["state"])


def load_world_model(user_id, agent_id, *, dsn=None) -> WorldModel | None:
    """Load a saved world model, or None when nothing was saved.

    The returned model has no session learning loop attached; the
    caller (session / TaskRunner) sets ``model.learning_loop``.
    """
    state = load_world_state(user_id, agent_id, dsn=dsn)
    if state is None:
        return None
    model = WorldModel()
    model._restore_state(state)
    return model


def delete_world_state(user_id, agent_id, *, dsn=None) -> bool:
    """Delete the saved row for one (user, agent). Tests only."""
    with psycopg.connect(_resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM world_beliefs WHERE user_id = %s AND agent_id = %s",
                (str(user_id), str(agent_id)),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


# -- bitemporal belief versions (Kosmos K3) ---------------------------
def apply_bitemporal_migration(dsn: str | None = None) -> None:
    """Run `infra/migrate_bitemporal.sql` (idempotent).

    Creates table `belief_versions` when missing and adds
    `events.valid_at` (backfilled from `created_at`) when the
    ledger table exists. Safe to run repeatedly and safe to run
    against a database that already has both. Run this once
    against an existing database; fresh databases get the same
    objects from `create_schema` (`schema_world.sql`) and
    `Ledger.create_schema` (`puspa/ledger/schema.sql`).
    """
    sql = (
        Path(__file__).resolve().parent.parent.parent / "infra" / "migrate_bitemporal.sql"
    ).read_text(encoding="utf-8")
    with psycopg.connect(_resolve_dsn(dsn), autocommit=True) as conn:
        conn.execute(sql)


def _belief_row_to_version(row: dict) -> BeliefVersion:
    return BeliefVersion(
        key=row["belief_key"],
        value=row["value"],
        version=row["version"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        known_from=row["known_from"],
        known_to=row["known_to"],
        supersedes=row["supersedes"],
    )


def _belief_version_to_dict(version: BeliefVersion) -> dict:
    return {
        "belief_key": version.key,
        "version": version.version,
        "value": version.value,
        "valid_from": version.valid_from.isoformat(),
        "valid_to": version.valid_to.isoformat() if version.valid_to else None,
        "known_from": version.known_from.isoformat(),
        "known_to": version.known_to.isoformat() if version.known_to else None,
        "supersedes": version.supersedes,
    }


def save_belief_versions(user_id, agent_id, history, *, dsn=None) -> int:
    """Persist a bitemporal history (one row per belief version).

    ``history`` is a ``BitemporalBeliefStore``. Versions are
    immutable: re-saving the same history inserts nothing
    (`ON CONFLICT DO NOTHING` on the (user, agent, key, version)
    primary key). Returns the number of rows actually inserted.
    """
    if not isinstance(history, BitemporalBeliefStore):
        raise ValueError("history must be a BitemporalBeliefStore")
    rows = [version for key in history for version in history.history(key)]
    if not rows:
        return 0
    with psycopg.connect(_resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO belief_versions"
                " (user_id, agent_id, belief_key, version, value,"
                "  valid_from, valid_to, known_from, known_to, supersedes)"
                " VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)"
                " ON CONFLICT (user_id, agent_id, belief_key, version)"
                " DO NOTHING",
                [
                    (
                        str(user_id),
                        str(agent_id),
                        version.key,
                        version.version,
                        json.dumps(version.value, ensure_ascii=False),
                        version.valid_from,
                        version.valid_to,
                        version.known_from,
                        version.known_to,
                        version.supersedes,
                    )
                    for version in rows
                ],
            )
            inserted = cur.rowcount
        conn.commit()
    return inserted


def load_belief_history(user_id, agent_id, *, dsn=None) -> BitemporalBeliefStore | None:
    """Load the full version history, or None when nothing was saved."""
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT belief_key, version, value, valid_from, valid_to,"
            " known_from, known_to, supersedes FROM belief_versions"
            " WHERE user_id = %s AND agent_id = %s"
            " ORDER BY belief_key, version",
            (str(user_id), str(agent_id)),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    return BitemporalBeliefStore.from_versions([_belief_row_to_version(row) for row in rows])


def beliefs_known_at(
    user_id, agent_id, at: datetime, *, belief_key: str | None = None, dsn=None
) -> list[dict]:
    """Beliefs as the system knew them at ``at`` (known time).

    One row per key: the version whose known interval contains
    ``at``. ``at`` must be timezone-aware. Pass ``belief_key``
    to ask about one belief only.
    """
    if at.tzinfo is None:
        raise ValueError("at must be a timezone-aware datetime")
    query = (
        "SELECT belief_key, version, value, valid_from, valid_to,"
        " known_from, known_to, supersedes FROM belief_versions"
        " WHERE user_id = %s AND agent_id = %s"
        " AND known_from <= %s AND (known_to IS NULL OR known_to > %s)"
    )
    params: list[Any] = [str(user_id), str(agent_id), at, at]
    if belief_key is not None:
        query += " AND belief_key = %s"
        params.append(belief_key)
    query += " ORDER BY belief_key"
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [_belief_version_to_dict(_belief_row_to_version(row)) for row in rows]


def beliefs_valid_at(
    user_id, agent_id, at: datetime, *, belief_key: str | None = None, dsn=None
) -> list[dict]:
    """Beliefs true at ``at`` under current knowledge (valid time).

    Per key, the latest version whose valid interval covers
    ``at``: a later correction overrides an earlier claim for
    the valid times it covers, and a world-change correction
    does not cover the old fact's interval, so the old fact
    still reads true then. ``at`` must be timezone-aware.
    """
    if at.tzinfo is None:
        raise ValueError("at must be a timezone-aware datetime")
    query = (
        "SELECT DISTINCT ON (belief_key) belief_key, version, value,"
        " valid_from, valid_to, known_from, known_to, supersedes"
        " FROM belief_versions"
        " WHERE user_id = %s AND agent_id = %s"
        " AND valid_from <= %s AND (valid_to IS NULL OR valid_to > %s)"
    )
    params: list[Any] = [str(user_id), str(agent_id), at, at]
    if belief_key is not None:
        query += " AND belief_key = %s"
        params.append(belief_key)
    query += " ORDER BY belief_key, version DESC"
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [_belief_version_to_dict(_belief_row_to_version(row)) for row in rows]


def delete_belief_history(user_id, agent_id, *, dsn=None) -> bool:
    """Delete all saved belief versions for one (user, agent). Tests only."""
    with psycopg.connect(_resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM belief_versions WHERE user_id = %s AND agent_id = %s",
                (str(user_id), str(agent_id)),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
