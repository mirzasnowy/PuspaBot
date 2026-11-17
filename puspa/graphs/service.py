"""Graf Alur Layanan (§3.3 revisi 7).

Prosedur = templat berparameter tanpa data pribadi. Nomor pelanggan
tetap di memori user dan TIDAK PERNAH masuk graf (ditegakkan di sini:
`langkah` yang memuat pola nomor pelanggan ditolak).

Siklus hidup memakai tabel `prosedur` Fase 1
(`puspa.memory.procedural`, ditulis ulang dari pola Graphiti):
percobaan -> terverifikasi (3 jalan sukses, min 2 agen) -> rusak
(2 gagal berturut-turut atau canary gagal). Modul ini menambah yang
belum ada di Fase 1: canary harian, pencatatan pakai ulang lintas
agen, versi + rollback, dan perbaikan (versi baru lolos 3 uji sebelum
menggantikan).

Pola yang diadaptasi (ditulis ulang): RAGFlow (Apache-2.0) — mesin
yang menyimpan langkah kerja sebagai data yang bisa dipakai ulang,
bukan sebagai kode yang ditulis ulang tiap kali. Di Puspa yang
disimpan adalah prosedur layanan (langkah + skema keluaran +
pemeriksaan wajib), bukan pipeline dokumen.
"""

from __future__ import annotations

import os
import re

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from puspa.memory import procedural as _prosedural

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
SYARAT_PERBAIKAN_TEST = 3

# Pola yang menandakan data pribadi terselip di langkah prosedur:
# nomor pelanggan PLN (12 digit), nomor HP Indonesia, nomor meter.
_POLa_PRIBADI = (
    re.compile(r"\b\d{12}\b"),          # ID pelanggan PLN 12 digit
    re.compile(r"\b08\d{8,11}\b"),      # nomor HP Indonesia
)


class ServiceError(Exception):
    """Graf alur layanan menolak operasi."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def _ada_data_pribadi(step) -> bool:
    text = " ".join(str(s) for s in (step if isinstance(step, list) else [step]))
    return any(p.search(text) for p in _POLa_PRIBADI)


def tulis_prosedur(*, user_id: str, service: str, step: list,
                   agent_id: str | None = None, output_schema: dict | None = None,
                   required_checks: list | None = None,
                   source_event_id=None, dsn=None) -> dict:
    """Tulis prosedur dari tugas sukses (otomatis, tanpa editor).

    Menolak langkah yang memuat data pribadi (§3.3). Prosedur baru
    lahir `percobaan`; snapshot versi 1 disimpan untuk rollback.
    """
    if _ada_data_pribadi(step):
        raise ServiceError(
            "langkah prosedur memuat data pribadi (nomor pelanggan/HP) — "
            "nomor tetap di memori, tidak pernah masuk graf (§3.3)")
    prosedur = _prosedural.record_successful_task(
        user_id, service, step, agent_id=agent_id,
        output_schema=output_schema, required_checks=required_checks,
        source_event_id=source_event_id, dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO procedure_versions (prosedur_id, user_id, versi, step)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (prosedur_id, versi) DO NOTHING",
                (prosedur["id"], user_id, prosedur["versi"], Jsonb(step)))
        conn.commit()
    return prosedur


def take_prosedur(prosedur_id: str, user_id: str, dsn=None) -> dict:
    """Ambil satu prosedur; prosedur `rusak` tetap bisa dibaca tapi
    TIDAK boleh dijalankan (pemanggil wajib menolak — lihat
    `jalankan_prosedur`)."""
    return _prosedural.get_procedure(prosedur_id, user_id, dsn=dsn)


def items_prosedur(user_id: str, status: str | None = None, dsn=None) -> list[dict]:
    return _prosedural.list_procedures(user_id, status=status, dsn=dsn)


def record_jalan(prosedur_id: str, user_id: str, success: bool,
                agent_id: str | None = None, dsn=None) -> dict:
    """Catat satu jalan prosedur; kembalikan status baru."""
    return _prosedural.mark_run(prosedur_id, user_id, success, agent_id=agent_id, dsn=dsn)


def record_use_ulang(*, prosedur_id: str, user_id: str, agent_id: str,
                      task_id: str, dsn=None) -> dict:
    """Catat agen lain memakai prosedur tanpa diajari ulang (§3.3).

    Kembalikan {"pemakaian_ke": n, "agen_berbeda": m} — bahan metrik
    gerbang 8 (dipakai ulang agen lain minimal 1 kali).
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO procedure_uses (prosedur_id, user_id, agent_id, task_id)"
                " VALUES (%s, %s, %s, %s)",
                (prosedur_id, user_id, agent_id, task_id))
            cur.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT agent_id) AS agent"
                " FROM procedure_uses WHERE prosedur_id = %s", (prosedur_id,))
            row = cur.fetchone()
        conn.commit()
    return {"pemakaian_to": row["n"], "agent_berbeda": row["agents"]}


def jalankan_prosedur(prosedur: dict, *, jalankan, agent_id: str,
                      user_id: str, task_id: str, dsn=None) -> dict:
    """Jalankan prosedur lewat callable `jalankan(prosedur)`.

    Prosedur `rusak` TIDAK dijalankan — kembalikan alasan gagal
    terlihat (§3.3, §4.2). Jalan sukses/gagal dicatat ke siklus
    hidup; jalan sukses oleh agen yang belum pernah memakai prosedur
    ini dicatat sebagai pakai ulang.
    """
    if prosedur["status"] == "broken":
        return {"success": False, "reason": "prosedur_broken",
                "message": "tidak bisa cek — prosedurnya rusak, agen tidak memakai prosedur yang mungkin salah"}
    try:
        result = jalankan(prosedur)
        success = bool(result.get("success", True)) if isinstance(result, dict) else True
    except Exception as exc:  # noqa: BLE001 — kegagalan jalan dicatat, bukan dilempar
        result = {"success": False, "reason": "failed_runs", "message": str(exc)}
        success = False
    new = record_jalan(prosedur["id"], user_id, success, agent_id=agent_id, dsn=dsn)
    if success:
        record_use_ulang(prosedur_id=prosedur["id"], user_id=user_id,
                          agent_id=agent_id, task_id=task_id, dsn=dsn)
    return {"success": success, "result": result, "procedures": new}


def jalankan_canary(*, prosedur_id: str, user_id: str, agent_id: str,
                    jalankan, dsn=None) -> dict:
    """Canary harian (§3.3): jalankan prosedur terverifikasi sekali
    pada data uji, hasilnya diperiksa seperti tugas biasa.

    Canary gagal -> prosedur menjadi `rusak` (2 jalan gagal
    berturut-turut juga bisa dari jalan biasa). Kembalikan hasil +
    status prosedur sesudah canary.
    """
    prosedur = take_prosedur(prosedur_id, user_id, dsn=dsn)
    if prosedur["status"] != "verified":
        return {"success": False, "reason": "bukan_verified",
                "message": "canary hanya untuk prosedur terverifikasi"}
    try:
        result = jalankan(prosedur)
        success = bool(result.get("success", True)) if isinstance(result, dict) else True
        detail = result if isinstance(result, dict) else {}
    except Exception as exc:  # noqa: BLE001
        success, detail = False, {"reason": "failed_runs", "message": str(exc)}
    new = record_jalan(prosedur_id, user_id, success, agent_id=agent_id, dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO canary_runs (prosedur_id, user_id, agent_id, success, detail)"
                " VALUES (%s, %s, %s, %s, %s)",
                (prosedur_id, user_id, agent_id, success, Jsonb(detail)))
        conn.commit()
    return {"success": success, "detail": detail, "procedures": new}


def usulkan_perbaikan(*, prosedur_id: str, user_id: str, step_new: list,
                      dsn=None) -> dict:
    """Usulkan versi baru prosedur rusak (§3.3 perbaikan).

    Versi baru disimpan sebagai snapshot; ia baru menggantikan versi
    lama setelah lolos 3 uji (`uji_perbaikan`, di bawah). Versi lama
    tetap bisa dipulihkan (`rollback`).
    """
    if _ada_data_pribadi(step_new):
        raise ServiceError("langkah baru memuat data pribadi — ditolak (§3.3)")
    prosedur = take_prosedur(prosedur_id, user_id, dsn=dsn)
    versi_new = prosedur["versi"] + 1
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO procedure_versions (prosedur_id, user_id, versi, step)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (prosedur_id, versi) DO NOTHING",
                (prosedur_id, user_id, versi_new, Jsonb(step_new)))
        conn.commit()
    return {"prosedur_id": prosedur_id, "versi_new": versi_new,
            "status": "pending_3_test"}


def test_perbaikan(*, prosedur_id: str, user_id: str, versi: int,
                 jalankan_test, dsn=None) -> dict:
    """Uji versi perbaikan pada data uji (§3.3).

    3 uji lolos -> versi baru menggantikan versi lama (langkah +
    versi diperbarui, status kembali `percobaan` dengan hitungan
    jalan di-nol-kan lewat baris baru? Tidak: status dipulihkan
    ke `percobaan` dan jalan gagal berurutan di-nol-kan; jalan
    sukses lama dipertahankan sebagai riwayat). Di bawah 3 uji
    lolos -> versi baru belum menggantikan.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step FROM procedure_versions WHERE prosedur_id = %s AND versi = %s",
                (prosedur_id, versi))
            row = cur.fetchone()
    if row is None:
        raise KeyError(f"versi perbaikan tidak ditemukan: {prosedur_id} v{versi}")
    passed = 0
    for _ in range(SYARAT_PERBAIKAN_TEST):
        try:
            result = jalankan_test({"step": row["step"], "versi": versi})
            if isinstance(result, dict) and result.get("success", True):
                passed += 1
        except Exception:  # noqa: BLE001 — satu uji gagal, lanjut hitung
            pass
    if passed < SYARAT_PERBAIKAN_TEST:
        return {"replaced": False, "passed": passed, "need": SYARAT_PERBAIKAN_TEST}
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE procedures SET step = %s, versi = %s, status = 'trial',"
                " consecutive_failed_runs = 0, updated_at = now() WHERE id = %s AND user_id = %s",
                (Jsonb(row["step"]), versi, prosedur_id, user_id))
        conn.commit()
    return {"replaced": True, "passed": passed, "versi": versi}


def rollback(*, prosedur_id: str, user_id: str, versi: int, dsn=None) -> dict:
    """Pulihkan versi lama prosedur (§3.3)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step FROM procedure_versions WHERE prosedur_id = %s AND versi = %s",
                (prosedur_id, versi))
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"versi tidak ditemukan: {prosedur_id} v{versi}")
            cur.execute(
                "UPDATE procedures SET step = %s, versi = %s, status = 'trial',"
                " consecutive_failed_runs = 0, updated_at = now() WHERE id = %s AND user_id = %s",
                (Jsonb(row["step"]), versi, prosedur_id, user_id))
        conn.commit()
    return take_prosedur(prosedur_id, user_id, dsn=dsn)


# --- Pola sub-agen (§3.3: "Pola sub-agen juga disimpan di sini") ---------

PATTERN_SUBAGEN_BAWAAN = {
    "compare_prices": [
        "pembaca_tokopedia",
        "pembaca_shopee",
        "pembaca_blibli",
        "pemeriksa_read_ulang",
    ],
    "check_bill": [
        "pembaca_provider",
        "pemeriksa_read_ulang",
    ],
}


def save_pattern_subagen(*, user_id: str, name: str, pattern: list | None = None,
                        agent_id: str | None = None, dsn=None) -> dict:
    """Simpan pola sub-agen yang berhasil sebagai prosedur di graf
    (§3.3), supaya agen lain memakainya tanpa menemukan ulang polanya.

    Pola disimpan dengan layanan `pola:<nama>` — bukan prosedur
    layanan, tapi tercatat di graf yang sama dan bisa dibaca lintas
    agen lewat `daftar_pola`.
    """
    step_pattern = pattern or PATTERN_SUBAGEN_BAWAAN.get(name)
    if not step_pattern:
        raise ServiceError(f"pola sub-agen tidak dikenal: {name!r}")
    return tulis_prosedur(
        user_id=user_id, service=f"pola:{name}",
        step=[{"step": step} for step in step_pattern],
        agent_id=agent_id, dsn=dsn)


def items_pattern(user_id: str, dsn=None) -> list[dict]:
    """Daftar pola sub-agen yang tersimpan di graf."""
    return [p for p in items_prosedur(user_id, dsn=dsn)
            if p["service"].startswith("pola:")]
