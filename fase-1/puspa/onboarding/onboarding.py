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
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from ..memory import episodic
from ..memory.schema import resolve_dsn
from ..persona import choose_style

TARGET_MENIT = 5

TEMPLATE_ALUR_BAWAAN = (
    "Tagihan bulanan",
    "Pantau harga barang",
    "Rekap tagihan minggu ini",
)

JENIS_TUGAS_PERTAMA = ("bandingkan_harga", "cek_tagihan")


class StubNotifier:
    """Pemberi notif pengganti sampai Web Push Fase 2 ada.

    `test_send` mencatat percobaan dan mengembalikan terkirim=False
    dengan alasan — TIDAK PERNAH pura-pura terkirim.
    """

    def __init__(self):
        self.percobaan = []

    def test_send(self, user_id):
        """Kembalikan (terkirim, detail)."""
        self.percobaan.append({"user_id": str(user_id), "waktu": datetime.now(timezone.utc)})
        return False, "Web Push belum ada (Fase 2); uji kirim dicatat sebagai percobaan"


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


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
        "langkah_saat_ini": row["langkah_saat_ini"],
        "nama": row["nama"],
        "gaya": row["gaya"],
        "notif_uji": row["notif_uji"],
        "konektor": row["konektor"],
        "tugas_pertama": row["tugas_pertama"],
        "hasil_pertama_terbukti": row["hasil_pertama_terbukti"],
        "dimulai": row["dimulai"],
        "selesai": row["selesai"],
        "durasi_detik": float(row["durasi_detik"]) if row["durasi_detik"] is not None else None,
    }


def get_status(user_id, dsn=None):
    """Status onboarding user; None kalau belum mulai."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM onboarding WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    return _row(row) if row else None


def complete_step1(user_id, nama, gaya, contoh_pesan=None, dsn=None):
    """Langkah 1: nama + gaya bicara (+ opsional contoh pesan)."""
    if not nama or not isinstance(nama, str):
        raise ValueError("nama harus string tak kosong")
    fact = choose_style(user_id, gaya, dsn=dsn)
    if contoh_pesan:
        from ..persona import add_style_samples

        add_style_samples(user_id, contoh_pesan, dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE onboarding SET nama = %s, gaya = %s, langkah_saat_ini = 1 "
                "WHERE user_id = %s RETURNING *",
                (nama.strip(), gaya, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"onboarding belum dimulai: {user_id}")
        conn.commit()
    status = _row(row)
    status["fakta_gaya_id"] = fact["id"]
    return status


def complete_step2(user_id, notifier=None, dsn=None):
    """Langkah 2: izinkan notif + uji kirim (dicatat apa adanya)."""
    notifier = notifier or StubNotifier()
    terkirim, detail = notifier.test_send(user_id)
    hasil = f"terkirim={terkirim}; {detail}"
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE onboarding SET notif_uji = %s, langkah_saat_ini = 2 "
                "WHERE user_id = %s RETURNING *",
                (hasil, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"onboarding belum dimulai: {user_id}")
        conn.commit()
    status = _row(row)
    status["notif_terkirim"] = terkirim
    return status


def complete_step3(user_id, konektor="nanti", dsn=None):
    """Langkah 3: sambungkan konektor (boleh 'nanti')."""
    if konektor not in ("nanti", "gmail", "drive", "calendar", "semua"):
        raise ValueError(f"konektor tidak dikenal: {konektor!r}")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE onboarding SET konektor = %s, langkah_saat_ini = 3 "
                "WHERE user_id = %s RETURNING *",
                (konektor, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"onboarding belum dimulai: {user_id}")
        conn.commit()
    return _row(row)


def run_first_task(user_id, jenis, target, dsn=None, ledger=None, agen_id=None):
    """Langkah 4: tugas pertama terbimbing — STUB TERUKUR.

    Memanggil jalur nyata sebisanya: validasi, tulis Buku Kerja
    (tugas_dibuat + tugas_selesai), tulis episodik, ukur waktu.
    Hasilnya SELALU `terbukti=False` dengan alasan: wedge-nya Fase 3.
    Tidak ada harga/tagihan yang dikarang.
    """
    if jenis not in JENIS_TUGAS_PERTAMA:
        raise ValueError(f"jenis tugas pertama tidak dikenal: {jenis!r}")
    if not target or not isinstance(target, str):
        raise ValueError("target harus string tak kosong")
    mulai = time.monotonic()
    import uuid

    tugas_id = str(uuid.uuid4())
    kejadian_ids = []
    if ledger is not None and agen_id is not None:
        for jenis_kejadian in ("tugas_dibuat", "tugas_selesai"):
            ev = ledger.append_event(
                tugas_id=tugas_id, agen_id=agen_id, jenis=jenis_kejadian,
                isi={"tugas": jenis, "target": target, "stub": True},
            )
            kejadian_ids.append(str(ev["id"]))
    episodic.record_episode(
        user_id,
        f"tugas pertama ({jenis}): {target} — stub, menunggu wedge Fase 3",
        "tugas_selesai", agen_id=agen_id, tugas_id=tugas_id,
        kejadian_id=kejadian_ids[-1] if kejadian_ids else None, dsn=dsn,
    )
    durasi = time.monotonic() - mulai
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE onboarding SET tugas_pertama = %s, "
                "hasil_pertama_terbukti = false, langkah_saat_ini = 4, "
                "selesai = now(), durasi_detik = "
                "EXTRACT(EPOCH FROM (now() - dimulai)) "
                "WHERE user_id = %s RETURNING *",
                (f"{jenis}:{target}", user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"onboarding belum dimulai: {user_id}")
        conn.commit()
    status = _row(row)
    return {
        "status": status,
        "tugas_id": tugas_id,
        "jenis": jenis,
        "target": target,
        "terbukti": False,
        "alasan": (
            "Wedge bandingkan harga / autopilot tagihan dibangun Fase 3; "
            "tugas pertama di Fase 1 adalah stub terukur (jalur nyata: "
            "Buku Kerja + episodik + ukur waktu)."
        ),
        "durasi_detik_tugas": durasi,
        "kejadian_ids": kejadian_ids,
        "templat_alur_ditawarkan": list(TEMPLATE_ALUR_BAWAAN),
    }


def measure_onboarding_seconds(status):
    """Durasi onboarding (detik) dari status; None kalau belum selesai."""
    if status is None or status["durasi_detik"] is None:
        return None
    return status["durasi_detik"]


def within_target(status):
    """True kalau onboarding selesai dalam target 5 menit."""
    durasi = measure_onboarding_seconds(status)
    return durasi is not None and durasi <= TARGET_MENIT * 60
