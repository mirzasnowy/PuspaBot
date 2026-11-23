"""Watchlist + sampler harga (§3.8 revisi 7).

User menandai barang untuk dipantau; penjadwal (Fase 2) membaca
harganya berkala dengan anggaran baca maks 20/hari/pengguna; deret
waktu terisi sendiri; dasar riwayat 30 hari + alert harga turun
(1 kredit, §4.1).

Perbaikan atas catatan Fase 2: parser cron penuh BELUM ada (pemicu
digeser +1 hari) — sampler di sini memakai pemicu `harga_turun` /
`jadwal` Fase 2 apa adanya (sekali sehari per barang) dan mencatat
batas itu di STATUS_FASE3.md, bukan pura-pura punya cron per jam.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.db import maybe_begin_immediate as _db_begin_immediate

DEFAULT_DSN = None
DAILY_READ_LIMIT = 20


def _connect(dsn=None):
    return _db_connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def add_watchlist(*, user_id: str, product_id: str, price_threshold=None, dsn=None) -> dict:
    """Tandai barang untuk dipantau (idempotent per user+produk)."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO watchlist (user_id, product_id, price_threshold)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (user_id, product_id) DO UPDATE SET price_threshold = EXCLUDED.price_threshold,"
            " active = TRUE RETURNING id, active",
            (user_id, product_id, price_threshold),
        )
        row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"]), "active": row["active"]}


def list_watchlist(user_id: str, dsn=None) -> list[dict]:
    """Daftar barang yang dipantau user (aktif saja)."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT w.id, w.product_id, w.price_threshold, p.brand, p.series,"
            " p.model_number, p.category FROM watchlist w"
            " JOIN canonical_products p ON p.id = w.product_id"
            " WHERE w.user_id = %s AND w.active ORDER BY w.created_at",
            (user_id,),
        )
        return [
            {
                "id": str(r["id"]),
                "product_id": str(r["product_id"]),
                "price_threshold": float(r["price_threshold"])
                if r["price_threshold"] is not None
                else None,
                "brand": r["brand"],
                "series": r["series"],
                "model_number": r["model_number"],
                "category": r["category"],
            }
            for r in cur.fetchall()
        ]


def remaining_read_budget(user_id: str, dsn=None, day: date | None = None) -> int:
    """Sisa anggaran baca hari ini (maks 20/hari/pengguna)."""
    day = day or datetime.now(UTC).date()
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT amount FROM read_budgets WHERE user_id = %s AND date = %s", (user_id, day)
        )
        row = cur.fetchone()
    return DAILY_READ_LIMIT - (int(row["amount"]) if row else 0)


def _use_budget(user_id: str, amount: int, dsn, day: date) -> bool:
    """Pakai anggaran baca; False bila sudah habis (sampler berhenti,
    bukan membaca diam-diam melewati batas)."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        # The insert, the balance read and the debit are one write
        # transaction (BEGIN IMMEDIATE on SQLite; FOR UPDATE does
        # the same job on Postgres).
        _db_begin_immediate(conn)
        cur.execute(
            "INSERT INTO read_budgets (user_id, date, amount) VALUES (%s, %s, 0)"
            " ON CONFLICT (user_id, date) DO NOTHING",
            (user_id, day),
        )
        cur.execute(
            "SELECT amount FROM read_budgets WHERE user_id = %s AND date = %s FOR UPDATE",
            (user_id, day),
        )
        used = int(cur.fetchone()["amount"])
        if used + amount > DAILY_READ_LIMIT:
            return False
        cur.execute(
            "UPDATE read_budgets SET amount = amount + %s WHERE user_id = %s AND date = %s",
            (amount, user_id, day),
        )
        conn.commit()
    return True


def run_sampler(*, user_id: str, agent_id: str, read_price, dsn=None) -> dict:
    """Satu putaran sampler: baca harga tiap barang watchlist aktif.

    `baca_harga(produk) -> {"harga", "bukti_id", "tautan_id", ...} |
    None` disuntikkan (di produksi: wedge bandingkan harga; di tes:
    fake). Anggaran 20/hari ditegakkan; barang yang anggarannya habis
    dilewati dan dilaporkan. Kembalikan {"dibaca", "dilewati",
    "alert": [...]} — alert bila harga <= ambang.
    """
    day = datetime.now(UTC).date()
    read, skipped, alert = [], [], []
    for product in list_watchlist(user_id, dsn=dsn):
        if not _use_budget(user_id, 1, dsn, day):
            skipped.append(
                {"product_id": product["product_id"], "reason": "anggaran_read_daily_habis"}
            )
            continue
        try:
            result = read_price(product)
        except Exception as exc:
            skipped.append(
                {"product_id": product["product_id"], "reason": f"baca_gagal: {exc}"[:200]}
            )
            continue
        if not result:
            skipped.append({"product_id": product["product_id"], "reason": "not_can_dibaca"})
            continue
        read.append(product["product_id"])
        threshold = product["price_threshold"]
        if (
            threshold is not None
            and result.get("price") is not None
            and result["price"] <= threshold
        ):
            alert.append(
                {
                    "product_id": product["product_id"],
                    "price": result["price"],
                    "price_threshold": threshold,
                }
            )
    return {
        "dibaca": read,
        "dilewati": skipped,
        "alert": alert,
        "remaining_budget": remaining_read_budget(user_id, dsn=dsn, day=day),
    }
