"""Registry agen (§2.2 revisi 7).

Aturan yang ditegakkan di sini (kode, bukan UI):
- Maks 3 agen per pengguna (Puspa + 2 buatan user).
- Puspa wajib dan tidak bisa dihapus: penolakan di API (`hapus_agen`)
  dan di database (trigger `trg_tolak_hapus_puspa` di `agen_meta`:
  DELETE baris `wajib=TRUE` melempar exception).
- Agen baru mulai tanpa konektor (§2.8); Puspa mulai dengan Gmail,
  Drive, Calendar (diberikan di `ensure_puspa_agent`).

Keputusan Fase 2 (pertanyaan Fase 0, butir a): pendaftaran agen Puspa
per pengguna WAJIB — satu agen Puspa dibuat otomatis saat pengguna
dibuat / login pertama (`ensure_puspa_agent`). Alasan: pembaca
bermilik Fase 0 menolak fail-closed kejadian dari agen yang belum
terdaftar, jadi agen tanpa Puspa terdaftar akan membuat semua
kejadian tugasnya ditolak. Fungsi ini idempotent.
"""

from __future__ import annotations

import os
import uuid

import psycopg

from puspa.accounts.access import register_agent

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
MAX_AGENT = 3
NAME_PUSPA = "Puspa"


class AgentError(Exception):
    """Kegagalan registry agen (batas, hapus Puspa, pemilik salah)."""


def _connect(dsn=None):
    return psycopg.connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def _agent_id_puspa(user_id: str) -> str:
    """ID agen Puspa deterministik per pengguna (stabil lintas login)."""
    return f"puspa-{user_id}"


def ensure_puspa_agent(user_id: str, dsn=None) -> str:
    """Buat + daftarkan agen Puspa pengguna ini kalau belum ada.

    Kembalikan `agen_id`. Idempotent. Juga memberikan ketiga konektor
    bawaan Puspa (Gmail, Drive, Calendar) lewat `konektor_agen`.
    """
    agent_id = _agent_id_puspa(str(user_id))
    register_agent(agent_id, str(user_id), dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_meta (agent_id, user_id, name, peran, purpose, required)"
                " VALUES (%s, %s, %s, %s, %s, TRUE)"
                " ON CONFLICT (agent_id) DO NOTHING",
                (agent_id, user_id, NAME_PUSPA, "agen utama",
                 "Agen wajib Puspa: memegang profil inti user dan menjadi fallback."),
            )
            for connector, scope in (
                ("gmail", ["read", "create_draft", "send"]),
                ("drive", ["read"]),
                ("calendar", ["read", "create_event"]),
            ):
                cur.execute(
                    "INSERT INTO agent_connectors (user_id, agent_id, connector, scope, terhubung)"
                    " VALUES (%s, %s, %s, %s, TRUE)"
                    " ON CONFLICT (user_id, agent_id, connector) DO NOTHING",
                    (user_id, agent_id, connector, scope),
                )
        conn.commit()
    return agent_id


def create_agent(user_id: str, name: str, peran: str = "", purpose: str = "", dsn=None) -> dict:
    """Buat agen baru milik pengguna. Agen ke-4 ditolak (maks 3)."""
    if not name or not name.strip():
        raise AgentError("nama agen tidak boleh kosong")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_meta WHERE user_id = %s", (user_id,))
            if cur.fetchone()[0] >= MAX_AGENT:
                raise AgentError(f"maks {MAX_AGENT} agen per pengguna")
    agent_id = f"agen-{uuid.uuid4().hex[:12]}"
    register_agent(agent_id, str(user_id), dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_meta (agent_id, user_id, name, peran, purpose, required)"
                " VALUES (%s, %s, %s, %s, %s, FALSE)",
                (agent_id, user_id, name.strip(), peran, purpose),
            )
        conn.commit()
    return {"agent_id": agent_id, "name": name.strip(), "peran": peran,
            "purpose": purpose, "required": False}


def items_agent(user_id: str, dsn=None) -> list[dict]:
    """Semua agen milik pengguna, Puspa wajib selalu pertama."""
    with _connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT agent_id, name, peran, purpose, wajib FROM agent_meta"
                " WHERE user_id = %s ORDER BY wajib DESC, created_at ASC",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def delete_agent(user_id: str, agent_id: str, dsn=None) -> None:
    """Hapus agen. Puspa (wajib) DITOLAK; agen orang lain DITOLAK."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT wajib, user_id FROM agent_meta WHERE agent_id = %s", (agent_id,)
            )
            row = cur.fetchone()
            if row is None:
                raise AgentError(f"agen tidak dikenal: {agent_id!r}")
            if str(row[1]) != str(user_id):
                raise AgentError("agen milik pengguna lain")
            if row[0]:
                raise AgentError("Puspa tidak bisa dihapus")
            cur.execute("DELETE FROM agent_meta WHERE agent_id = %s", (agent_id,))
            cur.execute("DELETE FROM agents WHERE agent_id = %s", (agent_id,))
        conn.commit()
