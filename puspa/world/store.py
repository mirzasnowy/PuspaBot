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
ride in the same column (see migration 0006_world). What is NOT stored: the transient
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
per row; see migration 0006_world and
migration 0006_world). The snapshot in
``world_beliefs.state`` keeps working exactly as before — the
version table is additive, and existing callers never see it
unless they ask for it.

Causal hypotheses (Kosmos K4, ``puspa.world.causal``) are
persisted the same way, in table ``causal_hypotheses`` (one
row per hypothesis; see migration 0006_world and
migration 0006_world). Hypotheses mutate in
place (strength updates, status changes), so saving upserts
by id; expired and contradicted rows are kept as history.

Other-agent goal models (Kosmos K5, ``puspa.world.agents``)
are persisted in table ``agent_goal_models`` (one row per
(user, agent, contact); see migration 0006_world and
migration 0006_world). A model mutates in
place (goal strengths, predictions, accuracy), so saving
upserts by (user, agent, contact); dropped goals are kept in
the row's goals JSONB as history. The world snapshot in
``world_beliefs.state`` also carries the agent models under
``"agent_models"`` (same round trip as the other beliefs),
so a plain ``save_world_state`` / ``load_world_model`` keeps
them without a second call.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from puspa.migrations import apply_migrations

from .agents import AgentGoalModel, AgentGoalStore
from .beliefs import (
    _BillBelief,
    _CapabilityBelief,
    _ContactBelief,
    _HabitBelief,
    _PriceBelief,
    _ScheduleBelief,
)
from .bitemporal import BeliefVersion, BitemporalBeliefStore
from .causal import CausalHypothesis, CausalLedger
from .model import WorldModel

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
    "agent_models",
    "belief_contradictions",
)


def _resolve_dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


def create_schema(dsn: str | None = None) -> None:
    """Apply the world tables (migration 0006_world).

    Prerequisite: the Phase 0 schema (users, agents) already exists,
    because `world_beliefs` references both tables.
    """
    apply_migrations(_resolve_dsn(dsn), target="0006")


def serialize_state(state: dict) -> dict:
    """World state dict -> JSON-safe dict (belief dataclasses to dicts)."""
    out: dict[str, Any] = {}
    for key in _STATE_KEYS:
        value = state.get(key)
        if key in _BELIEF_TYPES:
            out[key] = {name: asdict(belief) for name, belief in (value or {}).items()}
        elif key == "agent_models":
            out[key] = {name: model.to_dict() for name, model in (value or {}).items()}
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
        elif key == "agent_models":
            out[key] = {
                name: AgentGoalModel.from_dict(model) for name, model in (value or {}).items()
            }
        else:
            out[key] = value
    if out.get("entities") is None:
        out["entities"] = {}
    if out.get("relations") is None:
        out["relations"] = []
    if out.get("day") is None:
        out["day"] = 0
    if out.get("agent_models") is None:
        out["agent_models"] = {}
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
    """Apply the world tables (migration 0006_world).

    Kept as a named entry point: the `belief_versions` table and the
    `events.valid_at` column used to come from the old upgrade script
    `migrate_bitemporal.sql`; both now live in migration 0006_world,
    so this applies everything up to that migration. Safe to run
    repeatedly.
    """
    apply_migrations(_resolve_dsn(dsn), target="0006")


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


# -- causal hypotheses (Kosmos K4) ------------------------------------
def apply_causal_migration(dsn: str | None = None) -> None:
    """Apply the world tables (migration 0006_world).

    Kept as a named entry point: the `causal_hypotheses` table used
    to come from the old upgrade script `migrate_causal_ledger.sql`;
    it now lives in migration 0006_world, so this applies everything
    up to that migration. Safe to run repeatedly.
    """
    apply_migrations(_resolve_dsn(dsn), target="0006")


def _causal_row_to_hypothesis(row: dict) -> CausalHypothesis:
    # Postgres returns a UUID object (dashed form); in-memory ids
    # are `uuid4().hex` (no dashes), so normalize on load, or a
    # loaded ledger would never match the ids it was saved with.
    return CausalHypothesis(
        id=str(row["id"]).replace("-", ""),
        cause=row["cause"],
        effect=row["effect"],
        condition=row["condition"],
        strength=float(row["strength"]),
        evidence=[dict(link) for link in (row["evidence"] or [])],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        status=row["status"],
        effect_value=row["effect_value"],
        supporting=int(row["supporting"]),
        contradicting=int(row["contradicting"]),
    )


def _causal_hypothesis_to_dict(hypothesis: CausalHypothesis) -> dict:
    return hypothesis.to_dict()


def save_causal_hypotheses(user_id, agent_id, ledger, *, dsn=None) -> int:
    """Persist a causal ledger (one row per hypothesis).

    `ledger` is a `CausalLedger`. Hypotheses mutate in place
    (strength updates, status changes), so saving upserts by id
    (`ON CONFLICT (id) DO UPDATE`): re-saving the same ledger
    writes the same rows again with their current values.
    Expired and contradicted hypotheses are saved too — they
    are history, and the read path filters them. Returns the
    number of rows written.
    """
    if not isinstance(ledger, CausalLedger):
        raise ValueError("ledger must be a CausalLedger")
    rows = [CausalHypothesis.from_dict(row) for row in ledger.list_all(include_terminal=True)]
    if not rows:
        return 0
    with psycopg.connect(_resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO causal_hypotheses"
                " (id, user_id, agent_id, cause, effect, effect_value,"
                "  condition, strength, evidence, created_at, expires_at,"
                "  valid_from, valid_to, status, supporting, contradicting,"
                "  updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb,"
                "  %s, %s, %s, %s, %s, %s, %s, now())"
                " ON CONFLICT (id) DO UPDATE SET"
                "  strength = EXCLUDED.strength,"
                "  evidence = EXCLUDED.evidence,"
                "  expires_at = EXCLUDED.expires_at,"
                "  valid_to = EXCLUDED.valid_to,"
                "  status = EXCLUDED.status,"
                "  effect_value = EXCLUDED.effect_value,"
                "  supporting = EXCLUDED.supporting,"
                "  contradicting = EXCLUDED.contradicting,"
                "  updated_at = now()",
                [
                    (
                        hypothesis.id,
                        str(user_id),
                        str(agent_id),
                        hypothesis.cause,
                        hypothesis.effect,
                        json.dumps(hypothesis.effect_value, ensure_ascii=False, default=str),
                        hypothesis.condition,
                        hypothesis.strength,
                        json.dumps(hypothesis.evidence, ensure_ascii=False),
                        hypothesis.created_at,
                        hypothesis.expires_at,
                        hypothesis.valid_from,
                        hypothesis.valid_to,
                        hypothesis.status,
                        hypothesis.supporting,
                        hypothesis.contradicting,
                    )
                    for hypothesis in rows
                ],
            )
            written = cur.rowcount
        conn.commit()
    return written


def load_causal_ledger(user_id, agent_id, *, dsn=None) -> CausalLedger | None:
    """Load the full causal history, or None when nothing was saved.

    Returns every hypothesis including expired and
    contradicted ones (history is preserved); the read path
    (`CausalLedger.expect`) filters to active ones.
    """
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, cause, effect, effect_value, condition, strength,"
            " evidence, created_at, expires_at, valid_from, valid_to,"
            " status, supporting, contradicting FROM causal_hypotheses"
            " WHERE user_id = %s AND agent_id = %s"
            " ORDER BY created_at, id",
            (str(user_id), str(agent_id)),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    return CausalLedger.from_dict(
        {"hypotheses": [_causal_hypothesis_to_dict(_causal_row_to_hypothesis(row)) for row in rows]}
    )


def causal_hypotheses_valid_at(
    user_id, agent_id, at: datetime, *, cause: str | None = None, dsn=None
) -> list[dict]:
    """Causal claims that held at ``at`` (valid time).

    Every hypothesis whose valid interval covers ``at`` — one
    row per claim, including expired and contradicted ones
    whose interval still covers the moment asked about. This
    is the K3 bitemporal question applied to causal claims:
    "what did the system believe caused what, then?" ``at``
    must be timezone-aware. Pass ``cause`` to ask about one
    cause only.
    """
    if at.tzinfo is None:
        raise ValueError("at must be a timezone-aware datetime")
    query = (
        "SELECT id, cause, effect, effect_value, condition, strength,"
        " evidence, created_at, expires_at, valid_from, valid_to,"
        " status, supporting, contradicting FROM causal_hypotheses"
        " WHERE user_id = %s AND agent_id = %s"
        " AND valid_from <= %s AND (valid_to IS NULL OR valid_to > %s)"
    )
    params: list[Any] = [str(user_id), str(agent_id), at, at]
    if cause is not None:
        query += " AND cause = %s"
        params.append(cause)
    query += " ORDER BY cause, condition, effect"
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [_causal_hypothesis_to_dict(_causal_row_to_hypothesis(row)) for row in rows]


def delete_causal_hypotheses(user_id, agent_id, *, dsn=None) -> bool:
    """Delete all saved causal hypotheses for one (user, agent). Tests only."""
    with psycopg.connect(_resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM causal_hypotheses WHERE user_id = %s AND agent_id = %s",
                (str(user_id), str(agent_id)),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


# -- other-agent goal models (Kosmos K5) ------------------------------
def apply_agent_migration(dsn: str | None = None) -> None:
    """Apply the world tables (migration 0006_world).

    Kept as a named entry point: the `agent_goal_models` table used
    to come from the old upgrade script
    `migrate_agent_goal_models.sql`; it now lives in migration
    0006_world, so this applies everything up to that migration.
    Safe to run repeatedly.
    """
    apply_migrations(_resolve_dsn(dsn), target="0006")


def _agent_row_to_model(row: dict) -> AgentGoalModel:
    return AgentGoalModel.from_dict(
        {
            "contact_id": row["contact_id"],
            "goals": row["goals"] or {},
            "observations": row["observations"],
            "replies": row["replies"],
            "reply_delays": row["reply_delays"] or [],
            "channels": row["channels"] or {},
            "last_day": row["last_day"],
            "predictions": row["predictions"] or [],
            "stats": row["stats"] or {"predictions": 0, "correct": 0},
        }
    )


def save_agent_models(user_id, agent_id, store, *, dsn=None) -> int:
    """Persist per-contact goal models (one row per contact).

    `store` is an `AgentGoalStore` (or a `WorldModel`, whose
    `_agent_models` are read). Models mutate in place (goal
    strengths, predictions, accuracy), so saving upserts by
    (user, agent, contact) (`ON CONFLICT ... DO UPDATE`):
    re-saving the same store writes the same rows again with
    their current values. Dropped goals are saved too — they
    are history, and the read path filters them. Returns the
    number of rows written.
    """
    if isinstance(store, WorldModel):
        models = list(store._agent_models.values())
    elif isinstance(store, AgentGoalStore):
        models = store.all_models()
    else:
        raise ValueError("store must be an AgentGoalStore or a WorldModel")
    if not models:
        return 0
    with psycopg.connect(_resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO agent_goal_models"
                " (user_id, agent_id, contact_id, goals, observations,"
                "  replies, reply_delays, channels, last_day, predictions,"
                "  stats, updated_at)"
                " VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s::jsonb,"
                "  %s::jsonb, %s, %s::jsonb, %s::jsonb, now())"
                " ON CONFLICT (user_id, agent_id, contact_id) DO UPDATE SET"
                "  goals = EXCLUDED.goals,"
                "  observations = EXCLUDED.observations,"
                "  replies = EXCLUDED.replies,"
                "  reply_delays = EXCLUDED.reply_delays,"
                "  channels = EXCLUDED.channels,"
                "  last_day = EXCLUDED.last_day,"
                "  predictions = EXCLUDED.predictions,"
                "  stats = EXCLUDED.stats,"
                "  updated_at = now()",
                [
                    (
                        str(user_id),
                        str(agent_id),
                        model.contact_id,
                        json.dumps(
                            {label: goal.to_dict() for label, goal in model.goals.items()},
                            ensure_ascii=False,
                        ),
                        model.observations,
                        model.replies,
                        json.dumps(model.reply_delays, ensure_ascii=False),
                        json.dumps(model.channels, ensure_ascii=False),
                        model.last_day,
                        json.dumps(
                            [prediction.to_dict() for prediction in model.predictions],
                            ensure_ascii=False,
                        ),
                        json.dumps(model.stats, ensure_ascii=False),
                    )
                    for model in models
                ],
            )
            written = cur.rowcount
        conn.commit()
    return written


def load_agent_models(user_id, agent_id, *, dsn=None) -> AgentGoalStore | None:
    """Load all per-contact goal models, or None when none were saved.

    Returns every model including dropped goals (history is
    preserved); the read path (`AgentGoalModel.active_goals`,
    `WorldModel.predict_agent`) filters to live ones.
    """
    with psycopg.connect(_resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT contact_id, goals, observations, replies, reply_delays,"
            " channels, last_day, predictions, stats FROM agent_goal_models"
            " WHERE user_id = %s AND agent_id = %s"
            " ORDER BY contact_id",
            (str(user_id), str(agent_id)),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    return AgentGoalStore.from_dict(
        {"models": [_agent_row_to_model(row).to_dict() for row in rows]}
    )


def delete_agent_models(user_id, agent_id, *, dsn=None) -> bool:
    """Delete all saved goal models for one (user, agent). Tests only."""
    with psycopg.connect(_resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_goal_models WHERE user_id = %s AND agent_id = %s",
                (str(user_id), str(agent_id)),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
