"""Pemberian + pencabutan konektor per agen (§2.8 revisi 7).

Puspa mulai dengan Gmail, Drive, Calendar. Agen baru mulai kosong;
user yang memberikan konektor yang dibutuhkannya, dari layar
Konektor PWA. Pencabutan menghapus baris pemberian (token di vault
tetap milik pengguna; konektor lain agen yang sama tidak terganggu).
"""

from __future__ import annotations

import os

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
CONNECTOR_DIIZINKAN = ("gmail", "drive", "calendar")
SCOPE_PER_CONNECTOR = {
    "gmail": ["read", "create_draft", "send"],
    "drive": ["read"],
    "calendar": ["read", "create_event"],
}


class GrantError(Exception):
    """Pemberian konektor tidak sah."""


def _connect(dsn=None):
    return psycopg.connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def give_connector(user_id: str, agent_id: str, connector: str, dsn=None) -> dict:
    """Beri konektor ke satu agen (scope minimal bawaan konektor)."""
    if connector not in CONNECTOR_DIIZINKAN:
        raise GrantError(f"konektor tidak dikenal: {connector!r}")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM agent_meta WHERE agent_id = %s AND user_id = %s",
                        (agent_id, user_id))
            if cur.fetchone() is None:
                raise GrantError("agen tidak dikenal / bukan milik pengguna")
            cur.execute(
                "INSERT INTO agent_connectors (user_id, agent_id, connector, scope, terhubung)"
                " VALUES (%s, %s, %s, %s, TRUE)"
                " ON CONFLICT (user_id, agent_id, connector) DO UPDATE"
                " SET scope = EXCLUDED.scope, terhubung = TRUE",
                (user_id, agent_id, connector, SCOPE_PER_CONNECTOR[connector]),
            )
        conn.commit()
    return {"agent_id": agent_id, "connectors": connector,
            "scope": SCOPE_PER_CONNECTOR[connector], "terhubung": True}


def cabut_connector(user_id: str, agent_id: str, connector: str, dsn=None) -> None:
    """Cabut konektor dari satu agen."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_connectors WHERE user_id = %s AND agent_id = %s AND connector = %s",
                (user_id, agent_id, connector),
            )
        conn.commit()


def items_connector(user_id: str, agent_id: str, dsn=None) -> list[dict]:
    """Konektor + scope + status terhubung untuk satu agen."""
    with _connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT connector, scope, terhubung FROM agent_connectors"
                " WHERE user_id = %s AND agent_id = %s ORDER BY connector",
                (user_id, agent_id),
            )
            return [dict(r) for r in cur.fetchall()]


def punya_connector(user_id: str, agent_id: str, connector: str, dsn=None) -> bool:
    """True bila agen ini terhubung ke konektor itu."""
    return any(k["connectors"] == connector and k["terhubung"]
               for k in items_connector(user_id, agent_id, dsn=dsn))
