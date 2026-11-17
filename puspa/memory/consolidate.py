"""Konsolidasi saat sepi — job merapikan episodik lama (definisi 2.7, C60).

Saat sistem idle, job ini:
1. Meringkas episodik lama (lebih tua dari ambang) per tugas.
2. Tiap ringkasan MENUNJUK ke kejadian asal di Buku Kerja
   (`kejadian_ids`) — bisa ditelusuri dan dikoreksi.
3. Memindahkan pola tugas yang berhasil ke prosedural.

Ringkasan di sini DETERMINISTIK (ekstraktif: potongan isi episode,
tanpa model) — biaya Rp0 dan hasilnya bisa diuji. Ringkasan
berbasis model adalah peningkatan Fase 2+ (tetap menunjuk ke
kejadian asal yang sama). Penjadwal yang memanggil job ini saat idle
adalah bagian Fase 2 (2.14); di sini job-nya bisa dipanggil manual.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from . import episodic, procedural
from .schema import resolve_dsn

MAX_RINGKASAN_CHARS = 2000


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _extractive_summary(episodes):
    """Ringkasan ekstraktif: satu baris per episode, dipotong rapi."""
    lines = []
    total = 0
    for ep in episodes:
        isi = " ".join(str(ep["isi"]).split())
        if len(isi) > 200:
            isi = isi[:197] + "..."
        baris = f"- [{ep['jenis']}] {isi}"
        if total + len(baris) > MAX_RINGKASAN_CHARS:
            break
        lines.append(baris)
        total += len(baris)
    return "\n".join(lines)


def consolidate_idle(user_id, older_than_days=30, agen_id=None, dsn=None,
                     now=None):
    """Jalankan satu putaran konsolidasi untuk user ini.

    Kembalikan dict {ringkasan_dibuat, prosedur_dipromosikan, tugas}.
    Episode yang sudah masuk ringkasan tidak diringkas ulang (dicek
    lewat `kejadian_ids` yang sudah tersimpan).
    """
    now = now or datetime.now(timezone.utc)
    ambang = now - timedelta(days=older_than_days)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT tugas_id FROM episodik WHERE user_id = %s "
                "AND dibuat < %s AND tugas_id IS NOT NULL",
                (user_id, ambang),
            )
            tugas_ids = [r["tugas_id"] for r in cur.fetchall()]
            cur.execute(
                "SELECT kejadian_ids FROM ringkasan WHERE user_id = %s",
                (user_id,),
            )
            sudah = set()
            for r in cur.fetchall():
                sudah.update(str(k) for k in (r["kejadian_ids"] or []))
    ringkasan_dibuat = 0
    prosedur_dipromosikan = 0
    tugas_diproses = []
    for tugas_id in tugas_ids:
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, jenis, isi, kejadian_id, dibuat FROM episodik "
                    "WHERE user_id = %s AND tugas_id = %s ORDER BY dibuat ASC",
                    (user_id, tugas_id),
                )
                rows = cur.fetchall()
        episodes = [
            {"id": str(r["id"]), "jenis": r["jenis"], "isi": r["isi"],
             "kejadian_id": str(r["kejadian_id"]) if r["kejadian_id"] else None,
             "dibuat": r["dibuat"]}
            for r in rows
        ]
        baru = [e for e in episodes if e["kejadian_id"] and e["kejadian_id"] not in sudah]
        if not baru:
            continue
        teks = _extractive_summary(baru)
        kejadian_ids = [e["kejadian_id"] for e in baru if e["kejadian_id"]]
        periode_awal = min(e["dibuat"] for e in baru)
        periode_akhir = max(e["dibuat"] for e in baru)
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ringkasan (user_id, agen_id, periode_awal, "
                    "periode_akhir, ringkasan, kejadian_ids) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (user_id, agen_id, periode_awal, periode_akhir, teks, kejadian_ids),
                )
            conn.commit()
        ringkasan_dibuat += 1
        tugas_diproses.append(str(tugas_id))
        # Pola berhasil -> prosedural: episode tugas_selesai yang
        # isinya menandai berhasil.
        selesai = [e for e in baru if e["jenis"] == "tugas_selesai"]
        if selesai and any("berhasil" in e["isi"].lower() for e in selesai):
            layanan = selesai[0]["isi"].split(":", 1)[0].strip() or "tugas"
            langkah = [e["isi"] for e in baru if e["jenis"] != "tugas_selesai"] or [
                e["isi"] for e in selesai
            ]
            procedural.record_successful_task(
                user_id, layanan, langkah, agen_id=agen_id,
                kejadian_asal_id=kejadian_ids[0] if kejadian_ids else None,
                dsn=dsn,
            )
            prosedur_dipromosikan += 1
    return {
        "ringkasan_dibuat": ringkasan_dibuat,
        "prosedur_dipromosikan": prosedur_dipromosikan,
        "tugas": tugas_diproses,
    }


def list_summaries(user_id, dsn=None):
    """Daftar ringkasan milik user, terbaru dulu."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, agen_id, periode_awal, periode_akhir, "
                "ringkasan, kejadian_ids, dibuat FROM ringkasan "
                "WHERE user_id = %s ORDER BY dibuat DESC",
                (user_id,),
            )
            return [
                {
                    "id": str(r["id"]),
                    "user_id": str(r["user_id"]),
                    "agen_id": str(r["agen_id"]) if r["agen_id"] else None,
                    "periode_awal": r["periode_awal"],
                    "periode_akhir": r["periode_akhir"],
                    "ringkasan": r["ringkasan"],
                    "kejadian_ids": [str(k) for k in (r["kejadian_ids"] or [])],
                    "dibuat": r["dibuat"],
                }
                for r in cur.fetchall()
            ]
