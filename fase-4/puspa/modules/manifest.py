"""Manifes modul kustom (§2.13 revisi 7).

Modul = alur + tampilan + data + notif, ditulis sebagai SATU manifes
deklaratif. Manifes adalah DATA, bukan program: tidak ada HTML,
JavaScript, atau SQL dari bot.

Gerbang manifes (SEMUA oleh kode):
1. validasi skema,
2. pemindai komponen dan tipe yang diizinkan,
3. manifes izin turunan dari blok yang dipakai,
4. uji kering dengan data contoh,
5. batas ukuran.
Manifes yang gagal di salah satunya tidak bisa diaktifkan, dan
alasannya ditampilkan.
"""

from __future__ import annotations

import json
import os

import psycopg
from psycopg.rows import dict_row

from puspa.workflow.blocks import periksa_alur
from puspa.workflow.manifest import hitung_manifes, subset_izin
from puspa.workflow.triggers import validasi_pemicu

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

KOMPONEN_DIIZINKAN = ("kartu", "tabel", "daftar", "form", "angka",
                      "grafik_garis", "tombol_aksi")
TIPE_KOLOM = ("teks", "angka", "Rupiah", "tanggal", "pilihan")
MAKS_TAMPILAN = 3
MAKS_TABEL = 5
MAKS_KOLOM_PER_TABEL = 10
MAKS_UKURAN_MANIFES_BYTE = 65536
MAKS_MODUL_AKTIF = 3

# Pola yang menandakan kode (bukan data) di dalam manifes. Manifes yang
# memuatnya DITOLAK di gerbang 2 — bukan dibersihkan diam-diam.
POLA_KODE_DILARANG = (
    "<script", "</script", "<iframe", "javascript:", "onerror=",
    "onclick=", "SELECT ", "INSERT ", "UPDATE ", "DELETE ", "DROP ",
    "--", "/*",
)


class ModuleError(Exception):
    """Manifes modul tidak lolos gerbang, atau modul tidak ditemukan."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def _row(row: dict) -> dict:
    row = dict(row)
    for k in ("id", "user_id"):
        if k in row and row[k] is not None:
            row[k] = str(row[k])
    return row


# ---------------------------------------------------------------- gerbang

def gerbang_skema(manifes: dict) -> list[str]:
    """Gerbang 1: validasi skema."""
    masalah: list[str] = []
    if not isinstance(manifes, dict):
        return ["manifes harus objek"]
    if not isinstance(manifes.get("nama"), str) or not manifes["nama"].strip():
        masalah.append("manifes: 'nama' wajib teks tak kosong")
    tampilan = manifes.get("tampilan")
    if not isinstance(tampilan, list) or not tampilan:
        masalah.append("manifes: 'tampilan' wajib daftar tak kosong")
    tabel = manifes.get("tabel", [])
    if not isinstance(tabel, list):
        masalah.append("manifes: 'tabel' harus daftar")
    else:
        for t in tabel:
            if not isinstance(t, dict) or not t.get("nama"):
                masalah.append("manifes: tiap tabel wajib punya 'nama'")
                continue
            kolom = t.get("kolom")
            if not isinstance(kolom, list) or not kolom:
                masalah.append(f"tabel {t['nama']!r}: 'kolom' wajib daftar tak kosong")
                continue
            for k in kolom:
                if not isinstance(k, dict) or not k.get("nama") or not k.get("tipe"):
                    masalah.append(f"tabel {t['nama']!r}: tiap kolom wajib 'nama' + 'tipe'")
    alur = manifes.get("alur")
    if alur is not None:
        if not isinstance(alur, dict):
            masalah.append("manifes: 'alur' harus objek")
        else:
            masalah += validasi_pemicu(alur.get("pemicu", {}))
            masalah += periksa_alur(alur.get("langkah", []))
    notif = manifes.get("notif", [])
    if not isinstance(notif, list):
        masalah.append("manifes: 'notif' harus daftar")
    else:
        for n in notif:
            if not isinstance(n, dict) or not n.get("kondisi") or not n.get("pesan"):
                masalah.append("manifes: tiap aturan notif wajib 'kondisi' + 'pesan'")
    contoh = manifes.get("contoh_data", {})
    if not isinstance(contoh, dict):
        masalah.append("manifes: 'contoh_data' harus objek")
    return masalah


def gerbang_pemindai(manifes: dict) -> list[str]:
    """Gerbang 2: pemindai komponen dan tipe yang diizinkan + larangan kode."""
    masalah: list[str] = []
    for t in manifes.get("tampilan", []) or []:
        if not isinstance(t, dict):
            masalah.append("tampilan: tiap tampilan harus objek")
            continue
        if t.get("jenis") not in KOMPONEN_DIIZINKAN:
            masalah.append(
                f"tampilan: komponen {t.get('jenis')!r} tidak diizinkan "
                f"(yang boleh: {', '.join(KOMPONEN_DIIZINKAN)})")
    for t in manifes.get("tabel", []) or []:
        for k in (t.get("kolom") or []):
            if k.get("tipe") not in TIPE_KOLOM:
                masalah.append(
                    f"tabel {t.get('nama')!r}: tipe kolom {k.get('tipe')!r} "
                    f"tidak diizinkan (yang boleh: {', '.join(TIPE_KOLOM)})")
            if k.get("tipe") == "pilihan" and not isinstance(k.get("pilihan"), list):
                masalah.append(
                    f"tabel {t.get('nama')!r} kolom {k.get('nama')!r}: "
                    "tipe 'pilihan' wajib punya daftar 'pilihan'")
    # Pindai seluruh manifes (sebagai JSON) untuk pola kode.
    teks = json.dumps(manifes, ensure_ascii=False)
    for pola in POLA_KODE_DILARANG:
        if pola.lower() in teks.lower():
            masalah.append(f"manifes memuat kode yang dilarang: {pola!r}")
            break
    return masalah


def gerbang_izin(manifes: dict) -> dict:
    """Gerbang 3: manifes izin turunan dari blok yang dipakai di alur
    modul. Modul tanpa alur -> izin kosong."""
    alur = manifes.get("alur")
    if not alur:
        return {"konektor": [], "scope": [], "domain": [],
                "batas_belanja": 0, "blok": []}
    return hitung_manifes(alur.get("langkah", []))


def gerbang_uji_kering(manifes: dict, dsn=None) -> list[str]:
    """Gerbang 4: uji kering dengan data contoh — alur modul dijalankan
    mode kering (bila ada), dan tiap tampilan dirender dengan data
    contoh. Masalah = alasan gagal."""
    from puspa.modules.views import render_modul

    masalah: list[str] = []
    alur = manifes.get("alur")
    if alur:
        from puspa.workflow.runner import WorkflowError  # noqa: F401
        # Uji kering alur butuh baris alur di DB — dilakukan di
        # aktifkan_modul (setelah modul dibuat). Di sini: periksa
        # langkah sudah lolos gerbang 1; jalan kering penuh dicatat
        # di sana. Jadi gerbang 4 di level manifes = render tampilan.
        pass
    try:
        render_modul(manifes)
    except Exception as exc:  # noqa: BLE001
        masalah.append(f"pratinjau gagal dirender dengan data contoh: {exc}")
    return masalah


def gerbang_ukuran(manifes: dict) -> list[str]:
    """Gerbang 5: batas ukuran."""
    masalah: list[str] = []
    if len(manifes.get("tampilan", []) or []) > MAKS_TAMPILAN:
        masalah.append(f"tampilan {len(manifes['tampilan'])} — maks {MAKS_TAMPILAN} per modul")
    if len(manifes.get("tabel", []) or []) > MAKS_TABEL:
        masalah.append(f"tabel {len(manifes['tabel'])} — maks {MAKS_TABEL} per modul")
    for t in manifes.get("tabel", []) or []:
        if len(t.get("kolom", []) or []) > MAKS_KOLOM_PER_TABEL:
            masalah.append(
                f"tabel {t.get('nama')!r}: {len(t['kolom'])} kolom — "
                f"maks {MAKS_KOLOM_PER_TABEL}")
    ukuran = len(json.dumps(manifes, ensure_ascii=False).encode("utf-8"))
    if ukuran > MAKS_UKURAN_MANIFES_BYTE:
        masalah.append(f"manifes {ukuran} byte — maks {MAKS_UKURAN_MANIFES_BYTE}")
    alur = manifes.get("alur")
    if alur and len(alur.get("langkah", []) or []) > 10:
        masalah.append("alur modul: maks 10 langkah")
    return masalah


def jalankan_gerbang(manifes: dict, dsn=None) -> dict:
    """Jalankan kelima gerbang. Kembalikan {lolos, masalah, manifes_izin}."""
    masalah = gerbang_skema(manifes)
    masalah += gerbang_pemindai(manifes)
    masalah += gerbang_ukuran(manifes)
    masalah += gerbang_uji_kering(manifes, dsn=dsn)
    try:
        manifes_izin = gerbang_izin(manifes)
    except Exception as exc:  # noqa: BLE001
        masalah.append(f"manifes izin turunan gagal dihitung: {exc}")
        manifes_izin = {"konektor": [], "scope": [], "domain": [],
                        "batas_belanja": 0, "blok": []}
    return {"lolos": not masalah, "masalah": masalah,
            "manifes_izin": manifes_izin}


# ---------------------------------------------------------------- CRUD

def buat_modul(*, user_id: str, manifes: dict, dsn=None) -> dict:
    """Buat modul draf (versi 1). Gerbang 1-2-5 harus lolos saat dibuat;
    gerbang 3-4 dicek penuh saat aktivasi."""
    hasil = jalankan_gerbang(manifes, dsn=dsn)
    if hasil["masalah"]:
        raise ModuleError("manifes tidak lolos gerbang: " + "; ".join(hasil["masalah"]))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO modul (user_id, nama, manifes, status)"
                " VALUES (%s, %s, %s, 'draf') RETURNING *",
                (user_id, manifes["nama"], json.dumps(manifes)))
            row = dict(cur.fetchone())
            cur.execute(
                "INSERT INTO modul_versi (modul_id, versi, manifes)"
                " VALUES (%s, 1, %s)", (row["id"], json.dumps(manifes)))
            # Buat tabel milik modul dari manifes.
            from puspa.modules.tables import buat_tabel

            for t in manifes.get("tabel", []) or []:
                buat_tabel(modul_id=str(row["id"]), user_id=user_id,
                           nama=t["nama"], kolom=t["kolom"], dsn=dsn,
                           _conn=conn)
        conn.commit()
    return _row(row)


def ambil_modul(modul_id: str, *, user_id: str, dsn=None) -> dict | None:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM modul WHERE id = %s AND user_id = %s",
                        (modul_id, user_id))
            row = cur.fetchone()
    return _row(row) if row else None


def daftar_modul(user_id: str, *, status: str | None = None, dsn=None) -> list[dict]:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute("SELECT * FROM modul WHERE user_id = %s AND status = %s"
                            " ORDER BY dibuat", (user_id, status))
            else:
                cur.execute("SELECT * FROM modul WHERE user_id = %s ORDER BY dibuat",
                            (user_id,))
            return [_row(r) for r in cur.fetchall()]


def aktifkan_modul(modul_id: str, *, user_id: str, izin_user: dict,
                   dsn=None) -> dict:
    """Aktifkan modul: kelima gerbang + izin turunan himpunan bagian
    dari izin user + maks 3 modul aktif + uji kering alur modul
    (bila ada) dengan data contoh."""
    modul = ambil_modul(modul_id, user_id=user_id, dsn=dsn)
    if modul is None:
        raise ModuleError("modul tidak ditemukan")
    if modul["status"] == "dihapus":
        raise ModuleError("modul sudah dihapus")
    hasil = jalankan_gerbang(modul["manifes"], dsn=dsn)
    if not hasil["lolos"]:
        raise ModuleError("manifes tidak lolos gerbang: " + "; ".join(hasil["masalah"]))
    lolos, kurang = subset_izin(hasil["manifes_izin"], izin_user)
    if not lolos:
        raise ModuleError("manifes izin di luar izin user: " + "; ".join(kurang))
    aktif = [m for m in daftar_modul(user_id, status="aktif", dsn=dsn)
             if str(m["id"]) != str(modul_id)]
    if len(aktif) >= MAKS_MODUL_AKTIF:
        raise ModuleError(f"sudah ada {MAKS_MODUL_AKTIF} modul aktif — maks di MVP")
    # Gerbang 4 (jalan): uji kering alur modul bila ada.
    alur = modul["manifes"].get("alur")
    if alur:
        from puspa.workflow.runner import buat_alur, jalankan_alur, uji_kering

        alur_sementara = buat_alur(
            user_id=user_id, agen_id="puspa",
            nama=f"uji kering modul {modul['nama']}",
            pemicu=alur.get("pemicu", {"jenis": "manual"}),
            langkah=alur.get("langkah", []), dsn=dsn)
        try:
            hasil_kering = uji_kering(str(alur_sementara["id"]), user_id=user_id, dsn=dsn)
        finally:
            from puspa.workflow.runner import hapus_alur

            hapus_alur(str(alur_sementara["id"]), user_id=user_id, dsn=dsn)
        if not hasil_kering.get("sukses"):
            raise ModuleError(
                "uji kering alur modul gagal: " + (hasil_kering.get("alasan") or ""))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE modul SET status = 'aktif', diubah = now()"
                " WHERE id = %s RETURNING *", (modul_id,))
            row = dict(cur.fetchone())
        conn.commit()
    return _row(row)


def ubah_modul(modul_id: str, *, user_id: str, manifes: dict, dsn=None) -> dict:
    """Ubah manifes (hanya lewat sesi penyusunan user). Versi baru;
    gerbang 1-2-5 dicek ulang, gerbang 3-4 saat aktivasi ulang."""
    modul = ambil_modul(modul_id, user_id=user_id, dsn=dsn)
    if modul is None:
        raise ModuleError("modul tidak ditemukan")
    if modul["status"] == "dihapus":
        raise ModuleError("modul sudah dihapus")
    hasil = jalankan_gerbang(manifes, dsn=dsn)
    if hasil["masalah"]:
        raise ModuleError("manifes tidak lolos gerbang: " + "; ".join(hasil["masalah"]))
    versi_baru = int(modul["versi_aktif"]) + 1
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE modul SET manifes = %s, versi_aktif = %s, status = 'draf',"
                " diubah = now() WHERE id = %s RETURNING *",
                (json.dumps(manifes), versi_baru, modul_id))
            row = dict(cur.fetchone())
            cur.execute(
                "INSERT INTO modul_versi (modul_id, versi, manifes)"
                " VALUES (%s, %s, %s)",
                (modul_id, versi_baru, json.dumps(manifes)))
        conn.commit()
    return _row(row)


def rollback_modul(modul_id: str, *, user_id: str, versi: int, dsn=None) -> dict:
    """Pulihkan versi lama (menjadi versi baru)."""
    modul = ambil_modul(modul_id, user_id=user_id, dsn=dsn)
    if modul is None:
        raise ModuleError("modul tidak ditemukan")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT manifes FROM modul_versi WHERE modul_id = %s AND versi = %s",
                (modul_id, versi))
            lama = cur.fetchone()
            if lama is None:
                raise ModuleError(f"versi {versi} tidak ada")
    return ubah_modul(modul_id, user_id=user_id, manifes=lama["manifes"], dsn=dsn)


def ekspor_data_modul(modul_id: str, *, user_id: str, dsn=None) -> dict:
    """Ekspor seluruh data tabel milik modul (JSON) — ditawarkan
    sebelum hapus (§2.13)."""
    from puspa.modules.tables import daftar_tabel, baca_tabel

    modul = ambil_modul(modul_id, user_id=user_id, dsn=dsn)
    if modul is None:
        raise ModuleError("modul tidak ditemukan")
    ekspor = {"modul": modul["nama"], "tabel": {}}
    for t in daftar_tabel(modul_id, user_id=user_id, dsn=dsn):
        ekspor["tabel"][t["nama"]] = baca_tabel(
            t["id"], user_id=user_id, dsn=dsn)
    return ekspor


def hapus_modul(modul_id: str, *, user_id: str, dsn=None) -> dict:
    """Hapus modul. Kembalikan ekspor datanya dulu (pemanggil
    menampilkannya ke user sebelum benar-benar menghapus — di sini
    ekspor dikembalikan bersama hasil hapus)."""
    ekspor = ekspor_data_modul(modul_id, user_id=user_id, dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE modul SET status = 'dihapus', diubah = now()"
                " WHERE id = %s AND user_id = %s RETURNING *",
                (modul_id, user_id))
            row = cur.fetchone()
            cur.execute("DELETE FROM baris_modul WHERE modul_id = %s", (modul_id,))
            cur.execute("DELETE FROM tabel_modul WHERE modul_id = %s", (modul_id,))
        conn.commit()
    return {"dihapus": _row(dict(row)) if row else None, "ekspor": ekspor}
