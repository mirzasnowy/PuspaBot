"""Shared anti-fraud and price graphs, per-user privacy (ticket B3).

Some graphs get stronger with more users: a bad seller flagged
once protects everyone, and more price checks make fair-price
estimates more accurate. Each user's own data stays theirs.

The boundary is structural, not a promise in a comment:

- **Shared tables carry no user id at all.** A contribution is
  stored with a per-(user, subject) pseudonym,
  ``sha256(user_id + ":" + subject_key)``, used only to count
  distinct contributors and to stop one user flagging the same
  seller (or contributing the same price) twice. The hash is
  never returned by any read function, and because the subject
  key is part of the hash, flags for different sellers cannot
  be linked to the same user. The residual limit is honest:
  anyone who already knows a user's UUID could test whether
  that user flagged one specific seller by recomputing the
  hash; they cannot enumerate flaggers, read private rows, or
  link contributions across subjects.
- **Shared reads return aggregates only**: a seller reputation
  (flag count, first flag, latest flag) and a fair-price
  estimate (median, mean, standard error, observation count,
  sources). No individual private row is ever exposed.
- **Private records live in their own table, namespaced by
  ``user_id``.** Every private read filters on the requesting
  user; a record fetched with the wrong user id returns
  ``None``. The table also has a row-level-security policy
  (see migration 0004_graphs) for connections as the
  ``puspa_app`` role; the application-level filter is the
  enforcement tests rely on, because test connections run as
  a superuser that bypasses row-level security.

This module reuses the existing graph storage (Postgres tables
in migration 0004_graphs, created by
``puspa.graphs.create_schema``) and the existing B1 record
types for its inputs; it does not build a second graph store.
The fair-price math is pure (no database) so the improvement
with more observations can be tested without any I/O.
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.db import Jsonb
from puspa.db import parse_timestamp as _db_parse_timestamp

from puspa.accounts.ports import resolve_dsn
from puspa.migrations import apply_migrations

#: Shared tables this module owns. The canonical DDL lives in
#: migration 0004_graphs (``puspa/migrations/versions/``).
SHARED_TABLES = (
    "shared_seller_flags",
    "shared_price_observations",
    "shared_graph_private_records",
)


class SharedGraphError(ValueError):
    """A shared-graph write was rejected (bad input, nothing stored)."""


def _connect(dsn=None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


def create_shared_schema(dsn: str | None = None) -> None:
    """Apply the three shared-graph tables (migration 0004_graphs)."""
    apply_migrations(resolve_dsn(dsn), target="0004")


def normalize_seller_key(name: str) -> str:
    """Shared seller key: lowercased, collapsed whitespace."""
    return " ".join((name or "").lower().split())


def normalize_product_key(raw: str) -> str:
    """Shared product key: lowercased, no trailing slash, no query."""
    key = (raw or "").strip().lower()
    return re.split(r"[?#]", key, maxsplit=1)[0].rstrip("/")


def _subject_hash(user_id: str, subject_key: str) -> str:
    """Per-(user, subject) pseudonym. Never returned to callers."""
    return hashlib.sha256(f"{user_id}:{subject_key}".encode()).hexdigest()


def _require_user_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not user_id.strip():
        raise SharedGraphError("user_id must be a non-empty string")
    return user_id.strip()


# ---------------------------------------------------------------------------
# Fair-price estimate (pure: no database, no I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FairPriceEstimate:
    """A fair-price estimate from a set of price observations.

    ``value`` is the median (robust to one outlier listing).
    ``standard_error`` is the sample standard deviation divided
    by the square root of the observation count: it shrinks as
    ``observations`` grows, which is the measurable form of
    "more checks make the estimate more accurate". With a single
    observation there is no spread to measure, so the standard
    error and the confidence interval are ``None``.
    """

    product_key: str
    value: float
    mean: float
    observations: int
    sources: list[str] = field(default_factory=list)
    standard_deviation: float | None = None
    standard_error: float | None = None
    low: float | None = None
    high: float | None = None
    currency: str = "IDR"

    def to_dict(self) -> dict:
        return {
            "product_key": self.product_key,
            "value": self.value,
            "mean": self.mean,
            "observations": self.observations,
            "sources": list(self.sources),
            "standard_deviation": self.standard_deviation,
            "standard_error": self.standard_error,
            "low": self.low,
            "high": self.high,
            "currency": self.currency,
        }


def estimate_fair_price(
    product_key: str,
    prices: list[float],
    sources: list[str] | None = None,
) -> FairPriceEstimate | None:
    """Estimate a fair price from observed prices.

    Returns ``None`` when there are no observations. Prices must
    be finite and positive; anything else is rejected rather
    than silently dropped, so a bad contribution cannot skew
    the shared estimate.
    """
    key = normalize_product_key(product_key)
    if not key:
        raise SharedGraphError("product_key must be a non-empty string")
    if not prices:
        return None
    values = []
    for price in prices:
        try:
            number = float(price)
        except (TypeError, ValueError) as exc:
            raise SharedGraphError(f"price is not a number: {price!r}") from exc
        if not math.isfinite(number) or number <= 0:
            raise SharedGraphError(f"price must be a finite positive number: {price!r}")
        values.append(number)
    median = float(statistics.median(values))
    mean = float(statistics.mean(values))
    if len(values) >= 2:
        deviation = float(statistics.stdev(values))
        error = deviation / math.sqrt(len(values))
        half_width = 1.96 * error
        return FairPriceEstimate(
            product_key=key,
            value=median,
            mean=mean,
            observations=len(values),
            sources=sorted(set(sources or [])),
            standard_deviation=deviation,
            standard_error=error,
            low=median - half_width,
            high=median + half_width,
        )
    return FairPriceEstimate(
        product_key=key,
        value=median,
        mean=mean,
        observations=1,
        sources=sorted(set(sources or [])),
    )


# ---------------------------------------------------------------------------
# Shared seller reputation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SellerReputation:
    """The shared signal for one seller: aggregates only.

    ``flag_count`` counts distinct users who flagged the seller
    (one flag per user: repeat flags do not inflate it).
    ``flagged`` is true from the first flag: one flag protects
    every other user. No reporter identity is stored in this
    object, and none is returned by any read.
    """

    seller_key: str
    flag_count: int
    first_flagged_at: datetime | None = None
    latest_flagged_at: datetime | None = None
    flag_reason: str = ""

    @property
    def flagged(self) -> bool:
        return self.flag_count >= 1

    def to_dict(self) -> dict:
        return {
            "seller_key": self.seller_key,
            "flag_count": self.flag_count,
            "flagged": self.flagged,
            "first_flagged_at": (
                self.first_flagged_at.isoformat() if self.first_flagged_at else None
            ),
            "latest_flagged_at": (
                self.latest_flagged_at.isoformat() if self.latest_flagged_at else None
            ),
            "flag_reason": self.flag_reason,
        }


def _reputation_from_row(seller_key: str, row: dict | None) -> SellerReputation | None:
    if row is None or not row["flag_count"]:
        return None
    return SellerReputation(
        seller_key=seller_key,
        flag_count=int(row["flag_count"]),
        # MIN()/MAX() are expressions, so SQLite returns their values
        # as raw text (no column type to convert from); normalise
        # them back to datetimes, exactly as Postgres would return.
        first_flagged_at=_db_parse_timestamp(row["first_flagged_at"]),
        latest_flagged_at=_db_parse_timestamp(row["latest_flagged_at"]),
        flag_reason=row["flag_reason"] or "",
    )


def flag_seller(
    dsn,
    *,
    user_id: str,
    seller_name: str,
    flag_reason: str = "",
) -> SellerReputation:
    """Flag a seller on behalf of one user.

    The flag lands in the shared table under the reporter's
    per-seller pseudonym, so the next user who checks this
    seller sees it. Flagging twice changes nothing: the second
    flag is a no-op and the count stays at one per user.
    Returns the updated shared reputation.
    """
    user_id = _require_user_id(user_id)
    seller_key = normalize_seller_key(seller_name)
    if not seller_key:
        raise SharedGraphError("seller_name must be a non-empty string")
    reason = (flag_reason or "").strip()
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO shared_seller_flags (seller_key, reporter_hash, flag_reason)"
            " VALUES (%s, %s, %s) ON CONFLICT (seller_key, reporter_hash) DO NOTHING",
            (seller_key, _subject_hash(user_id, seller_key), reason),
        )
        cur.execute(
            "SELECT COUNT(*) AS flag_count, MIN(created_at) AS first_flagged_at,"
            " MAX(created_at) AS latest_flagged_at,"
            " (SELECT flag_reason FROM shared_seller_flags WHERE seller_key = %s"
            "  ORDER BY created_at ASC LIMIT 1) AS flag_reason"
            " FROM shared_seller_flags WHERE seller_key = %s",
            (seller_key, seller_key),
        )
        row = cur.fetchone()
        conn.commit()
    reputation = _reputation_from_row(seller_key, row)
    assert reputation is not None  # the insert above just wrote a row
    return reputation


def seller_reputation(dsn, seller_name: str) -> SellerReputation | None:
    """The shared reputation for a seller, or None if unflagged.

    Aggregates only: flag count plus first/latest flag times.
    No reporter identity is returned.
    """
    seller_key = normalize_seller_key(seller_name)
    if not seller_key:
        return None
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS flag_count, MIN(created_at) AS first_flagged_at,"
            " MAX(created_at) AS latest_flagged_at,"
            " (SELECT flag_reason FROM shared_seller_flags WHERE seller_key = %s"
            "  ORDER BY created_at ASC LIMIT 1) AS flag_reason"
            " FROM shared_seller_flags WHERE seller_key = %s",
            (seller_key, seller_key),
        )
        row = cur.fetchone()
    return _reputation_from_row(seller_key, row)


# ---------------------------------------------------------------------------
# Shared price observations
# ---------------------------------------------------------------------------


def contribute_price(
    dsn,
    *,
    user_id: str,
    product_key: str,
    source: str,
    price: float,
) -> FairPriceEstimate | None:
    """Contribute one anonymised price observation to the shared graph.

    Only the product key, the source, and the price are stored;
    the contributor is recorded as a per-(user, product)
    pseudonym used for de-duplication only. Contributing the
    same product from the same source twice keeps one row.
    Returns the updated shared estimate for the product.
    """
    user_id = _require_user_id(user_id)
    key = normalize_product_key(product_key)
    if not key:
        raise SharedGraphError("product_key must be a non-empty string")
    source = (source or "").strip()
    if not source:
        raise SharedGraphError("source must be a non-empty string")
    try:
        amount = float(price)
    except (TypeError, ValueError) as exc:
        raise SharedGraphError(f"price is not a number: {price!r}") from exc
    if not math.isfinite(amount) or amount <= 0:
        raise SharedGraphError(f"price must be a finite positive number: {price!r}")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO shared_price_observations"
            " (product_key, source, price, observer_hash) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (product_key, source, observer_hash) DO NOTHING",
            (key, source, amount, _subject_hash(user_id, key)),
        )
        conn.commit()
    return shared_fair_price(dsn, key)


def shared_fair_price(dsn, product_key: str) -> FairPriceEstimate | None:
    """The shared fair-price estimate for a product, or None.

    Built from every anonymised observation in the shared
    table. The estimate object carries counts and statistics
    only — no contributor identity.
    """
    key = normalize_product_key(product_key)
    if not key:
        return None
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT price, source FROM shared_price_observations"
            " WHERE product_key = %s ORDER BY observed_at ASC, id ASC",
            (key,),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    return estimate_fair_price(
        key,
        [float(row["price"]) for row in rows],
        sources=[row["source"] for row in rows],
    )


def list_shared_price_points(dsn, product_key: str) -> list[dict]:
    """Shared price points for a product: price + source only.

    This is the only per-observation shared read, and it
    deliberately exposes no observer hash, no row id, and no
    user id — the returned dicts carry exactly
    ``product_key`` / ``source`` / ``price``.
    """
    key = normalize_product_key(product_key)
    if not key:
        return []
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT product_key, source, price FROM shared_price_observations"
            " WHERE product_key = %s ORDER BY observed_at ASC, id ASC",
            (key,),
        )
        rows = cur.fetchall()
    return [
        {"product_key": row["product_key"], "source": row["source"], "price": float(row["price"])}
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Private records: per-user, never pooled
# ---------------------------------------------------------------------------


def record_private(
    dsn,
    *,
    user_id: str,
    kind: str,
    subject_key: str,
    payload: dict | None = None,
) -> dict:
    """Store one private record for one user.

    ``kind`` names what the record is (for example
    ``"price_observation"`` or ``"seller_note"``); the payload
    is the user's own data and is only ever returned to that
    user. Shared aggregates never read this table.
    """
    user_id = _require_user_id(user_id)
    kind = (kind or "").strip()
    if not kind:
        raise SharedGraphError("kind must be a non-empty string")
    subject_key = (subject_key or "").strip()
    if not subject_key:
        raise SharedGraphError("subject_key must be a non-empty string")
    if payload is not None and not isinstance(payload, dict):
        raise SharedGraphError("payload must be a dict")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO shared_graph_private_records (user_id, kind, subject_key, payload)"
            " VALUES (%s, %s, %s, %s) RETURNING id, created_at",
            (user_id, kind, subject_key, Jsonb(payload or {})),
        )
        row = cur.fetchone()
        conn.commit()
    return {
        "id": str(row["id"]),
        "user_id": user_id,
        "kind": kind,
        "subject_key": subject_key,
        "payload": dict(payload or {}),
        "created_at": row["created_at"],
    }


def list_private_records(
    dsn,
    user_id: str,
    *,
    kind: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Private records of one user only, newest first.

    The ``user_id`` filter is the privacy boundary: records
    belonging to any other user can never appear here, whatever
    ``kind`` is asked for.
    """
    user_id = _require_user_id(user_id)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise SharedGraphError("limit must be a positive integer")
    query = (
        "SELECT id, user_id, kind, subject_key, payload, created_at"
        " FROM shared_graph_private_records WHERE user_id = %s"
    )
    params: list = [user_id]
    if kind is not None:
        query += " AND kind = %s"
        params.append(kind)
    query += " ORDER BY created_at DESC, id DESC LIMIT %s"
    params.append(limit)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [
        {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "kind": row["kind"],
            "subject_key": row["subject_key"],
            "payload": dict(row["payload"] or {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_private_record(dsn, record_id: str, user_id: str) -> dict | None:
    """One private record, only when it belongs to this user.

    Returns ``None`` when the id does not exist, is not a
    UUID, or belongs to another user — the caller cannot tell
    which of the three it was.
    """
    user_id = _require_user_id(user_id)
    try:
        UUID(str(record_id))
    except (ValueError, TypeError, AttributeError):
        return None
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, kind, subject_key, payload, created_at"
            " FROM shared_graph_private_records WHERE id = %s AND user_id = %s",
            (record_id, user_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "kind": row["kind"],
        "subject_key": row["subject_key"],
        "payload": dict(row["payload"] or {}),
        "created_at": row["created_at"],
    }
