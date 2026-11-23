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

Refleksi (pola Generative Agents, MIT, ditulis ulang, bukan
disalin): `reflect_episodes` mengubah episode menjadi inferensi
tingkat tinggi (layanan yang andal, kegagalan berulang, preferensi
user). Setiap inferensi diusulkan sebagai fakta profil lewat
Usulan di `puspa/memory/profile.py`, tidak pernah masuk profil
diam-diam. Setiap inferensi menunjuk ke kejadian asal
(`evidence_event_ids`) dan butuh minimal 2 bukti.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from puspa.db import connect as _db_connect
from puspa.db import dict_row

from . import procedural, profile
from .limits import truncate_narrative
from .schema import resolve_dsn

MAX_SUMMARY_CHARS = 2000
REFLECTION_MIN_EVIDENCE = 2

_PREFERENCE_MARKERS = (
    "prefer",
    "always",
    "likes",
    "like",
    "suka",
    "selalu",
    "gemar",
)
_SUCCESS_MARKERS = ("successful", "berhasil", "success", "selesai")
_FAILURE_MARKERS = ("failed", "gagal", "failure", "error")


def _service_of(content):
    """Service token of an episode: text before the first colon."""
    return str(content).split(":", 1)[0].strip().lower()


def reflect_episodes(episodes):
    """Turn episodes into higher-level inferences (deterministic).

    `episodes` is a list of dicts with keys `type`, `content`
    and `event_id`. Returns a list of inferences, each a dict
    with `key`, `value`, `confidence`, `source` ("inferred")
    and `evidence_event_ids`. Rules, all deterministic:

    - 2+ successful completions for one service ->
      ("reliable_service", service).
    - 2+ failures for one service -> ("repeated_failure", service).
    - a user message carrying a preference marker ("prefer",
      "always", "suka", ...) -> ("user_preference", phrase).

    Inferences with fewer than 2 evidence events are dropped.
    Nothing here writes to the profile: callers must route
    inferences through Proposals (see `reflect_and_propose`).
    """
    successes = {}
    failures = {}
    preferences = {}
    for ep in episodes or []:
        content = str(ep.get("content") or "")
        event_id = ep.get("event_id")
        lowered = content.lower()
        if ep.get("type") == "task_completed":
            service = _service_of(content)
            if not service:
                continue
            if any(w in lowered for w in _SUCCESS_MARKERS):
                successes.setdefault(service, []).append(event_id)
            elif any(w in lowered for w in _FAILURE_MARKERS):
                failures.setdefault(service, []).append(event_id)
        elif ep.get("type") == "user_message" and any(w in lowered for w in _PREFERENCE_MARKERS):
            phrase = " ".join(content.split())[:200]
            preferences.setdefault(phrase, []).append(event_id)
    inferences = []
    for service, events in sorted(successes.items()):
        filtered = [e for e in events if e]
        if len(filtered) >= REFLECTION_MIN_EVIDENCE:
            inferences.append(
                {
                    "key": "reliable_service",
                    "value": service,
                    "confidence": 0.9,
                    "source": "inferred",
                    "evidence_event_ids": sorted(set(filtered)),
                }
            )
    for service, events in sorted(failures.items()):
        filtered = [e for e in events if e]
        if len(filtered) >= REFLECTION_MIN_EVIDENCE:
            inferences.append(
                {
                    "key": "repeated_failure",
                    "value": service,
                    "confidence": 0.9,
                    "source": "inferred",
                    "evidence_event_ids": sorted(set(filtered)),
                }
            )
    for phrase, events in sorted(preferences.items()):
        filtered = [e for e in events if e]
        if len(filtered) >= REFLECTION_MIN_EVIDENCE:
            inferences.append(
                {
                    "key": "user_preference",
                    "value": phrase,
                    "confidence": 0.7,
                    "source": "inferred",
                    "evidence_event_ids": sorted(set(filtered)),
                }
            )
    return inferences


def reflect_and_propose(user_id, episodes, *, agent_id=None, propose=None, dsn=None):
    """Reflect episodes into profile Proposals (never active facts).

    Each inference from `reflect_episodes` is proposed through
    `profile.add_fact` with source "inferred", which stores it
    as status "proposed" until the user confirms it. `propose`
    is an injectable callable
    `propose(user_id, key, value, source, agent_id, event_id, dsn)`
    used by tests to avoid the database; it defaults to
    `profile.add_fact`. Returns the list of proposal dicts.
    """
    propose = propose or profile.add_fact
    proposals = []
    for inference in reflect_episodes(episodes):
        event_id = inference["evidence_event_ids"][0] if inference["evidence_event_ids"] else None
        proposals.append(
            propose(
                user_id,
                inference["key"],
                inference["value"],
                "inferred",
                agent_id=agent_id,
                event_id=event_id,
                dsn=dsn,
            )
        )
    return proposals


def _connect(dsn=None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


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
    # G7 narrative cap: the summary writer enforces the 200-line /
    # 24,000-byte limit before the text is stored.
    return truncate_narrative("\n".join(lines))


def consolidate_idle(user_id, older_than_days=30, agent_id=None, dsn=None, now=None, reflect=False):
    """Jalankan satu putaran konsolidasi untuk user ini.

    Kembalikan dict {ringkasan_dibuat, prosedur_dipromosikan, tugas}.
    Episode yang sudah masuk ringkasan tidak diringkas ulang (dicek
    lewat `kejadian_ids` yang sudah tersimpan). Dengan
    `reflect=True`, episode juga direfleksikan menjadi Usulan
    profil (lihat `reflect_and_propose`); hasilnya dilaporkan
    sebagai `proposals_created`.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=older_than_days)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT task_id FROM episodic WHERE user_id = %s "
            "AND created_at < %s AND task_id IS NOT NULL",
            (user_id, cutoff),
        )
        task_ids = [r["task_id"] for r in cur.fetchall()]
        cur.execute(
            "SELECT event_ids FROM summaries WHERE user_id = %s",
            (user_id,),
        )
        already = set()
        for r in cur.fetchall():
            already.update(str(k) for k in (r["event_ids"] or []))
    summaries_created = 0
    procedures_promoted = 0
    proposals_created = 0
    processed_tasks = []
    for task_id in task_ids:
        with _connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, type, content, event_id, created_at FROM episodic "
                "WHERE user_id = %s AND task_id = %s ORDER BY created_at ASC",
                (user_id, task_id),
            )
            rows = cur.fetchall()
        episodes = [
            {
                "id": str(r["id"]),
                "type": r["type"],
                "content": r["content"],
                "event_id": str(r["event_id"]) if r["event_id"] else None,
                "created_at": r["created_at"],
            }
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
        summaries_created += 1
        processed_tasks.append(str(task_id))
        # Pola berhasil -> prosedural: episode tugas_selesai yang
        # isinya menandai berhasil.
        completed_episodes = [e for e in new if e["type"] == "task_completed"]
        if completed_episodes and any(
            w in e["content"].lower()
            for e in completed_episodes
            for w in ("successful", "berhasil")
        ):
            service = completed_episodes[0]["content"].split(":", 1)[0].strip() or "tasks"
            step = [e["content"] for e in new if e["type"] != "task_completed"] or [
                e["content"] for e in completed_episodes
            ]
            procedural.record_successful_task(
                user_id,
                service,
                step,
                agent_id=agent_id,
                source_event_id=event_ids[0] if event_ids else None,
                dsn=dsn,
            )
            procedures_promoted += 1
        if reflect:
            proposals_created += len(reflect_and_propose(user_id, new, agent_id=agent_id, dsn=dsn))
    return {
        "summaries_created": summaries_created,
        "procedures_promoted": procedures_promoted,
        "proposals_created": proposals_created,
        "tasks": processed_tasks,
    }


def list_summaries(user_id, dsn=None):
    """Daftar ringkasan milik user, terbaru dulu."""
    with _connect(dsn) as conn, conn.cursor() as cur:
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
                "summary": r["summary"],
                "event_ids": [str(k) for k in (r["event_ids"] or [])],
                "created_at": r["created_at"],
            }
            for r in cur.fetchall()
        ]
