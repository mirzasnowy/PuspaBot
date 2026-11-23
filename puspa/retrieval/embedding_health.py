"""Visible embedding degradation (E-14, plan section 7.4).

Silent embedding degradation is the failure this module makes
visible. Three facts drive it:

- A vector is meaningless without the embedder that produced it.
  Every episodic row stores ``embedder_id`` + ``dimension``
  (migration 0022), the write path refuses an embedder that does
  not match the active one, and search only ever compares rows
  from the same embedder, so vectors from different embedders
  never mix.
- In production, running on the ``HashEmbedding`` stand-in is a
  degraded state, not a quiet default. Startup either refuses to
  start (strict policy) or comes up degraded, and that state is
  visible in three places: ``GET /health``, the ledger, and the
  operator banner.
- Two alarms fire in production: any hash fallback at all, and a
  recall drop of more than 10 percent (relative) on the frozen
  30-question canary set, measured against its stored baseline.
  A degraded measurement never silently becomes the new baseline.

Changing embedder is a tiered re-embed job, never a sudden column
change: recent rows first, older rows later, and the active
embedder only switches once no row still uses the old one.

No new ledger event type: degradation is recorded as a
``heartbeat`` event whose content carries
``kind == "embedding_degraded"`` (the closed set of 31 types stays
closed). The ledger read-back filters on that content kind.

Pure functions (startup evaluation, canary math, tier planning)
take plain values and need no database. The ``*_job`` / ``*_state``
helpers below are the thin database layer over migration 0022.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.db import errors as pg_errors
from puspa.memory.embeddings import (
    DEFAULT_FASTEMBED_MODEL,
    HashEmbedding,
)
from puspa.memory.episodic import search_episodes
from puspa.migrations import resolve_dsn

from .canary_questions import CANARY_QUESTIONS, canary_corpus

if TYPE_CHECKING:
    from puspa.ledger import Ledger

#: Health states for the embedding subsystem.
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"

#: Production policies for a hash-only start: "strict" refuses to
#: start, "degraded" starts in the degraded state (plan 7.4 allows
#: either; the default is visible degradation, not a refusal).
PRODUCTION_POLICIES = ("strict", "degraded")
DEFAULT_PRODUCTION_POLICY = "degraded"

#: Alarm: any hash fallback in production (count above zero).
#: Alarm: canary recall drops more than this fraction (relative)
#: below its stored baseline.
CANARY_ALARM_DROP = 0.10

#: The frozen canary set: exactly this many questions.
CANARY_QUESTION_COUNT = 30

#: How many top results count as a canary hit.
CANARY_TOP_K = 5

#: Content kind of the ledger heartbeat event that carries a
#: degraded embedding state (see module docstring).
LEDGER_DEGRADED_KIND = "embedding_degraded"

#: Ledger task id for embedding-health events (not a user task).
LEDGER_TASK_ID = "embedding-health"

#: Ledger agent id for embedding-health events.
LEDGER_AGENT_ID = "puspa"

#: Cache directory name fastembed uses for the default model.
_FASTEMBED_MODEL_CACHE_DIR = "models--Qdrant--bge-small-en-v1.5-onnx-Q"


class MixedEmbedderError(ValueError):
    """A vector row would mix embedders: refused."""


class EmbeddingStartupRefused(ValueError):
    """Production start refused: only the hash stand-in is available."""


def embedder_id_of(embedder: Any) -> str:
    """Stable identity of an embedder (its stored row label)."""
    return str(getattr(embedder, "embedder_id", None) or getattr(embedder, "name", "hash"))


def dimension_of(embedder: Any) -> int:
    """Vector dimension of an embedder."""
    return int(getattr(embedder, "dim", 0))


def detect_available_embedder_kind() -> str:
    """Cheap availability probe: "fastembed" or "hash", no model load.

    The real model counts as available only when fastembed imports
    AND its model cache is already on disk. A missing cache means
    the first real embed would have to download the model, so for
    a startup decision the honest answer is the hash stand-in.
    Never touches the network.
    """
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return "hash"
    cache = os.environ.get("FASTEMBED_CACHE_PATH")
    if cache:
        return "fastembed" if (Path(cache) / _FASTEMBED_MODEL_CACHE_DIR).exists() else "hash"
    model_dir = Path.home() / ".cache" / "fastembed" / _FASTEMBED_MODEL_CACHE_DIR
    return "fastembed" if model_dir.exists() else "hash"


@dataclass
class EmbeddingHealth:
    """One snapshot of embedding health, JSON-serializable."""

    embedder_id: str
    dimension: int
    status: str = STATUS_HEALTHY
    reason: str = ""
    fallback_count: int = 0
    environment: str = "development"
    alarm: bool = False
    alarm_reasons: list[str] = field(default_factory=list)
    canary: dict[str, Any] | None = None

    @property
    def is_degraded(self) -> bool:
        """True when the subsystem runs degraded."""
        return self.status == STATUS_DEGRADED

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for /health, the operator payload, and logs."""
        return {
            "embedder_id": self.embedder_id,
            "dimension": self.dimension,
            "status": self.status,
            "reason": self.reason,
            "fallback_count": self.fallback_count,
            "environment": self.environment,
            "alarm": self.alarm,
            "alarm_reasons": list(self.alarm_reasons),
            "canary": self.canary,
        }

    def to_banner(self) -> dict[str, str] | None:
        """Operator banner, or None when nothing is degraded."""
        if not self.is_degraded:
            return None
        return {
            "level": "warning",
            "title": "Embedding degraded",
            "detail": (
                f"Active embedder {self.embedder_id} ({self.dimension} dimensions) "
                f"is a fallback: {self.reason}. Vector search quality is reduced; "
                "check the embedding state before trusting recall."
            ),
        }


def evaluate_startup(
    embedder: Any,
    *,
    environment: str = "development",
    production_policy: str = DEFAULT_PRODUCTION_POLICY,
    fell_back: bool = False,
) -> EmbeddingHealth:
    """Decide the startup state for one embedder in one environment.

    Production with only the hash stand-in is degraded (or refused
    under the strict policy). Anywhere else the hash stand-in is a
    normal development default and stays healthy. ``fell_back``
    records that a real model was requested but unavailable.
    """
    environment = (environment or "development").strip().lower()
    if production_policy not in PRODUCTION_POLICIES:
        raise ValueError(
            f"unknown production policy {production_policy!r}; "
            f"expected one of {', '.join(PRODUCTION_POLICIES)}"
        )
    embedder_id = embedder_id_of(embedder)
    dimension = dimension_of(embedder)
    hash_only = isinstance(embedder, HashEmbedding)
    fallback_count = 1 if (fell_back or hash_only) else 0
    # E-18: every startup resolution feeds the embedding fallback ratio.
    with contextlib.suppress(Exception):
        from puspa.observability import metrics as observability_metrics

        observability_metrics.get_metrics().record_embedding_fallback(fallback_count > 0)
    if environment == "production" and hash_only:
        if production_policy == "strict":
            raise EmbeddingStartupRefused(
                "production start refused: only HashEmbedding is available; "
                "provide the real embedding model or set "
                "PUSPA_EMBEDDING_PRODUCTION_POLICY=degraded to start degraded"
            )
        return EmbeddingHealth(
            embedder_id=embedder_id,
            dimension=dimension,
            status=STATUS_DEGRADED,
            reason="hash_only_fallback",
            fallback_count=fallback_count,
            environment=environment,
            alarm=True,
            alarm_reasons=["hash_fallback_in_production"],
        )
    return EmbeddingHealth(
        embedder_id=embedder_id,
        dimension=dimension,
        status=STATUS_HEALTHY,
        fallback_count=fallback_count,
        environment=environment,
    )


def check_fallback_alarm(fallback_count: int, *, environment: str) -> bool:
    """Alarm when any hash fallback happened in production."""
    return (environment or "").strip().lower() == "production" and fallback_count > 0


def measure_canary_recall(
    search_fn: Any, questions: list[dict[str, str]], *, top_k: int = CANARY_TOP_K
) -> dict[str, Any]:
    """Recall of one search function on canary questions (pure).

    ``questions`` is a list of ``{"question", "episode_id"}``;
    ``search_fn(question)`` returns result rows (dicts with ``id``)
    or plain ids. A hit is the expected episode in the top ``top_k``.
    """
    if not questions:
        raise ValueError("canary questions must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    hits = 0
    misses: list[str] = []
    for item in questions:
        results = search_fn(item["question"])
        ids = [str(r["id"]) if isinstance(r, dict) else str(r) for r in results][:top_k]
        if str(item["episode_id"]) in ids:
            hits += 1
        else:
            misses.append(item["question"])
    total = len(questions)
    return {
        "recall": hits / total,
        "hits": hits,
        "total": total,
        "misses": misses,
        "top_k": top_k,
    }


def evaluate_canary_recall(measured_recall: float, baseline_recall: float) -> dict[str, Any]:
    """Alarm when measured recall fell more than 10% below baseline.

    The drop is relative: ``(baseline - measured) / baseline``.
    A zero baseline is degenerate (nothing to fall from) and never
    alarms. The result carries the numbers, not just the verdict,
    so an alarm is always explainable.
    """
    if not 0.0 <= measured_recall <= 1.0:
        raise ValueError(f"measured recall {measured_recall} is outside 0..1")
    if not 0.0 <= baseline_recall <= 1.0:
        raise ValueError(f"baseline recall {baseline_recall} is outside 0..1")
    if baseline_recall == 0.0:
        drop_relative = 0.0
    else:
        drop_relative = (baseline_recall - measured_recall) / baseline_recall
    return {
        "measured_recall": measured_recall,
        "baseline_recall": baseline_recall,
        "drop_relative": drop_relative,
        "threshold": CANARY_ALARM_DROP,
        "alarm": drop_relative > CANARY_ALARM_DROP,
    }


# --- tiered re-embed ------------------------------------------------------------
# Tier plan (newest first): tier 1 is the last 7 days, tier 2 the 90
# days before that, tier 3 everything older. A job walks the tiers
# in order; the active embedder only switches after the last tier,
# when no row still uses the old embedder.

REEMBED_TIERS: tuple[dict[str, Any], ...] = (
    {"tier": 1, "label": "recent_7d", "max_age_days": 7},
    {"tier": 2, "label": "recent_90d", "max_age_days": 90},
    {"tier": 3, "label": "older", "max_age_days": None},
)


def plan_reembed_tiers() -> list[dict[str, Any]]:
    """A fresh copy of the default tier plan (newest rows first)."""
    return [dict(tier) for tier in REEMBED_TIERS]


def _tier_window(
    tiers: list[dict[str, Any]], tier_number: int, now: datetime
) -> tuple[datetime | None, datetime | None]:
    """(newest, oldest) created_at bounds for one tier.

    ``newest`` is exclusive (rows newer than it belong to an
    earlier tier); ``oldest`` is inclusive. Either bound may be
    None (open-ended).
    """
    ordered = sorted(tiers, key=lambda t: t["tier"])
    current = next((t for t in ordered if t["tier"] == tier_number), None)
    if current is None:
        raise ValueError(f"unknown re-embed tier {tier_number}")
    max_age = current.get("max_age_days")
    oldest = now - timedelta(days=max_age) if max_age is not None else None
    previous = [t for t in ordered if t["tier"] < tier_number]
    newest = None
    if previous:
        prev_age = previous[-1].get("max_age_days")
        if prev_age is not None:
            newest = now - timedelta(days=prev_age)
    return newest, oldest


# --- database layer -------------------------------------------------------------


def _connect(dsn: str | None = None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


def _state_row(conn: Any) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM embedding_state WHERE id = 1")
        row = cur.fetchone()
    return dict(row) if row is not None else None


def get_embedding_state(dsn: str | None = None) -> dict[str, Any]:
    """Current embedding state; defaults describe a fresh database.

    A database that predates migration 0022 has no state row, and
    its rows are all hash vectors, so the defaults say exactly
    that (active ``hash`` / 384, healthy, no fallbacks).
    """
    try:
        with _connect(dsn) as conn:
            row = _state_row(conn)
    except pg_errors.UndefinedTable:
        return _default_state()
    if row is None:
        return _default_state()
    return _public_state(row)


def _default_state() -> dict[str, Any]:
    return {
        "active_embedder_id": "hash",
        "active_dimension": 384,
        "status": STATUS_HEALTHY,
        "fallback_count": 0,
        "reason": "",
    }


def _public_state(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "active_embedder_id": row["active_embedder_id"],
        "active_dimension": int(row["active_dimension"]),
        "status": row["status"],
        "fallback_count": int(row["fallback_count"]),
        "reason": row["reason"],
    }


def set_active_embedder(
    dsn: str | None, embedder_id: str, dimension: int, *, allow_mixed: bool = False
) -> dict[str, Any]:
    """Switch the active embedder (the sanctioned switch point).

    Refuses while rows still use the previous active embedder,
    unless ``allow_mixed`` is set (operator override for tests and
    recovery; the re-embed job never needs it, because it switches
    only after the last tier). Switching to the same embedder is a
    no-op that still writes the row.
    """
    if not embedder_id:
        raise ValueError("embedder_id must not be empty")
    if dimension < 1:
        raise ValueError("dimension must be at least 1")
    with _connect(dsn) as conn:
        row = _state_row(conn)
        previous = _public_state(row) if row is not None else _default_state()
        if (previous["active_embedder_id"], previous["active_dimension"]) != (
            embedder_id,
            dimension,
        ) and not allow_mixed:
            remaining = _count_with_embedder(
                conn, previous["active_embedder_id"], previous["active_dimension"]
            )
            if remaining > 0:
                raise MixedEmbedderError(
                    f"cannot switch active embedder to {embedder_id} "
                    f"(dim {dimension}): {remaining} rows still use "
                    f"{previous['active_embedder_id']} "
                    f"(dim {previous['active_dimension']}); "
                    "finish a re-embed job first"
                )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO embedding_state "
                "(id, active_embedder_id, active_dimension, status, fallback_count, reason) "
                "VALUES (1, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET "
                "active_embedder_id = EXCLUDED.active_embedder_id, "
                "active_dimension = EXCLUDED.active_dimension, "
                "updated_at = now()",
                (
                    embedder_id,
                    dimension,
                    previous["status"],
                    previous["fallback_count"],
                    previous["reason"],
                ),
            )
        conn.commit()
        return _public_state(_state_row(conn) or {})


def set_embedding_status(
    dsn: str | None,
    *,
    status: str,
    reason: str = "",
    fallback_count: int | None = None,
) -> dict[str, Any]:
    """Record the current health status (and optional fallback count)."""
    if status not in (STATUS_HEALTHY, STATUS_DEGRADED):
        raise ValueError(f"unknown embedding status {status!r}")
    if fallback_count is not None and fallback_count < 0:
        raise ValueError("fallback_count must not be negative")
    with _connect(dsn) as conn:
        row = _state_row(conn)
        previous = _public_state(row) if row is not None else _default_state()
        count = previous["fallback_count"] if fallback_count is None else fallback_count
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO embedding_state "
                "(id, active_embedder_id, active_dimension, status, fallback_count, reason) "
                "VALUES (1, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET "
                "status = EXCLUDED.status, "
                "fallback_count = EXCLUDED.fallback_count, "
                "reason = EXCLUDED.reason, updated_at = now()",
                (
                    previous["active_embedder_id"],
                    previous["active_dimension"],
                    status,
                    count,
                    reason,
                ),
            )
        conn.commit()
        return _public_state(_state_row(conn) or {})


def _count_with_embedder(conn: Any, embedder_id: str, dimension: int) -> int:
    with conn.cursor() as cur:
        row = cur.execute(
            "SELECT count(*) FROM episodic WHERE embedder_id = %s AND dimension = %s",
            (embedder_id, dimension),
        ).fetchone()
    return int(row["count"])


def count_mixed_rows(dsn: str | None, *, embedder_id: str, dimension: int) -> int:
    """Rows whose embedder differs from the given one (0 = clean)."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        row = cur.execute(
            "SELECT count(*) FROM episodic WHERE NOT (embedder_id = %s AND dimension = %s)",
            (embedder_id, dimension),
        ).fetchone()
    return int(row["count"])


def validate_rows_match_active(dsn: str | None = None) -> int:
    """Raise when any row disagrees with the active embedder.

    Returns 0 when the store is clean. This is the maintenance
    check for rows written behind the write path's back (direct
    SQL, a pre-0022 writer): the write path itself refuses them
    up front, this catches the ones that slipped through.
    """
    state = get_embedding_state(dsn)
    mixed = count_mixed_rows(
        dsn,
        embedder_id=state["active_embedder_id"],
        dimension=state["active_dimension"],
    )
    if mixed > 0:
        raise MixedEmbedderError(
            f"{mixed} episodic rows use an embedder other than the active "
            f"{state['active_embedder_id']} (dim {state['active_dimension']}); "
            "re-embed them before trusting search"
        )
    return 0


def record_canary_baseline(
    dsn: str | None,
    *,
    embedder_id: str,
    dimension: int,
    recall: float,
    question_count: int = CANARY_QUESTION_COUNT,
    force: bool = False,
) -> dict[str, Any]:
    """Store a canary baseline, with a ratchet against silent decay.

    A measurement below the stored baseline never replaces it
    (``updated`` is False and the old baseline stands), so a
    degraded embedder cannot normalize itself by re-measuring.
    Pass ``force=True`` only after a sanctioned embedder change,
    when the new embedder earns a new reference number.
    """
    if not 0.0 <= recall <= 1.0:
        raise ValueError(f"recall {recall} is outside 0..1")
    if question_count != CANARY_QUESTION_COUNT:
        raise ValueError(
            f"canary baseline needs exactly {CANARY_QUESTION_COUNT} questions, got {question_count}"
        )
    existing = load_canary_baseline(dsn, embedder_id=embedder_id, dimension=dimension)
    if existing is not None and not force and recall < existing["recall"]:
        return {
            "updated": False,
            "embedder_id": embedder_id,
            "dimension": dimension,
            "stored_recall": existing["recall"],
            "measured_recall": recall,
        }
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO embedding_canary_baselines "
                "(embedder_id, dimension, recall, question_count) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (embedder_id, dimension) DO UPDATE SET "
                "recall = EXCLUDED.recall, question_count = EXCLUDED.question_count, "
                "measured_at = now()",
                (embedder_id, dimension, recall, question_count),
            )
        conn.commit()
    return {
        "updated": True,
        "embedder_id": embedder_id,
        "dimension": dimension,
        "stored_recall": recall,
        "measured_recall": recall,
    }


def load_canary_baseline(
    dsn: str | None, *, embedder_id: str, dimension: int
) -> dict[str, Any] | None:
    """Stored canary baseline for one embedder, or None."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT embedder_id, dimension, recall, question_count, measured_at "
            "FROM embedding_canary_baselines "
            "WHERE embedder_id = %s AND dimension = %s",
            (embedder_id, dimension),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "embedder_id": row["embedder_id"],
        "dimension": int(row["dimension"]),
        "recall": float(row["recall"]),
        "question_count": int(row["question_count"]),
        "measured_at": row["measured_at"],
    }


def load_reference_baseline(dsn: str | None) -> dict[str, Any] | None:
    """The reference baseline: the best stored recall across embedders.

    The alarm always compares against the best known-good number,
    not against the current embedder's own baseline — otherwise a
    bad embedder could earn a low baseline and never alarm again.
    """
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT embedder_id, dimension, recall, question_count, measured_at "
            "FROM embedding_canary_baselines "
            "ORDER BY recall DESC, measured_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "embedder_id": row["embedder_id"],
        "dimension": int(row["dimension"]),
        "recall": float(row["recall"]),
        "question_count": int(row["question_count"]),
        "measured_at": row["measured_at"],
    }


def run_canary_check(
    dsn: str | None,
    *,
    user_id: str,
    embedder: Any,
    questions: list[dict[str, str]],
    top_k: int = CANARY_TOP_K,
) -> dict[str, Any]:
    """Measure canary recall on live episodic rows (DB-backed).

    ``questions`` is a list of ``{"question", "episode_id"}``.
    Returns the measurement plus the evaluation against the
    reference baseline (the best stored recall); ``alarm`` is None
    when no baseline exists yet (first run — record one).
    """
    measured = measure_canary_recall(
        lambda question: search_episodes(
            user_id, question, limit=top_k, embedder=embedder, dsn=dsn
        ),
        questions,
        top_k=top_k,
    )
    baseline = load_reference_baseline(dsn)
    evaluation = None
    if baseline is not None:
        evaluation = evaluate_canary_recall(measured["recall"], baseline["recall"])
    return {
        "embedder_id": embedder_id_of(embedder),
        "dimension": dimension_of(embedder),
        "measured": measured,
        "baseline": baseline,
        "evaluation": evaluation,
        "alarm": evaluation["alarm"] if evaluation is not None else None,
    }


# --- re-embed jobs --------------------------------------------------------------


def _public_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "from_embedder_id": row["from_embedder_id"],
        "to_embedder_id": row["to_embedder_id"],
        "from_dimension": int(row["from_dimension"]),
        "to_dimension": int(row["to_dimension"]),
        "status": row["status"],
        "total_rows": int(row["total_rows"]),
        "done_rows": int(row["done_rows"]),
        "current_tier": int(row["current_tier"]),
        "tiers": list(row["tiers"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_reembed_job(
    dsn: str | None,
    *,
    from_embedder_id: str,
    to_embedder_id: str,
    from_dimension: int,
    to_dimension: int,
    tiers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a tiered re-embed job (queued; nothing runs yet)."""
    if (from_embedder_id, from_dimension) == (to_embedder_id, to_dimension):
        raise ValueError("re-embed job needs a different target embedder")
    if from_dimension < 1 or to_dimension < 1:
        raise ValueError("dimensions must be at least 1")
    plan = tiers if tiers is not None else plan_reembed_tiers()
    if not plan:
        raise ValueError("re-embed job needs at least one tier")
    if [t["tier"] for t in sorted(plan, key=lambda t: t["tier"])] != list(range(1, len(plan) + 1)):
        raise ValueError("re-embed tiers must be numbered 1..N in order")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            total_row = cur.execute(
                "SELECT count(*) FROM episodic WHERE embedder_id = %s AND dimension = %s",
                (from_embedder_id, from_dimension),
            ).fetchone()
            total = int(total_row["count"])
            import json

            job_row = cur.execute(
                "INSERT INTO embedding_reembed_jobs "
                "(from_embedder_id, to_embedder_id, from_dimension, to_dimension, "
                " total_rows, tiers) VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
                "RETURNING id",
                (
                    from_embedder_id,
                    to_embedder_id,
                    from_dimension,
                    to_dimension,
                    total,
                    json.dumps(plan),
                ),
            ).fetchone()
            job_id = str(job_row["id"])
        conn.commit()
        return get_reembed_job(dsn, job_id)


def get_reembed_job(dsn: str | None, job_id: str) -> dict[str, Any]:
    """One re-embed job row, or ValueError when it does not exist."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM embedding_reembed_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown re-embed job {job_id}")
    return _public_job(dict(row))


def _update_job(conn: Any, job_id: str, **fields: Any) -> None:
    assignments = ", ".join(f"{key} = %s" for key in fields)
    values = list(fields.values())
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE embedding_reembed_jobs SET {assignments}, updated_at = now() WHERE id = %s",
            (*values, job_id),
        )


def run_reembed_tier(
    dsn: str | None, job_id: str, tier: int, embedder: Any, *, batch_size: int = 100
) -> dict[str, Any]:
    """Re-embed one tier of a job with the target embedder.

    Rows move tier by tier (newest first); a tier is re-run safe
    (finished rows no longer match the source embedder). When the
    last tier finishes and no row still uses the source embedder,
    the job is done and the active embedder switches; when rows
    remain (written mid-job with the old embedder), the job stays
    running and reports them instead of switching under live data.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if embedder_id_of(embedder) == "" or dimension_of(embedder) < 1:
        raise ValueError("embedder needs an id and a dimension")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM embedding_reembed_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown re-embed job {job_id}")
        job = _public_job(dict(row))
        if job["status"] == "done":
            return job
        if job["status"] == "failed":
            raise ValueError(f"re-embed job {job_id} failed: {job['error']}")
        if (
            embedder_id_of(embedder) != job["to_embedder_id"]
            or dimension_of(embedder) != job["to_dimension"]
        ):
            raise MixedEmbedderError(
                f"tier {tier} needs embedder {job['to_embedder_id']} "
                f"(dim {job['to_dimension']}), got {embedder_id_of(embedder)} "
                f"(dim {dimension_of(embedder)})"
            )
        tiers = job["tiers"]
        newest, oldest = _tier_window(tiers, tier, datetime.now(UTC))
        _update_job(conn, job_id, status="running", current_tier=tier)
        conn.commit()
        done_this_tier = 0
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, content FROM episodic "
                    "WHERE embedder_id = %s AND dimension = %s "
                    "AND (%s::timestamptz IS NULL OR created_at < %s::timestamptz) "
                    "AND (%s::timestamptz IS NULL OR created_at >= %s::timestamptz) "
                    "ORDER BY created_at DESC LIMIT %s",
                    (
                        job["from_embedder_id"],
                        job["from_dimension"],
                        newest,
                        newest,
                        oldest,
                        oldest,
                        batch_size,
                    ),
                )
                batch = cur.fetchall()
            if not batch:
                break
            for batch_row in batch:
                vector = embedder.embed(batch_row["content"])
                if len(vector) != job["to_dimension"]:
                    raise MixedEmbedderError(
                        f"embedder {job['to_embedder_id']} returned dim "
                        f"{len(vector)}, expected {job['to_dimension']}"
                    )
                literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE episodic SET embedding = %s::vector, "
                        "embedder_id = %s, dimension = %s WHERE id = %s",
                        (literal, job["to_embedder_id"], job["to_dimension"], batch_row["id"]),
                    )
            conn.commit()
            done_this_tier += len(batch)
        import json

        updated_tiers = []
        for entry in tiers:
            if entry["tier"] == tier:
                updated_tiers.append(
                    {
                        **entry,
                        "done": True,
                        # Accumulate: re-running a finished tier finds
                        # no rows, and must not erase its earlier count.
                        "done_rows": int(entry.get("done_rows") or 0) + done_this_tier,
                    }
                )
            else:
                updated_tiers.append(entry)
        total_done = job["done_rows"] + done_this_tier
        remaining = _count_with_embedder(conn, job["from_embedder_id"], job["from_dimension"])
        all_tiers_done = all(entry.get("done") for entry in updated_tiers)
        if all_tiers_done and remaining == 0:
            _update_job(
                conn,
                job_id,
                status="done",
                done_rows=total_done,
                tiers=json.dumps(updated_tiers),
                current_tier=tier,
            )
            conn.commit()
            set_active_embedder(dsn, job["to_embedder_id"], job["to_dimension"], allow_mixed=True)
            set_embedding_status(dsn, status=STATUS_HEALTHY, reason="", fallback_count=0)
        else:
            _update_job(
                conn,
                job_id,
                status="running",
                done_rows=total_done,
                tiers=json.dumps(updated_tiers),
                current_tier=tier,
            )
            conn.commit()
        finished = get_reembed_job(dsn, job_id)
        finished["tier_done_rows"] = done_this_tier
        finished["remaining_rows"] = remaining
        return finished


# --- ledger ---------------------------------------------------------------------


def record_degraded_to_ledger(
    ledger: Ledger,
    health: EmbeddingHealth,
    *,
    task_id: str = LEDGER_TASK_ID,
    agent_id: str = LEDGER_AGENT_ID,
) -> dict[str, Any]:
    """Append the degraded state to the ledger (heartbeat event).

    The closed set of event types stays closed: the event type is
    ``heartbeat`` and the content carries
    ``kind == "embedding_degraded"`` plus the full health snapshot,
    so the degraded state is queryable from the ledger without a
    32nd type. Returns the appended event.
    """
    return ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="heartbeat",
        content={
            "kind": LEDGER_DEGRADED_KIND,
            "embedder_id": health.embedder_id,
            "dimension": health.dimension,
            "status": health.status,
            "reason": health.reason,
            "fallback_count": health.fallback_count,
            "environment": health.environment,
            "alarm": health.alarm,
            "alarm_reasons": list(health.alarm_reasons),
            "canary": health.canary,
        },
    )


def read_degraded_from_ledger(ledger: Ledger, task_id: str = LEDGER_TASK_ID) -> list[dict]:
    """Degraded-state events recorded for one ledger task."""
    return [
        event
        for event in ledger.events(task_id)
        if (event.get("content") or {}).get("kind") == LEDGER_DEGRADED_KIND
    ]


__all__ = [
    "CANARY_ALARM_DROP",
    "CANARY_QUESTIONS",
    "CANARY_QUESTION_COUNT",
    "CANARY_TOP_K",
    "DEFAULT_FASTEMBED_MODEL",
    "DEFAULT_PRODUCTION_POLICY",
    "LEDGER_DEGRADED_KIND",
    "PRODUCTION_POLICIES",
    "REEMBED_TIERS",
    "STATUS_DEGRADED",
    "STATUS_HEALTHY",
    "EmbeddingHealth",
    "EmbeddingStartupRefused",
    "MixedEmbedderError",
    "canary_corpus",
    "check_fallback_alarm",
    "count_mixed_rows",
    "create_reembed_job",
    "detect_available_embedder_kind",
    "dimension_of",
    "embedder_id_of",
    "evaluate_canary_recall",
    "evaluate_startup",
    "get_embedding_state",
    "get_reembed_job",
    "load_canary_baseline",
    "load_reference_baseline",
    "measure_canary_recall",
    "plan_reembed_tiers",
    "read_degraded_from_ledger",
    "record_canary_baseline",
    "record_degraded_to_ledger",
    "run_canary_check",
    "run_reembed_tier",
    "set_active_embedder",
    "set_embedding_status",
    "validate_rows_match_active",
]
