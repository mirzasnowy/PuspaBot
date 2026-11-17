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

MAX_SUMMARY_CHARS = 2000


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _extractive_summary(episodes):
    """Ringkasan ekstraktif: satu baris per episode, dipotong rapi."""
    lines = []
    total = 0
    for ep in episodes:
        content = " ".join(str(ep["content"]).split())
        if len(content) > 200:
            content = content[:197] + "..."
        rows = f"- [{ep['type']}] {content}"
        if total + len(rows) > MAX_SUMMARY_CHARS:
            break
        lines.append(rows)
        total += len(rows)
    return "\n".join(lines)


def consolidate_idle(user_id, older_than_days=30, agent_id=None, dsn=None,
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
                "SELECT DISTINCT task_id FROM episodic WHERE user_id = %s "
                "AND created_at < %s AND task_id IS NOT NULL",
                (user_id, ambang),
            )
            task_ids = [r["task_id"] for r in cur.fetchall()]
            cur.execute(
                "SELECT event_ids FROM summaries WHERE user_id = %s",
                (user_id,),
            )
            already = set()
            for r in cur.fetchall():
                already.update(str(k) for k in (r["event_ids"] or []))
    summary_created = 0
    prosedur_dipromosikan = 0
    task_diproses = []
    for task_id in task_ids:
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, type, content, event_id, created_at FROM episodic "
                    "WHERE user_id = %s AND task_id = %s ORDER BY created_at ASC",
                    (user_id, task_id),
                )
                rows = cur.fetchall()
        episodes = [
            {"id": str(r["id"]), "type": r["type"], "content": r["content"],
             "event_id": str(r["event_id"]) if r["event_id"] else None,
             "created_at": r["created_at"]}
            for r in rows
        ]
        new = [e for e in episodes if e["event_id"] and e["event_id"] not in already]
        if not new:
            continue
        text = _extractive_summary(new)
        event_ids = [e["event_id"] for e in new if e["event_id"]]
        period_start = min(e["created_at"] for e in new)
        period_end = max(e["created_at"] for e in new)
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO summaries (user_id, agent_id, period_start, "
                    "period_end, summary, event_ids) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (user_id, agent_id, period_start, period_end, text, event_ids),
                )
            conn.commit()
        summary_created += 1
        task_diproses.append(str(task_id))
        # Pola berhasil -> prosedural: episode tugas_selesai yang
        # isinya menandai berhasil.
        completed_at = [e for e in new if e["type"] == "task_completed"]
        if completed_at and any("successful" in e["content"].lower() for e in completed_at):
            service = completed_at[0]["content"].split(":", 1)[0].strip() or "tasks"
            step = [e["content"] for e in new if e["type"] != "task_completed"] or [
                e["content"] for e in completed_at
            ]
            procedural.record_successful_task(
                user_id, service, step, agent_id=agent_id,
                source_event_id=event_ids[0] if event_ids else None,
                dsn=dsn,
            )
            prosedur_dipromosikan += 1
    return {
        "summary_created": summary_created,
        "prosedur_dipromosikan": prosedur_dipromosikan,
        "tasks": task_diproses,
    }


def list_summaries(user_id, dsn=None):
    """Daftar ringkasan milik user, terbaru dulu."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, agent_id, period_start, period_end, "
                "summary, event_ids, created_at FROM summaries "
                "WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            return [
                {
                    "id": str(r["id"]),
                    "user_id": str(r["user_id"]),
                    "agent_id": str(r["agent_id"]) if r["agent_id"] else None,
                    "period_start": r["period_start"],
                    "period_end": r["period_end"],
                    "summaries": r["summaries"],
                    "event_ids": [str(k) for k in (r["event_ids"] or [])],
                    "created_at": r["created_at"],
                }
                for r in cur.fetchall()
            ]
