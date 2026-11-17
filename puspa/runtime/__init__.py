"""Skema Postgres Fase 2 (Runtime tim + Bukti + Penjadwal + Kaset).

Tabel baru (semua punya kolom `user_id` kecuali `detak_sistem`):
- `agen_meta`: nama/peran/tujuan agen + penanda wajib (Puspa).
- `tugas`: satu baris per tugas (status + biaya berjalan). Kejadian
  rinci tetap di Buku Kerja (`kejadian`, Fase 0); tabel ini proyeksi
  yang bisa dibangun ulang dari sana.
- `handoff`: kontrak handoff yang diajukan + status mesinnya.
- `bukti`: satu rekaman bukti per angka (§2.5 revisi 7).
- `pemicu`: pemicu penjadwal (§2.14) + `pemicu_jalan` (kunci
  idempotensi tiap jalan pemicu).
- `konektor_agen`: konektor yang diberikan user per agen (§2.8).
- `kaset`: metadata kaset; isi kaset disimpan sebagai file JSON di
  `cassettes/` (lokal, ikut terhapus saat tugas dihapus).
- `langganan_push`: langganan Web Push per pengguna (§2.14).
- `detak_sistem`: detak penjadwal (komponen `penjadwal`).

RLS: diaktifkan di tabel ber-`user_id` dengan kebijakan yang sama
seperti Fase 0/1 (`app.current_user_id`, lihat `puspa.accounts.rls`).
Pemilik tabel/superuser melewati RLS (sifat Postgres); penegakan nyata
hanya untuk koneksi sebagai peran `puspa_app` (lihat STATUS Fase 0,
keputusan 15). Tabel `kejadian` SENGAJA tidak diberi RLS (keputusan
Fase 2, lihat STATUS_FASE2.md): ia tidak punya kolom `user_id` dan
isolasinya lewat registry agen + `require_owner`.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

TABLES_FASE2 = (
    "agent_meta", "tasks", "handoffs", "evidence", "triggers", "trigger_jalan",
    "connector_agent", "cassettes", "push_subscriptions", "heartbeat_sistem",
)


def create_schema(dsn: str | None = None) -> None:
    """Jalankan `infra/schema_runtime.sql` (idempotent).

    Keputusan Fase 2 (pertanyaan Fase 0, butir c): peran `puspa_app`
    DIBERI LOGIN + password — tapi hanya bila env `PUSPA_APP_PASSWORD`
    diisi (tanpa env, peran tetap NOLOGIN seperti Fase 0; tidak ada
    password default yang ditulis ke mana pun).
    """
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    sql = (Path(__file__).resolve().parent.parent.parent / "infra" / "schema_runtime.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql)
        password = os.environ.get("PUSPA_APP_PASSWORD")
        if password:
            conn.execute("ALTER ROLE puspa_app WITH LOGIN PASSWORD %s", (password,))


def reset_tables(dsn: str | None = None) -> None:
    """Kosongkan semua tabel Fase 2 (hanya untuk tes)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE " + ", ".join(TABLES_FASE2) + " CASCADE")
        conn.commit()
