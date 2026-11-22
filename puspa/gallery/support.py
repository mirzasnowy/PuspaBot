"""Creator support purchases (B17: credit-only revenue share, no cash-out).

A user pays to support a published B16 gallery work. The platform
keeps a commission and the creator gets the rest in Puspa credits
ONLY: credits can never be converted back to money, and there is no
cash-out, withdrawal, or payout path anywhere in this module — the
store only INSERTs and SELECTs purchase rows, never UPDATEs or
DELETEs one, and it exposes no function that turns credits into
money.

The split, stated plainly so the anti-farming rule is checkable:

- The supporter pays ``amount_idr`` (Rupiah, at least
  ``MIN_SUPPORT_IDR``).
- The platform keeps at least 20% (``PLATFORM_COMMISSION_RATE``).
  The commission is ``amount_idr`` minus the value of the credits
  granted, so the split always sums exactly: every Rupiah is either
  commission or creator credit value, with rounding dust staying
  with the platform.
- One credit is valued at ``CREDIT_VALUE_IDR`` Rupiah. That is the
  highest per-credit price in the public credit packs
  (``pack-20``: 20 credits for Rp9,000, see
  ``puspa/billing/config.py``), so the "credit value below price
  paid" proof is conservative: it holds at every pack's rate.
- Credits granted are ``floor(amount * 80% / 450)``, at least 1.
  Because the creator's share is at most 80% of what was paid,
  buying your own work to farm credits always loses money: the
  credits you get back are worth strictly less than the price you
  paid. That is the whole point, and the tests prove it at every
  amount from the minimum to the maximum.

Commission applies ONLY to digital gallery works. The split
function takes a ``subject_kind`` and refuses anything that is not
a gallery work — a price check or an authenticity check of a
physical item can never carry a commission here.

Beta follows the B8 prepare-only rule: a support is recorded with
status ``'prepared'`` and no real money moves. Charging the
supporter and minting the credits for real is hosted-service work
that comes after B8 leaves prepare-only. The purchase is recorded
twice: once in the append-only ``creator_support_purchases``
table (the transaction record for this feature), and once as an
``evidence_recorded`` event in the hash-chained ledger, using the
closed event set (no new event type: the ledger's 33 types stay
closed, and the support rides on the existing ``evidence_recorded``
type with ``kind="creator_support"``, the same pattern the
outcomes module uses). The ledger write needs the supporter's
agent id; when the caller does not pass one, the store resolves
it from the agent registry and refuses the support when the
supporter has no registered agent, instead of recording a
purchase the ledger cannot account for.

The schema lives in migration 0043_creator_support (next to the
other migrations) and is applied the same way.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.ports import resolve_dsn
from puspa.migrations import apply_migrations

if TYPE_CHECKING:
    from puspa.ledger import Ledger

__all__ = [
    "CREDIT_VALUE_IDR",
    "MAX_SUPPORT_IDR",
    "MIN_SUPPORT_IDR",
    "PLATFORM_COMMISSION_RATE",
    "SUPPORT_TABLES",
    "SupportError",
    "SupportStore",
    "agent_id_for_user",
    "create_schema",
    "reset_tables",
    "schema_path",
    "split_amount",
]

SUPPORT_TABLES = ("creator_support_purchases",)

#: The platform keeps at least this share of every support payment.
PLATFORM_COMMISSION_RATE = 0.20

#: Value of one Puspa credit in Rupiah: the highest per-credit price
#: in the public credit packs (pack-20: Rp9,000 for 20 credits), so
#: the value-below-price proof is conservative at every pack rate.
CREDIT_VALUE_IDR = 450

#: Smallest support payment (still grants at least 1 credit:
#: floor(1000 * 80% / 450) = 1). Below this a payment could grant
#: zero credits, which is not a support at all.
MIN_SUPPORT_IDR = 1_000

#: Largest single support payment.
MAX_SUPPORT_IDR = 1_000_000

#: The only subject a support commission may apply to: a digital
#: gallery work. Anything else is refused by split_amount.
GALLERY_SUBJECT_KIND = "gallery_work"

#: Kind marker inside the ledger event content (the ledger's closed
#: event set gains no new type for this feature).
SUPPORT_EVENT_KIND = "creator_support"


class SupportError(ValueError):
    """A support rule refused the request (bad amount, wrong subject, no agent)."""


def schema_path() -> Path:
    """Absolute path of migration 0043_creator_support."""
    return (
        Path(__file__).resolve().parents[2]
        / "puspa"
        / "migrations"
        / "versions"
        / "0043_creator_support.sql"
    )


def create_schema(dsn=None):
    """Apply the support table (migration 0043_creator_support). Idempotent; safe per test run."""
    apply_migrations(resolve_dsn(dsn), target="0043")


def reset_tables(dsn=None):
    """Empty the support table (for tests)."""
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(SUPPORT_TABLES) + " CASCADE")
        conn.commit()


def agent_id_for_user(dsn, user_id: str) -> str | None:
    """The Puspa agent id registered to a user, or None when they have none."""
    with psycopg.connect(resolve_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT agent_id FROM agents WHERE user_id = %s ORDER BY agent_id LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
    return str(row[0]) if row is not None else None


def split_amount(amount_idr: int, *, subject_kind: str = GALLERY_SUBJECT_KIND) -> dict:
    """Split one support payment into platform commission + creator credits.

    Pure function, so the money math is testable without a database.
    The creator's share is at most 80% of the amount, converted to
    whole credits at ``CREDIT_VALUE_IDR`` each; the commission is
    whatever is left, so the split always sums exactly to the amount
    paid and the commission is never below the 20% rate.
    """
    if subject_kind != GALLERY_SUBJECT_KIND:
        raise SupportError(
            f"commission applies only to digital gallery works, never to {subject_kind!r}"
        )
    if isinstance(amount_idr, bool) or not isinstance(amount_idr, int):
        raise SupportError(f"amount_idr must be a whole number of Rupiah, got {amount_idr!r}")
    if amount_idr < MIN_SUPPORT_IDR or amount_idr > MAX_SUPPORT_IDR:
        raise SupportError(
            f"amount_idr must be between Rp{MIN_SUPPORT_IDR:,} and "
            f"Rp{MAX_SUPPORT_IDR:,}, got Rp{amount_idr:,}"
        )
    creator_percent = 100 - round(PLATFORM_COMMISSION_RATE * 100)
    creator_share_idr = amount_idr * creator_percent // 100
    credits_granted = creator_share_idr // CREDIT_VALUE_IDR
    if credits_granted < 1:
        raise SupportError("amount is too small to grant even one credit")
    credit_value_idr = credits_granted * CREDIT_VALUE_IDR
    if credit_value_idr >= amount_idr:  # pragma: no cover - floor + 20% makes this unreachable
        raise SupportError("creator credit value must stay below the price paid")
    return {
        "amount_idr": amount_idr,
        "commission_idr": amount_idr - credit_value_idr,
        "credits_granted": credits_granted,
        "credit_value_idr": CREDIT_VALUE_IDR,
    }


def _receipt(row: dict) -> dict:
    """One support purchase as the API shows it."""
    return {
        "id": row["id"],
        "work_id": row["work_id"],
        "supporter_id": row["supporter_id"],
        "creator_id": row["creator_id"],
        "amount_idr": int(row["amount_idr"]),
        "commission_idr": int(row["commission_idr"]),
        "credits_granted": int(row["credits_granted"]),
        "credit_value_idr": CREDIT_VALUE_IDR,
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
    }


class SupportStore:
    """Record creator support purchases and read back their totals.

    The store only ever INSERTs and SELECTs: a recorded purchase is
    never changed or deleted, so the table is the durable record of
    every support. Supporting your own work is allowed on purpose:
    the split makes it lose money, and the tests prove the loss at
    every amount instead of hiding the case behind a refusal.
    """

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def support(
        self,
        supporter_id: str,
        work_id: str,
        amount_idr: int,
        *,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
    ) -> dict:
        """Support a published gallery work; returns the receipt.

        The creator receives ``credits_granted`` Puspa credits
        (worth strictly less than ``amount_idr``); the platform
        keeps ``commission_idr``. The purchase is recorded in the
        support table and, when a ledger is given, as an
        ``evidence_recorded`` event in the hash-chained ledger.
        Nothing here moves real money: the purchase is prepared
        only (B8), status ``'prepared'``.
        """
        split = split_amount(amount_idr)
        if ledger is not None and agent_id is None:
            agent_id = agent_id_for_user(self.dsn, supporter_id)
            if agent_id is None:
                raise SupportError(
                    "supporter has no registered Puspa agent; the ledger entry needs an agent id"
                )
        support_id = f"sup-{uuid.uuid4().hex[:12]}"
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            work = conn.execute(
                "SELECT id, owner_id, status FROM gallery_works WHERE id = %s",
                (work_id,),
            ).fetchone()
            if work is None or work["status"] != "published":
                raise KeyError(f"gallery work {work_id!r} not found")
            conn.execute(
                "INSERT INTO creator_support_purchases "
                "(id, work_id, supporter_id, creator_id, amount_idr, "
                "commission_idr, credits_granted, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'prepared')",
                (
                    support_id,
                    work_id,
                    supporter_id,
                    work["owner_id"],
                    split["amount_idr"],
                    split["commission_idr"],
                    split["credits_granted"],
                ),
            )
            if ledger is not None:
                assert agent_id is not None  # resolved above when a ledger is given
                ledger.append_event(
                    task_id=support_id,
                    agent_id=agent_id,
                    event_type="evidence_recorded",
                    content={
                        "kind": SUPPORT_EVENT_KIND,
                        "support_id": support_id,
                        "work_id": work_id,
                        "supporter_id": supporter_id,
                        "creator_id": work["owner_id"],
                        "amount_idr": split["amount_idr"],
                        "commission_idr": split["commission_idr"],
                        "credits_granted": split["credits_granted"],
                        "credit_value_idr": CREDIT_VALUE_IDR,
                        "status": "prepared",
                    },
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM creator_support_purchases WHERE id = %s",
                (support_id,),
            ).fetchone()
            assert row is not None  # the purchase was just inserted above
        return _receipt(row)

    def get(self, support_id: str) -> dict:
        """One purchase receipt by id; KeyError when unknown."""
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM creator_support_purchases WHERE id = %s",
                (support_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"support purchase {support_id!r} not found")
        return _receipt(row)

    def list_for_work(self, work_id: str) -> list[dict]:
        """Every support of one work, newest first."""
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM creator_support_purchases WHERE work_id = %s "
                "ORDER BY created_at DESC",
                (work_id,),
            ).fetchall()
        return [_receipt(row) for row in rows]

    def totals_for_work(self, work_id: str) -> dict:
        """Support totals for one work: count, amount, commission, credits."""
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            work = conn.execute(
                "SELECT id FROM gallery_works WHERE id = %s",
                (work_id,),
            ).fetchone()
            if work is None:
                raise KeyError(f"gallery work {work_id!r} not found")
            totals = conn.execute(
                "SELECT COUNT(*) AS support_count, "
                "COALESCE(SUM(amount_idr), 0) AS total_amount_idr, "
                "COALESCE(SUM(commission_idr), 0) AS total_commission_idr, "
                "COALESCE(SUM(credits_granted), 0) AS total_credits_granted "
                "FROM creator_support_purchases WHERE work_id = %s",
                (work_id,),
            ).fetchone()
            assert totals is not None  # aggregate always returns one row
        return {
            "work_id": work_id,
            "support_count": int(totals["support_count"]),
            "total_amount_idr": int(totals["total_amount_idr"]),
            "total_commission_idr": int(totals["total_commission_idr"]),
            "total_credits_granted": int(totals["total_credits_granted"]),
        }

    def credits_for_creator(self, creator_id: str) -> dict:
        """Credits one creator has received from all supports of their works."""
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            totals = conn.execute(
                "SELECT COUNT(*) AS supports_received, "
                "COALESCE(SUM(credits_granted), 0) AS credits_received "
                "FROM creator_support_purchases WHERE creator_id = %s",
                (creator_id,),
            ).fetchone()
            assert totals is not None  # aggregate always returns one row
        return {
            "user_id": creator_id,
            "credits_received": int(totals["credits_received"]),
            "supports_received": int(totals["supports_received"]),
        }
