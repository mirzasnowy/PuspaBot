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
from datetime import date, datetime, timezone

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
BATAS_READ_DAILY = 20


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def add_watchlist(*, user_id: str, product_id: str, price_threshold=None,
                     dsn=None) -> dict:
    """Tandai barang untuk dipantau (idempotent per user+produk)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO watchlist (user_id, product_id, price_threshold)"
                " VALUES (%s, %s, %s)"
                " ON CONFLICT (user_id, product_id) DO UPDATE SET price_threshold = EXCLUDED.price_threshold,"
                " active = TRUE RETURNING id, active",
                (user_id, product_id, price_threshold))
            row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"]), "active": row["active"]}


def items_watchlist(user_id: str, dsn=None) -> list[dict]:
    """Daftar barang yang dipantau user (aktif saja)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT w.id, w.product_id, w.price_threshold, p.brand, p.series,"
                " p.model_number, p.category FROM watchlist w"
                " JOIN canonical_products p ON p.id = w.product_id"
                " WHERE w.user_id = %s AND w.active ORDER BY w.created_at",
                (user_id,))
            return [{"id": str(r["id"]), "product_id": str(r["product_id"]),
                     "price_threshold": float(r["price_threshold"]) if r["price_threshold"] is not None else None,
                     "brand": r["brand"], "series": r["series"],
                     "model_number": r["model_number"], "category": r["category"]}
                    for r in cur.fetchall()]


def sisa_anggaran_read(user_id: str, dsn=None, tanggal: date | None = None) -> int:
    """Sisa anggaran baca hari ini (maks 20/hari/pengguna)."""
    tanggal = tanggal or datetime.now(timezone.utc).date()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT amount FROM read_budgets WHERE user_id = %s AND tanggal = %s",
                (user_id, tanggal))
            row = cur.fetchone()
    return BATAS_READ_DAILY - (int(row["amount"]) if row else 0)


def _use_anggaran(user_id: str, amount: int, dsn, tanggal: date) -> bool:
    """Pakai anggaran baca; False bila sudah habis (sampler berhenti,
    bukan membaca diam-diam melewati batas)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO read_budgets (user_id, tanggal, amount) VALUES (%s, %s, 0)"
                " ON CONFLICT (user_id, tanggal) DO NOTHING", (user_id, tanggal))
            cur.execute(
                "SELECT amount FROM read_budgets WHERE user_id = %s AND tanggal = %s FOR UPDATE",
                (user_id, tanggal))
            dipakai = int(cur.fetchone()["amount"])
            if dipakai + amount > BATAS_READ_DAILY:
                return False
            cur.execute(
                "UPDATE read_budgets SET amount = amount + %s WHERE user_id = %s AND tanggal = %s",
                (amount, user_id, tanggal))
        conn.commit()
    return True


def jalankan_sampler(*, user_id: str, agent_id: str, read_price,
                     dsn=None) -> dict:
    """Satu putaran sampler: baca harga tiap barang watchlist aktif.

    `baca_harga(produk) -> {"harga", "bukti_id", "tautan_id", ...} |
    None` disuntikkan (di produksi: wedge bandingkan harga; di tes:
    fake). Anggaran 20/hari ditegakkan; barang yang anggarannya habis
    dilewati dan dilaporkan. Kembalikan {"dibaca", "dilewati",
    "alert": [...]} — alert bila harga <= ambang.
    """
    tanggal = datetime.now(timezone.utc).date()
    dibaca, dilewati, alert = [], [], []
    for product in items_watchlist(user_id, dsn=dsn):
        if not _use_anggaran(user_id, 1, dsn, tanggal):
            dilewati.append({"product_id": product["product_id"],
                             "reason": "anggaran_read_daily_habis"})
            continue
        try:
            result = read_price(product)
        except Exception as exc:  # noqa: BLE001 — satu barang gagal, lanjut
            dilewati.append({"product_id": product["product_id"],
                             "reason": f"baca_gagal: {exc}"[:200]})
            continue
        if not result:
            dilewati.append({"product_id": product["product_id"],
                             "reason": "not_can_dibaca"})
            continue
        dibaca.append(product["product_id"])
        ambang = product["price_threshold"]
        if ambang is not None and result.get("price") is not None and result["price"] <= ambang:
            alert.append({"product_id": product["product_id"],
                          "price": result["price"], "price_threshold": ambang})
    return {"dibaca": dibaca, "dilewati": dilewati, "alert": alert,
            "sisa_anggaran": sisa_anggaran_read(user_id, dsn=dsn, tanggal=tanggal)}
