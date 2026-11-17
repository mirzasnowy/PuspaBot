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
BATAS_BACA_HARIAN = 20


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def tambah_watchlist(*, user_id: str, produk_id: str, ambang_harga=None,
                     dsn=None) -> dict:
    """Tandai barang untuk dipantau (idempotent per user+produk)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO watchlist (user_id, produk_id, ambang_harga)"
                " VALUES (%s, %s, %s)"
                " ON CONFLICT (user_id, produk_id) DO UPDATE SET ambang_harga = EXCLUDED.ambang_harga,"
                " aktif = TRUE RETURNING id, aktif",
                (user_id, produk_id, ambang_harga))
            row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"]), "aktif": row["aktif"]}


def daftar_watchlist(user_id: str, dsn=None) -> list[dict]:
    """Daftar barang yang dipantau user (aktif saja)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT w.id, w.produk_id, w.ambang_harga, p.merek, p.seri,"
                " p.nomor_model, p.kategori FROM watchlist w"
                " JOIN produk_kanonik p ON p.id = w.produk_id"
                " WHERE w.user_id = %s AND w.aktif ORDER BY w.dibuat",
                (user_id,))
            return [{"id": str(r["id"]), "produk_id": str(r["produk_id"]),
                     "ambang_harga": float(r["ambang_harga"]) if r["ambang_harga"] is not None else None,
                     "merek": r["merek"], "seri": r["seri"],
                     "nomor_model": r["nomor_model"], "kategori": r["kategori"]}
                    for r in cur.fetchall()]


def sisa_anggaran_baca(user_id: str, dsn=None, tanggal: date | None = None) -> int:
    """Sisa anggaran baca hari ini (maks 20/hari/pengguna)."""
    tanggal = tanggal or datetime.now(timezone.utc).date()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT jumlah FROM anggaran_baca WHERE user_id = %s AND tanggal = %s",
                (user_id, tanggal))
            row = cur.fetchone()
    return BATAS_BACA_HARIAN - (int(row["jumlah"]) if row else 0)


def _pakai_anggaran(user_id: str, jumlah: int, dsn, tanggal: date) -> bool:
    """Pakai anggaran baca; False bila sudah habis (sampler berhenti,
    bukan membaca diam-diam melewati batas)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO anggaran_baca (user_id, tanggal, jumlah) VALUES (%s, %s, 0)"
                " ON CONFLICT (user_id, tanggal) DO NOTHING", (user_id, tanggal))
            cur.execute(
                "SELECT jumlah FROM anggaran_baca WHERE user_id = %s AND tanggal = %s FOR UPDATE",
                (user_id, tanggal))
            dipakai = int(cur.fetchone()["jumlah"])
            if dipakai + jumlah > BATAS_BACA_HARIAN:
                return False
            cur.execute(
                "UPDATE anggaran_baca SET jumlah = jumlah + %s WHERE user_id = %s AND tanggal = %s",
                (jumlah, user_id, tanggal))
        conn.commit()
    return True


def jalankan_sampler(*, user_id: str, agen_id: str, baca_harga,
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
    for barang in daftar_watchlist(user_id, dsn=dsn):
        if not _pakai_anggaran(user_id, 1, dsn, tanggal):
            dilewati.append({"produk_id": barang["produk_id"],
                             "alasan": "anggaran_baca_harian_habis"})
            continue
        try:
            hasil = baca_harga(barang)
        except Exception as exc:  # noqa: BLE001 — satu barang gagal, lanjut
            dilewati.append({"produk_id": barang["produk_id"],
                             "alasan": f"baca_gagal: {exc}"[:200]})
            continue
        if not hasil:
            dilewati.append({"produk_id": barang["produk_id"],
                             "alasan": "tidak_bisa_dibaca"})
            continue
        dibaca.append(barang["produk_id"])
        ambang = barang["ambang_harga"]
        if ambang is not None and hasil.get("harga") is not None and hasil["harga"] <= ambang:
            alert.append({"produk_id": barang["produk_id"],
                          "harga": hasil["harga"], "ambang_harga": ambang})
    return {"dibaca": dibaca, "dilewati": dilewati, "alert": alert,
            "sisa_anggaran": sisa_anggaran_baca(user_id, dsn=dsn, tanggal=tanggal)}
