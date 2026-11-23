"""Onboarding 5 menit — 4 langkah (definisi 2.16, C122, C123, C125).

Target: 5 menit dari daftar sampai hasil berbukti pertama.
Langkah:
1. Nama + gaya bicara (pilih 1 dari 3 contoh gaya + opsional contoh pesan).
2. Izinkan notif, lalu uji kirim.
3. Sambungkan konektor (boleh nanti).
4. Tugas pertama terbimbing: bandingkan harga atau cek tagihan PLN.

BATAS YANG JUJUR (dicatat di STATUS_FASE1.md):
- Uji kirim notif: Web Push (VAPID) dibangun Fase 2 (2.14). Di sini
  langkah 2 memakai antarmuka `Notifier` — `StubNotifier` mencatat
  percobaan dan melaporkan TIDAK TERKIRIM (bukan pura-pura terkirim).
  Langkah tetap bisa diselesaikan dengan catatan itu (gagal terlihat).
- Tugas pertama: wedge bandingkan harga + autopilot tagihan dibangun
  Fase 3. Di sini tugas pertama adalah STUB TERUKUR: ia memanggil
  jalur nyata sebisanya (validasi input, tulis Buku Kerja, tulis
  episodik, ukur waktu) dan mengembalikan `terbukti=False` dengan
  alasan yang jelas — bukan hasil harga/tagihan palsu.
- Sesudah tugas pertama selesai, 3 templat alur bawaan ditawarkan
  (C125): Tagihan bulanan, Pantau harga barang, Rekap tagihan minggu
  ini — sebagai daftar (alur kustom dibangun Fase 4).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from puspa.db import connect as _db_connect
from puspa.db import dict_row

from ..memory import episodic
from ..memory.schema import resolve_dsn
from ..persona import choose_style

TARGET_MINUTES = 5

BUILTIN_WORKFLOW_TEMPLATES = (
    "Tagihan bulanan",
    "Pantau harga barang",
    "Rekap tagihan minggu ini",
)

FIRST_TASK_TYPES = ("compare_prices", "check_bill")


class StubNotifier:
    """Pemberi notif pengganti sampai Web Push Fase 2 ada.

    `test_send` mencatat percobaan dan mengembalikan terkirim=False
    dengan alasan — TIDAK PERNAH pura-pura terkirim.
    """

    def __init__(self):
        self.trial = []

    def test_send(self, user_id):
        """Kembalikan (terkirim, detail)."""
        self.trial.append({"user_id": str(user_id), "created_at": datetime.now(UTC)})
        return False, "Web Push belum ada (Fase 2); uji kirim dicatat sebagai percobaan"


def _connect(dsn=None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


def start_onboarding(user_id, dsn=None):
    """Mulai onboarding; kembalikan status (langkah 0)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO onboarding (user_id) VALUES (%s) "
                "ON CONFLICT (user_id) DO NOTHING RETURNING *",
                (user_id,),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT * FROM onboarding WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
        conn.commit()
    return _row(row)


def _row(row):
    return {
        "user_id": str(row["user_id"]),
        "current_step": row["current_step"],
        "name": row["name"],
        "style": row["style"],
        "test_notification": row["test_notification"],
        "connector": row["connector"],
        "first_task": row["first_task"],
        "first_result_verified": row["first_result_verified"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "duration_seconds": float(row["duration_seconds"])
        if row["duration_seconds"] is not None
        else None,
    }


def get_status(user_id, dsn=None):
    """Status onboarding user; None kalau belum mulai."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM onboarding WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    return _row(row) if row else None


def complete_step1(user_id, name, style, example_message=None, dsn=None):
    """Langkah 1: nama + gaya bicara (+ opsional contoh pesan)."""
    if not name or not isinstance(name, str):
        raise ValueError("nama harus string tak kosong")
    fact = choose_style(user_id, style, dsn=dsn)
    if example_message:
        from ..persona import add_style_samples

        add_style_samples(user_id, example_message, dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE onboarding SET name = %s, style = %s, current_step = 1 "
                "WHERE user_id = %s RETURNING *",
                (name.strip(), style, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"onboarding belum dimulai: {user_id}")
        conn.commit()
    status = _row(row)
    status["style_fact_id"] = fact["id"]
    return status


def complete_step2(user_id, notifier=None, dsn=None):
    """Langkah 2: izinkan notif + uji kirim (dicatat apa adanya)."""
    notifier = notifier or StubNotifier()
    sent, detail = notifier.test_send(user_id)
    result = f"terkirim={sent}; {detail}"
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE onboarding SET test_notification = %s, current_step = 2 "
                "WHERE user_id = %s RETURNING *",
                (result, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"onboarding belum dimulai: {user_id}")
        conn.commit()
    status = _row(row)
    status["notification_sent"] = sent
    return status


def complete_step3(user_id, connector="nanti", dsn=None):
    """Langkah 3: sambungkan konektor (boleh 'nanti')."""
    if connector not in ("nanti", "gmail", "drive", "calendar", "all"):
        raise ValueError(f"konektor tidak dikenal: {connector!r}")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE onboarding SET connector = %s, current_step = 3 "
                "WHERE user_id = %s RETURNING *",
                (connector, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"onboarding belum dimulai: {user_id}")
        conn.commit()
    return _row(row)


def run_first_task(user_id, event_type, target, dsn=None, ledger=None, agent_id=None):
    """Langkah 4: tugas pertama terbimbing — STUB TERUKUR.

    Memanggil jalur nyata sebisanya: validasi, tulis Buku Kerja
    (tugas_dibuat + tugas_selesai), tulis episodik, ukur waktu.
    Hasilnya SELALU `terbukti=False` dengan alasan: wedge-nya Fase 3.
    Tidak ada harga/tagihan yang dikarang.
    """
    if event_type not in FIRST_TASK_TYPES:
        raise ValueError(f"jenis tugas pertama tidak dikenal: {event_type!r}")
    if not target or not isinstance(target, str):
        raise ValueError("target harus string tak kosong")
    start = time.monotonic()
    import uuid

    task_id = str(uuid.uuid4())
    event_ids = []
    if ledger is not None and agent_id is not None:
        for event_kind in ("task_created", "task_completed"):
            ev = ledger.append_event(
                task_id=task_id,
                agent_id=agent_id,
                event_type=event_kind,
                content={"tasks": event_type, "target": target, "stub": True},
            )
            event_ids.append(str(ev["id"]))
    episodic.record_episode(
        user_id,
        f"tugas pertama ({event_type}): {target} — stub, menunggu wedge Fase 3",
        "task_completed",
        agent_id=agent_id,
        task_id=task_id,
        event_id=event_ids[-1] if event_ids else None,
        dsn=dsn,
    )
    duration = time.monotonic() - start
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            # The duration is bound from Python: the Postgres form
            # EXTRACT(EPOCH FROM (now() - started_at)) has no
            # SQLite equivalent, and this is the only EXTRACT site
            # in the tree. The value is the same wall-clock span
            # the query measured (start was taken at the top of
            # this function, just before the work ran).
            cur.execute(
                "UPDATE onboarding SET first_task = %s, "
                "first_result_verified = false, current_step = 4, "
                "completed_at = now(), duration_seconds = %s "
                "WHERE user_id = %s RETURNING *",
                (f"{event_type}:{target}", duration, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"onboarding belum dimulai: {user_id}")
        conn.commit()
    status = _row(row)
    return {
        "status": status,
        "task_id": task_id,
        "type": event_type,
        "target": target,
        "verified": False,
        "reason": (
            "Wedge bandingkan harga / autopilot tagihan dibangun Fase 3; "
            "tugas pertama di Fase 1 adalah stub terukur (jalur nyata: "
            "Buku Kerja + episodik + ukur waktu)."
        ),
        "task_duration_seconds": duration,
        "event_ids": event_ids,
        "offered_workflow_templates": list(BUILTIN_WORKFLOW_TEMPLATES),
    }


def measure_onboarding_seconds(status):
    """Durasi onboarding (detik) dari status; None kalau belum selesai."""
    if status is None or status["duration_seconds"] is None:
        return None
    return status["duration_seconds"]


def within_target(status):
    """True kalau onboarding selesai dalam target 5 menit."""
    duration = measure_onboarding_seconds(status)
    return duration is not None and duration <= TARGET_MINUTES * 60
