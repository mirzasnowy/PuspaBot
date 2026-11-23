"""Irisan memori — sub-agen tidak menyentuh penyimpanan (definisi 2.7, C57).

Aturan: sub-agen tidak punya akses ke penyimpanan memori. Agen
memberinya irisan: query terbatas + kolom yang diizinkan, lewat token.
Pemberian irisan DICATAT (tabel `pemberian_irisan`).

CATATAN KONFLIK (dicatat di STATUS_FASE1.md, bukan diubah diam-diam):
definisi 2.7 meminta pemberian irisan dicatat DI BUKU KERJA, tapi
himpunan jenis kejadian Buku Kerja tertutup (31 jenis, revisi 7 §2.3)
dan tidak ada jenis `irisan_diberikan`. Menambah jenis butuh perubahan
skema Fase 0 + migrasi CHECK — itu keputusan revisi 8 / Fase 2.
Sementara ini pencatatan memakai tabel audit `pemberian_irisan`
(milik pengguna, ber-RLS, bisa diaudit); kolom `kejadian_id` disiapkan
untuk menautkan ke kejadian Buku Kerja begitu jenisnya ada.
"""

from __future__ import annotations

from puspa.db import connect as _db_connect
from puspa.db import dict_row

from . import episodic, profile
from .schema import resolve_dsn

#: Kolom yang BOLEH diberikan sebagai irisan (himpunan tertutup).
ALLOWED_COLUMNS = frozenset({"profile", "episodic", "procedures"})


def _connect(dsn=None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


def grant_slice(user_id, agent_id, subagent_id, query, columns, token_ref, dsn=None):
    """Beri irisan memori ke satu sub-agen; kembalikan data irisannya.

    - `kolom`: subset dari {profil, episodik, prosedur}.
    - `query`: query terbatas (maks 500 karakter); dipakai untuk cari
      episodik / saring profil.
    - `token_ref`: referensi token izin sub-agen (string tak kosong).
    - Pemberian dicatat di `pemberian_irisan` sebelum data dikembalikan.
    - Data yang dikembalikan HANYA berisi kolom yang diminta, milik
      user ini, dan (untuk episodik) maksimal 5 hasil.
    """
    if not isinstance(query, str) or not query or len(query) > 500:
        raise ValueError("query harus string tak kosong, maks 500 karakter")
    columns = tuple(columns or [])
    if not columns:
        raise ValueError("kolom irisan tidak boleh kosong")
    if not set(columns) <= ALLOWED_COLUMNS:
        raise ValueError(f"kolom tidak diizinkan: {sorted(set(columns) - ALLOWED_COLUMNS)}")
    if not token_ref or not isinstance(token_ref, str):
        raise ValueError("token_ref harus string tak kosong")
    data = {}
    if "profile" in columns:
        profile_data = profile.get_profile(user_id, agent_id=agent_id, dsn=dsn)
        query_lower = query.lower()
        data["profile"] = {
            k: v
            for k, v in profile_data.items()
            if query_lower in k.lower() or query_lower in v.lower()
        } or profile_data
    if "episodic" in columns:
        data["episodic"] = episodic.search_episodes(
            user_id, query, limit=5, agent_id=agent_id, dsn=dsn
        )
    if "procedures" in columns:
        from . import procedural

        all_procedures = procedural.list_procedures(user_id, dsn=dsn)
        query_lower = query.lower()
        data["procedures"] = [p for p in all_procedures if query_lower in p["service"].lower()]
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO slice_grants (user_id, agent_id, subagent_id, "
                "query, kolom, token_ref) VALUES (%s, %s, %s, %s, %s, %s) "
                "RETURNING id, created_at",
                (user_id, agent_id, subagent_id, query, list(columns), token_ref),
            )
            row = cur.fetchone()
        conn.commit()
    data["_slice_id"] = str(row["id"])
    data["_recorded_at"] = row["created_at"]
    return data


def list_grants(user_id, dsn=None):
    """Daftar pemberian irisan milik user, terbaru dulu."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, agent_id, subagent_id, query, kolom, "
            "token_ref, event_id, created_at FROM slice_grants "
            "WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        return [
            {
                "id": str(r["id"]),
                "user_id": str(r["user_id"]),
                "agent_id": str(r["agent_id"]) if r["agent_id"] else None,
                "subagent_id": str(r["subagent_id"]) if r["subagent_id"] else None,
                "query": r["query"],
                "columns": list(r["kolom"]),
                "token_ref": r["token_ref"],
                "event_id": str(r["event_id"]) if r["event_id"] else None,
                "created_at": r["created_at"],
            }
            for r in cur.fetchall()
        ]
