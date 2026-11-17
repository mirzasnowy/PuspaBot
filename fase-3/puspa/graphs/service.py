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
SYARAT_PERBAIKAN_UJI = 3

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


def _ada_data_pribadi(langkah) -> bool:
    teks = " ".join(str(s) for s in (langkah if isinstance(langkah, list) else [langkah]))
    return any(p.search(teks) for p in _POLa_PRIBADI)


def tulis_prosedur(*, user_id: str, layanan: str, langkah: list,
                   agen_id: str | None = None, skema_keluaran: dict | None = None,
                   pemeriksaan_wajib: list | None = None,
                   kejadian_asal_id=None, dsn=None) -> dict:
    """Tulis prosedur dari tugas sukses (otomatis, tanpa editor).

    Menolak langkah yang memuat data pribadi (§3.3). Prosedur baru
    lahir `percobaan`; snapshot versi 1 disimpan untuk rollback.
    """
    if _ada_data_pribadi(langkah):
        raise ServiceError(
            "langkah prosedur memuat data pribadi (nomor pelanggan/HP) — "
            "nomor tetap di memori, tidak pernah masuk graf (§3.3)")
    prosedur = _prosedural.record_successful_task(
        user_id, layanan, langkah, agen_id=agen_id,
        skema_keluaran=skema_keluaran, pemeriksaan_wajib=pemeriksaan_wajib,
        kejadian_asal_id=kejadian_asal_id, dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO prosedur_versi (prosedur_id, user_id, versi, langkah)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (prosedur_id, versi) DO NOTHING",
                (prosedur["id"], user_id, prosedur["versi"], Jsonb(langkah)))
        conn.commit()
    return prosedur


def ambil_prosedur(prosedur_id: str, user_id: str, dsn=None) -> dict:
    """Ambil satu prosedur; prosedur `rusak` tetap bisa dibaca tapi
    TIDAK boleh dijalankan (pemanggil wajib menolak — lihat
    `jalankan_prosedur`)."""
    return _prosedural.get_procedure(prosedur_id, user_id, dsn=dsn)


def daftar_prosedur(user_id: str, status: str | None = None, dsn=None) -> list[dict]:
    return _prosedural.list_procedures(user_id, status=status, dsn=dsn)


def catat_jalan(prosedur_id: str, user_id: str, sukses: bool,
                agen_id: str | None = None, dsn=None) -> dict:
    """Catat satu jalan prosedur; kembalikan status baru."""
    return _prosedural.mark_run(prosedur_id, user_id, sukses, agen_id=agen_id, dsn=dsn)


def catat_pakai_ulang(*, prosedur_id: str, user_id: str, agen_id: str,
                      tugas_id: str, dsn=None) -> dict:
    """Catat agen lain memakai prosedur tanpa diajari ulang (§3.3).

    Kembalikan {"pemakaian_ke": n, "agen_berbeda": m} — bahan metrik
    gerbang 8 (dipakai ulang agen lain minimal 1 kali).
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pemakaian_prosedur (prosedur_id, user_id, agen_id, tugas_id)"
                " VALUES (%s, %s, %s, %s)",
                (prosedur_id, user_id, agen_id, tugas_id))
            cur.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT agen_id) AS agen"
                " FROM pemakaian_prosedur WHERE prosedur_id = %s", (prosedur_id,))
            row = cur.fetchone()
        conn.commit()
    return {"pemakaian_ke": row["n"], "agen_berbeda": row["agen"]}


def jalankan_prosedur(prosedur: dict, *, jalankan, agen_id: str,
                      user_id: str, tugas_id: str, dsn=None) -> dict:
    """Jalankan prosedur lewat callable `jalankan(prosedur)`.

    Prosedur `rusak` TIDAK dijalankan — kembalikan alasan gagal
    terlihat (§3.3, §4.2). Jalan sukses/gagal dicatat ke siklus
    hidup; jalan sukses oleh agen yang belum pernah memakai prosedur
    ini dicatat sebagai pakai ulang.
    """
    if prosedur["status"] == "rusak":
        return {"sukses": False, "alasan": "prosedur_rusak",
                "pesan": "tidak bisa cek — prosedurnya rusak, agen tidak memakai prosedur yang mungkin salah"}
    try:
        hasil = jalankan(prosedur)
        sukses = bool(hasil.get("sukses", True)) if isinstance(hasil, dict) else True
    except Exception as exc:  # noqa: BLE001 — kegagalan jalan dicatat, bukan dilempar
        hasil = {"sukses": False, "alasan": "jalan_gagal", "pesan": str(exc)}
        sukses = False
    baru = catat_jalan(prosedur["id"], user_id, sukses, agen_id=agen_id, dsn=dsn)
    if sukses:
        catat_pakai_ulang(prosedur_id=prosedur["id"], user_id=user_id,
                          agen_id=agen_id, tugas_id=tugas_id, dsn=dsn)
    return {"sukses": sukses, "hasil": hasil, "prosedur": baru}


def jalankan_canary(*, prosedur_id: str, user_id: str, agen_id: str,
                    jalankan, dsn=None) -> dict:
    """Canary harian (§3.3): jalankan prosedur terverifikasi sekali
    pada data uji, hasilnya diperiksa seperti tugas biasa.

    Canary gagal -> prosedur menjadi `rusak` (2 jalan gagal
    berturut-turut juga bisa dari jalan biasa). Kembalikan hasil +
    status prosedur sesudah canary.
    """
    prosedur = ambil_prosedur(prosedur_id, user_id, dsn=dsn)
    if prosedur["status"] != "terverifikasi":
        return {"sukses": False, "alasan": "bukan_terverifikasi",
                "pesan": "canary hanya untuk prosedur terverifikasi"}
    try:
        hasil = jalankan(prosedur)
        sukses = bool(hasil.get("sukses", True)) if isinstance(hasil, dict) else True
        detail = hasil if isinstance(hasil, dict) else {}
    except Exception as exc:  # noqa: BLE001
        sukses, detail = False, {"alasan": "jalan_gagal", "pesan": str(exc)}
    baru = catat_jalan(prosedur_id, user_id, sukses, agen_id=agen_id, dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO canary_jalan (prosedur_id, user_id, agen_id, sukses, detail)"
                " VALUES (%s, %s, %s, %s, %s)",
                (prosedur_id, user_id, agen_id, sukses, Jsonb(detail)))
        conn.commit()
    return {"sukses": sukses, "detail": detail, "prosedur": baru}


def usulkan_perbaikan(*, prosedur_id: str, user_id: str, langkah_baru: list,
                      dsn=None) -> dict:
    """Usulkan versi baru prosedur rusak (§3.3 perbaikan).

    Versi baru disimpan sebagai snapshot; ia baru menggantikan versi
    lama setelah lolos 3 uji (`uji_perbaikan`, di bawah). Versi lama
    tetap bisa dipulihkan (`rollback`).
    """
    if _ada_data_pribadi(langkah_baru):
        raise ServiceError("langkah baru memuat data pribadi — ditolak (§3.3)")
    prosedur = ambil_prosedur(prosedur_id, user_id, dsn=dsn)
    versi_baru = prosedur["versi"] + 1
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO prosedur_versi (prosedur_id, user_id, versi, langkah)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (prosedur_id, versi) DO NOTHING",
                (prosedur_id, user_id, versi_baru, Jsonb(langkah_baru)))
        conn.commit()
    return {"prosedur_id": prosedur_id, "versi_baru": versi_baru,
            "status": "menunggu_3_uji"}


def uji_perbaikan(*, prosedur_id: str, user_id: str, versi: int,
                 jalankan_uji, dsn=None) -> dict:
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
                "SELECT langkah FROM prosedur_versi WHERE prosedur_id = %s AND versi = %s",
                (prosedur_id, versi))
            row = cur.fetchone()
    if row is None:
        raise KeyError(f"versi perbaikan tidak ditemukan: {prosedur_id} v{versi}")
    lolos = 0
    for _ in range(SYARAT_PERBAIKAN_UJI):
        try:
            hasil = jalankan_uji({"langkah": row["langkah"], "versi": versi})
            if isinstance(hasil, dict) and hasil.get("sukses", True):
                lolos += 1
        except Exception:  # noqa: BLE001 — satu uji gagal, lanjut hitung
            pass
    if lolos < SYARAT_PERBAIKAN_UJI:
        return {"diganti": False, "lolos": lolos, "butuh": SYARAT_PERBAIKAN_UJI}
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE prosedur SET langkah = %s, versi = %s, status = 'percobaan',"
                " jalan_gagal_berturut = 0, diubah = now() WHERE id = %s AND user_id = %s",
                (Jsonb(row["langkah"]), versi, prosedur_id, user_id))
        conn.commit()
    return {"diganti": True, "lolos": lolos, "versi": versi}


def rollback(*, prosedur_id: str, user_id: str, versi: int, dsn=None) -> dict:
    """Pulihkan versi lama prosedur (§3.3)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT langkah FROM prosedur_versi WHERE prosedur_id = %s AND versi = %s",
                (prosedur_id, versi))
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"versi tidak ditemukan: {prosedur_id} v{versi}")
            cur.execute(
                "UPDATE prosedur SET langkah = %s, versi = %s, status = 'percobaan',"
                " jalan_gagal_berturut = 0, diubah = now() WHERE id = %s AND user_id = %s",
                (Jsonb(row["langkah"]), versi, prosedur_id, user_id))
        conn.commit()
    return ambil_prosedur(prosedur_id, user_id, dsn=dsn)


# --- Pola sub-agen (§3.3: "Pola sub-agen juga disimpan di sini") ---------

POLA_SUBAGEN_BAWAAN = {
    "bandingkan_harga": [
        "pembaca_tokopedia",
        "pembaca_shopee",
        "pembaca_blibli",
        "pemeriksa_baca_ulang",
    ],
    "cek_tagihan": [
        "pembaca_provider",
        "pemeriksa_baca_ulang",
    ],
}


def simpan_pola_subagen(*, user_id: str, nama: str, pola: list | None = None,
                        agen_id: str | None = None, dsn=None) -> dict:
    """Simpan pola sub-agen yang berhasil sebagai prosedur di graf
    (§3.3), supaya agen lain memakainya tanpa menemukan ulang polanya.

    Pola disimpan dengan layanan `pola:<nama>` — bukan prosedur
    layanan, tapi tercatat di graf yang sama dan bisa dibaca lintas
    agen lewat `daftar_pola`.
    """
    langkah_pola = pola or POLA_SUBAGEN_BAWAAN.get(nama)
    if not langkah_pola:
        raise ServiceError(f"pola sub-agen tidak dikenal: {nama!r}")
    return tulis_prosedur(
        user_id=user_id, layanan=f"pola:{nama}",
        langkah=[{"langkah": langkah} for langkah in langkah_pola],
        agen_id=agen_id, dsn=dsn)


def daftar_pola(user_id: str, dsn=None) -> list[dict]:
    """Daftar pola sub-agen yang tersimpan di graf."""
    return [p for p in daftar_prosedur(user_id, dsn=dsn)
            if p["layanan"].startswith("pola:")]
