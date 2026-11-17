"""Menjalankan alur (§2.12 revisi 7).

- Tiap jalan alur adalah tugas biasa di Buku Kerja: tiap langkah
  dicatat, angka dirender dari bukti, Pemeriksa tetap berlaku
  (vonis dari blok baca; angka tanpa bukti_id maksimal Belum pasti).
- Aksi keluar TIDAK dieksekusi langsung: masuk antrean persetujuan
  (approvals.py); jalan berhenti sampai user memutuskan.
- Uji kering: mode baca saja — blok aksi keluar diganti simulasi.
- Alur tidak bisa mengubah dirinya: runner hanya MEMBACA definisi
  alur; tidak ada alat edit alur di dalam eksekutor, dan teks apa pun
  dari data (email, listing, jawaban) diperlakukan sebagai data.
- Gagal terlihat: gagal 2x berturut-turut → dijeda otomatis + alasan
  dikembalikan ke pemanggil (untuk diberi tahu ke user).
- Sukses 3x + lolos Pemeriksa → tercatat sebagai prosedur percobaan
  di Graf Alur Layanan (§2.12, §3.3).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from puspa.workflow import approvals
from puspa.workflow.blocks import (
    BLOK, MAKS_ALUR_AKTIF, PLAFON_BIAYA_JALAN_RP, periksa_alur,
)
from puspa.workflow.manifest import hitung_manifes, subset_izin
from puspa.workflow.triggers import validasi_pemicu

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


class WorkflowError(Exception):
    """Alur tidak valid, di luar batas MVP, atau tidak bisa jalan."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def _catat_ledger(tugas_id, agen_id, jenis, isi, dsn=None):
    """Tulis kejadian ke Buku Kerja (bila ledger tersedia). Gagal tulis
    ledger tidak menggagalkan jalan alur — tapi dicatat di alasan."""
    try:
        from puspa.ledger import Ledger

        Ledger(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)).append_event(
            tugas_id=tugas_id, agen_id=agen_id, jenis=jenis, isi=isi)
    except Exception as exc:  # noqa: BLE001 — ledger sekunder di runner
        return str(exc)
    return None


# ---------------------------------------------------------------- CRUD

def buat_alur(*, user_id: str, agen_id: str, nama: str, pemicu: dict,
              langkah: list, dsn=None) -> dict:
    """Buat alur baru berstatus 'draf' + simpan versi 1."""
    masalah = validasi_pemicu(pemicu) + periksa_alur(langkah)
    if masalah:
        raise WorkflowError("; ".join(masalah))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO alur (user_id, agen_id, nama, pemicu, langkah, status)"
                " VALUES (%s, %s, %s, %s, %s, 'draf') RETURNING *",
                (user_id, agen_id, nama, json.dumps(pemicu), json.dumps(langkah)))
            row = dict(cur.fetchone())
            cur.execute(
                "INSERT INTO alur_versi (alur_id, versi, pemicu, langkah)"
                " VALUES (%s, 1, %s, %s)",
                (row["id"], json.dumps(pemicu), json.dumps(langkah)))
        conn.commit()
    _catat_ledger(f"alur-{row['id']}", agen_id, "alur_diajukan",
                  {"alur_id": str(row["id"]), "nama": nama}, dsn)
    return _row(row)


def ambil_alur(alur_id: str, *, user_id: str, dsn=None) -> dict | None:
    """Baca satu alur — hanya milik user itu."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM alur WHERE id = %s AND user_id = %s",
                        (alur_id, user_id))
            row = cur.fetchone()
    return _row(row) if row else None


def daftar_alur(user_id: str, *, status: str | None = None, dsn=None) -> list[dict]:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute("SELECT * FROM alur WHERE user_id = %s AND status = %s"
                            " ORDER BY dibuat", (user_id, status))
            else:
                cur.execute("SELECT * FROM alur WHERE user_id = %s ORDER BY dibuat",
                            (user_id,))
            return [_row(r) for r in cur.fetchall()]


def _row(row: dict) -> dict:
    row = dict(row)
    for k in ("id", "user_id"):
        if k in row and row[k] is not None:
            row[k] = str(row[k])
    return row


def ubah_alur(alur_id: str, *, user_id: str, pemicu: dict | None = None,
              langkah: list | None = None, dsn=None) -> dict:
    """Ubah definisi alur (hanya lewat sesi penyusunan user — runner
    tidak pernah memanggil ini). Tiap perubahan = versi baru."""
    alur = ambil_alur(alur_id, user_id=user_id, dsn=dsn)
    if alur is None:
        raise WorkflowError("alur tidak ditemukan")
    if alur["status"] == "dihapus":
        raise WorkflowError("alur sudah dihapus")
    pemicu_baru = pemicu if pemicu is not None else alur["pemicu"]
    langkah_baru = langkah if langkah is not None else alur["langkah"]
    masalah = validasi_pemicu(pemicu_baru) + periksa_alur(langkah_baru)
    if masalah:
        raise WorkflowError("; ".join(masalah))
    versi_baru = int(alur["versi_aktif"]) + 1
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE alur SET pemicu = %s, langkah = %s, versi_aktif = %s,"
                " diubah = now() WHERE id = %s RETURNING *",
                (json.dumps(pemicu_baru), json.dumps(langkah_baru),
                 versi_baru, alur_id))
            row = dict(cur.fetchone())
            cur.execute(
                "INSERT INTO alur_versi (alur_id, versi, pemicu, langkah)"
                " VALUES (%s, %s, %s, %s)",
                (alur_id, versi_baru, json.dumps(pemicu_baru),
                 json.dumps(langkah_baru)))
        conn.commit()
    return _row(row)


def rollback_alur(alur_id: str, *, user_id: str, versi: int, dsn=None) -> dict:
    """Pulihkan versi lama (menjadi versi baru — riwayat tidak ditimpa)."""
    alur = ambil_alur(alur_id, user_id=user_id, dsn=dsn)
    if alur is None:
        raise WorkflowError("alur tidak ditemukan")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pemicu, langkah FROM alur_versi"
                " WHERE alur_id = %s AND versi = %s", (alur_id, versi))
            lama = cur.fetchone()
            if lama is None:
                raise WorkflowError(f"versi {versi} tidak ada")
    return ubah_alur(alur_id, user_id=user_id,
                     pemicu=lama["pemicu"], langkah=lama["langkah"], dsn=dsn)


def hapus_alur(alur_id: str, *, user_id: str, dsn=None) -> dict:
    """Hapus alur (status 'dihapus'). Riwayat jalan di Buku Kerja dan
    tabel jalan_alur TIDAK dihapus (§2.12)."""
    alur = ambil_alur(alur_id, user_id=user_id, dsn=dsn)
    if alur is None:
        raise WorkflowError("alur tidak ditemukan")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE alur SET status = 'dihapus', diubah = now()"
                " WHERE id = %s RETURNING *", (alur_id,))
            row = dict(cur.fetchone())
        conn.commit()
    return _row(row)


def aktifkan_alur(alur_id: str, *, user_id: str, izin_user: dict,
                  hasil_kering: dict | None = None, dsn=None) -> dict:
    """Aktifkan alur. Syarat (semua oleh kode):
    1. maks 5 alur aktif per pengguna;
    2. manifes izin turunan himpunan bagian dari izin user;
    3. uji kering sudah dijalankan dan sukses (hasil_kering wajib).
    """
    alur = ambil_alur(alur_id, user_id=user_id, dsn=dsn)
    if alur is None:
        raise WorkflowError("alur tidak ditemukan")
    if alur["status"] == "dihapus":
        raise WorkflowError("alur sudah dihapus")
    if hasil_kering is None or not hasil_kering.get("sukses"):
        raise WorkflowError(
            "alur belum lolos uji kering — jalankan uji kering dulu, "
            "lihat hasilnya, baru aktifkan")
    aktif = [a for a in daftar_alur(user_id, status="aktif", dsn=dsn)
             if str(a["id"]) != str(alur_id)]
    if len(aktif) >= MAKS_ALUR_AKTIF:
        raise WorkflowError(
            f"sudah ada {MAKS_ALUR_AKTIF} alur aktif — maks di MVP")
    manifes = hitung_manifes(alur["langkah"])
    lolos, kurang = subset_izin(manifes, izin_user)
    if not lolos:
        raise WorkflowError("manifes izin di luar izin user: " + "; ".join(kurang))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE alur SET status = 'aktif', diubah = now()"
                " WHERE id = %s RETURNING *", (alur_id,))
            row = dict(cur.fetchone())
        conn.commit()
    _catat_ledger(f"alur-{alur_id}", alur["agen_id"], "alur_diaktifkan",
                  {"alur_id": str(alur_id), "manifes": manifes}, dsn)
    return _row(row)


def jeda_alur(alur_id: str, *, user_id: str, alasan: str = "", dsn=None) -> dict:
    alur = ambil_alur(alur_id, user_id=user_id, dsn=dsn)
    if alur is None:
        raise WorkflowError("alur tidak ditemukan")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE alur SET status = 'dijeda', diubah = now()"
                " WHERE id = %s RETURNING *", (alur_id,))
            row = dict(cur.fetchone())
        conn.commit()
    _catat_ledger(f"alur-{alur_id}", alur["agen_id"], "alur_dijeda",
                  {"alur_id": str(alur_id), "alasan": alasan}, dsn)
    return _row(row)


# ---------------------------------------------------------------- eksekutor

def eksekutor_fixture():
    """Eksekutor default: data uji (fixture), tanpa HTTP live.
    - cek_tagihan: DATA_UJI Fase 3 (PLN Rp132.500 dst.) — berlabel fixture.
    - bandingkan/pantau harga: gerbang ToS Fase 3 (boleh_baca) — live
      ditahan keputusan 15, kembalikan tidak_bisa_dicek TANPA HTTP.
    - email/draf/event/bayar: draf + simulasi (tanpa konektor live).
    """
    from puspa.billing.fixture import DATA_UJI

    def cek_tagihan(provider, nomor_pelanggan=""):
        data = DATA_UJI.get(str(provider).lower())
        if data is None:
            return {"sukses": False, "alasan": "provider_di_luar_mvp",
                    "pesan": f"Provider {provider!r} bukan provider MVP."}
        return {"sukses": True, "jumlah": data["jumlah"],
                "jatuh_tempo": data["jatuh_tempo"], "status": data["status"],
                "bukti_id": f"fixture:{provider}", "vonis": "Belum pasti",
                "sumber": "fixture"}

    def bandingkan_harga(nama_barang):
        from puspa.pricing.tos import boleh_baca

        if not boleh_baca("tokopedia"):
            return {"sukses": False, "alasan": "syarat_layanan_menunggu_keputusan_nero",
                    "pesan": "Tidak bisa cek harga live — syarat layanan marketplace "
                             "menunggu keputusan Nero (keputusan 15)."}
        return {"sukses": False, "alasan": "tidak_diimplementasi",
                "pesan": "Jalur live belum dibuka (keputusan 15)."}

    def pantau_harga(produk_id, ambang_harga):
        hasil = bandingkan_harga(produk_id)
        return hasil

    return {
        "cek_tagihan": cek_tagihan,
        "bandingkan_harga": bandingkan_harga,
        "pantau_harga": pantau_harga,
        "cari_email": lambda kata_kunci: {"sukses": True, "email": [], "sumber": "fixture"},
        "baca_email": lambda email_id: {"sukses": True, "pengirim": "", "subjek": "",
                                        "isi": "", "sumber": "fixture"},
        "buat_draf": lambda penerima, subjek, isi: {
            "sukses": True, "draf_id": "draf-fixture", "sumber": "fixture"},
        "buat_event": lambda judul, waktu, peserta: {
            "sukses": True, "event_id": "event-fixture", "sumber": "fixture"},
        "siapkan_bayar": lambda provider, jumlah, tujuan: {
            "sukses": True, "draf_bayar_id": "bayar-fixture",
            "isi_hash": approvals.isi_hash(
                {"provider": provider, "jumlah": jumlah, "tujuan": tujuan}),
            "sumber": "fixture"},
        "kirim_notif": lambda pesan: {"sukses": True, "terkirim": True},
        "tanya_user": lambda pertanyaan: {"sukses": False, "jawaban": None,
                                          "alasan": "menunggu_jawaban_user"},
        "simpan_memori": lambda fakta, asal="diucapkan_user": {
            "sukses": True, "tersimpan": True},
        "simpan_ke_tabel_modul": lambda modul, tabel, baris: {
            "sukses": True, "tersimpan": len(baris or [])},
    }


# ---------------------------------------------------------------- evaluasi kondisi

def evaluasi_ungkapan(ungkapan: str, konteks: dict) -> bool:
    """Evaluasi ungkapan kondisi sederhana: `<field> <op> <nilai>`.
    Field diambil dari hasil langkah sebelumnya (konteks). Operator:
    == != > < >= <=. Nilai: angka, atau teks tanpa spasi. Ungkapan
    yang tidak bisa diparse → WorkflowError (gagal terlihat)."""
    cocok = re.match(r"^\s*([\w.]+)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$", str(ungkapan))
    if not cocok:
        raise WorkflowError(f"ungkapan kondisi tidak bisa diparse: {ungkapan!r}")
    field, op, nilai_teks = cocok.groups()
    nilai_konteks = konteks
    for bagian in field.split("."):
        if isinstance(nilai_konteks, dict) and bagian in nilai_konteks:
            nilai_konteks = nilai_konteks[bagian]
        else:
            raise WorkflowError(f"ungkapan kondisi: field {field!r} tidak ada")
    try:
        nilai = float(nilai_teks)
        banding = float(nilai_konteks)
    except (TypeError, ValueError):
        nilai = nilai_teks.strip("'\"")
        banding = str(nilai_konteks)
    if op == "==":
        return banding == nilai
    if op == "!=":
        return banding != nilai
    if op == ">":
        return banding > nilai
    if op == "<":
        return banding < nilai
    if op == ">=":
        return banding >= nilai
    return banding <= nilai


# ---------------------------------------------------------------- jalan alur

def _simpan_jalan(*, alur_id, user_id, tugas_id, pemicu_jenis, status,
                  biaya_rp, langkah_hasil, alasan, dsn=None):
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jalan_alur (alur_id, user_id, tugas_id, pemicu_jenis,"
                " status, biaya_rp, langkah_hasil, alasan, selesai)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id",
                (alur_id, user_id, tugas_id, pemicu_jenis, status, biaya_rp,
                 json.dumps(langkah_hasil), alasan))
            jalan_id = str(cur.fetchone()["id"])
        conn.commit()
    return jalan_id


def _jalan_terakhir_gagal(user_id: str, alur_id: str, dsn=None) -> int:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM jalan_alur WHERE alur_id = %s"
                " ORDER BY dimulai DESC LIMIT 2", (alur_id,))
            return [r["status"] for r in cur.fetchall()]


def uji_kering(alur_id: str, *, user_id: str, konteks=None, dsn=None) -> dict:
    """Jalankan alur sekali mode baca saja: blok aksi keluar diganti
    simulasi. Kembalikan hasil jalan (lihat jalankan_alur)."""
    return jalankan_alur(alur_id, user_id=user_id, pemicu_jenis="manual",
                         konteks=konteks, mode="kering", dsn=dsn)


def jalankan_alur(alur_id: str, *, user_id: str, pemicu_jenis: str = "manual",
                  konteks=None, mode: str = "jalan",
                  jawaban: dict | None = None, dsn=None) -> dict:
    """Jalankan satu alur. `mode`: 'jalan' atau 'kering'. `konteks`:
    eksekutor (default fixture). `jawaban`: jawaban user untuk blok
    tanya_user ({judul_langkah: jawaban}). Kembalikan:
    {sukses, jalan_id, status, langkah_hasil, biaya_rp, alasan,
     persetujuan_id (bila berhenti di persetujuan)}.
    """
    alur = ambil_alur(alur_id, user_id=user_id, dsn=dsn)
    if alur is None:
        raise WorkflowError("alur tidak ditemukan")
    if mode == "jalan" and alur["status"] != "aktif":
        raise WorkflowError(f"alur berstatus {alur['status']!r} — aktifkan dulu")
    eksekutor = konteks or eksekutor_fixture()
    tugas_id = f"jalan-{uuid.uuid4().hex[:12]}"
    _catat_ledger(tugas_id, alur["agen_id"], "tugas_dibuat",
                  {"alur_id": str(alur_id), "pemicu": pemicu_jenis, "mode": mode}, dsn)
    _catat_ledger(tugas_id, alur["agen_id"], "pemicu_terpicu",
                  {"alur_id": str(alur_id), "pemicu": pemicu_jenis}, dsn)

    hasil_konteks: dict = {}
    langkah_hasil: list[dict] = []
    biaya = 0
    status = "selesai"
    alasan = ""
    persetujuan_id = None

    def _jalan_langkah(daftar, kedalaman=0):
        nonlocal biaya, status, alasan, persetujuan_id
        for langkah in daftar:
            if status != "selesai":
                return
            nama = langkah.get("blok")
            if nama not in BLOK:
                status, alasan = "gagal", f"blok {nama!r} tidak ada di pustaka"
                return
            masukan = dict(langkah.get("masukan", {}) or {})
            # Resolusi nilai dari hasil langkah sebelumnya: masukan
            # {"jumlah": "{cek_tagihan.jumlah}"} diisi dari konteks.
            for k, v in list(masukan.items()):
                if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
                    kunci = v[1:-1]
                    masukan[k] = hasil_konteks.get(kunci, v)
            biaya += int(BLOK[nama].get("biaya_rp", 0))
            if biaya > PLAFON_BIAYA_JALAN_RP:
                status, alasan = "gagal", (
                    f"plafon biaya per jalan Rp{PLAFON_BIAYA_JALAN_RP:,} terlampaui"
                    .replace(",", "."))
                return
            if nama == "kondisi":
                try:
                    benar = evaluasi_ungkapan(masukan.get("ungkapan", ""), hasil_konteks)
                except WorkflowError as exc:
                    status, alasan = "gagal", str(exc)
                    return
                cabang = langkah.get("maka", []) if benar else langkah.get("kalau_tidak", [])
                langkah_hasil.append({"blok": nama, "benar": benar, "sukses": True})
                _jalan_langkah(cabang, kedalaman + 1)
                continue
            if nama == "untuk_tiap":
                daftar_item = masukan.get("daftar", [])
                if not isinstance(daftar_item, list):
                    daftar_item = hasil_konteks.get(str(masukan.get("daftar", "")), [])
                if not isinstance(daftar_item, list):
                    status, alasan = "gagal", "untuk_tiap: 'daftar' bukan daftar"
                    return
                if len(daftar_item) > 20:
                    status, alasan = "gagal", "untuk_tiap: maks 20 item"
                    return
                for item in daftar_item:
                    hasil_konteks["item"] = item
                    _jalan_langkah(langkah.get("langkah_dalam", []), kedalaman + 1)
                    if status != "selesai":
                        return
                langkah_hasil.append({"blok": nama, "jumlah_item": len(daftar_item),
                                      "sukses": True})
                continue
            # Blok aksi keluar: kering -> simulasi; jalan -> antre persetujuan.
            if BLOK[nama].get("aksi_keluar"):
                aksi = {"blok": nama, "masukan": masukan}
                if mode == "kering":
                    langkah_hasil.append({"blok": nama, "sukses": True,
                                          "simulasi": True, "aksi": aksi})
                    hasil_konteks[nama] = {"simulasi": True}
                    continue
                # Jalan biasa: buat jalan dulu? persetujuan butuh jalan_id.
                # Simpan jalan 'menunggu_persetujuan' di bawah (setelah loop).
                status = "menunggu_persetujuan"
                alasan = f"menunggu persetujuan: {nama}"
                langkah_hasil.append({"blok": nama, "sukses": None,
                                      "menunggu_persetujuan": True, "aksi": aksi})
                persetujuan_id = aksi  # ditukar id setelah jalan disimpan
                return
            if nama == "tanya_user":
                judul = langkah.get("judul") or nama
                if jawaban and judul in jawaban:
                    hasil = {"sukses": True, "jawaban": jawaban[judul]}
                else:
                    status = "menunggu_jawaban"
                    alasan = f"menunggu jawaban user: {masukan.get('pertanyaan', '')}"
                    langkah_hasil.append({"blok": nama, "sukses": None,
                                          "menunggu_jawaban": True})
                    return
                langkah_hasil.append({"blok": nama, "sukses": True, "hasil": hasil})
                hasil_konteks[nama] = hasil
                continue
            fn = eksekutor.get(nama)
            if fn is None:
                status, alasan = "gagal", f"eksekutor belum menyediakan blok {nama!r}"
                return
            try:
                hasil = fn(**masukan) if masukan else fn()
            except TypeError:
                try:
                    hasil = fn(masukan)
                except Exception as exc:  # noqa: BLE001
                    status, alasan = "gagal", f"blok {nama} gagal: {exc}"
                    return
            except Exception as exc:  # noqa: BLE001
                status, alasan = "gagal", f"blok {nama} gagal: {exc}"
                return
            if not isinstance(hasil, dict):
                status, alasan = "gagal", f"blok {nama}: keluaran bukan objek"
                return
            if hasil.get("sukses") is False:
                # Gagal terlihat: blok bilang tidak bisa — jalan gagal
                # dengan alasan blok (bukan ditebak).
                status, alasan = "gagal", hasil.get("pesan") or hasil.get("alasan") or nama
                langkah_hasil.append({"blok": nama, "sukses": False, "hasil": hasil})
                return
            # Pemeriksa tetap berlaku: angka dari blok baca tanpa bukti_id
            # maksimal Belum pasti (bukan Terverifikasi).
            if nama in ("cek_tagihan", "bandingkan_harga", "pantau_harga"):
                if not hasil.get("bukti_id"):
                    hasil = dict(hasil, vonis="Belum pasti")
            langkah_hasil.append({"blok": nama, "sukses": True, "hasil": hasil})
            hasil_konteks[nama] = hasil
            # Simpan juga per field supaya kondisi bisa membaca
            # "{cek_tagihan.jumlah}" — konteks datar per blok sudah cukup;
            # field diakses sebagai "blok.field" di evaluasi_ungkapan.
            for k, v in hasil.items():
                hasil_konteks[f"{nama}.{k}"] = v

    _jalan_langkah(alur["langkah"])

    # Persetujuan (bila berhenti di aksi keluar): jalan harus ada dulu.
    jalan_id = _simpan_jalan(
        alur_id=alur_id, user_id=user_id, tugas_id=tugas_id,
        pemicu_jenis=pemicu_jenis,
        status="menunggu_persetujuan" if status == "menunggu_persetujuan" else (
            "selesai" if status == "selesai" else (
                "menunggu_jawaban" if status == "menunggu_jawaban" else "gagal")),
        biaya_rp=biaya, langkah_hasil=langkah_hasil,
        alasan=alasan, dsn=dsn)
    if status == "menunggu_persetujuan" and isinstance(persetujuan_id, dict):
        aksi_keluar = persetujuan_id
        dibuat = approvals.minta_persetujuan(
            jalan_id=jalan_id, alur_id=alur_id, user_id=user_id,
            aksi=aksi_keluar, dsn=dsn)
        persetujuan_id = str(dibuat["id"])
        _catat_ledger(tugas_id, alur["agen_id"], "persetujuan_diminta",
                      {"persetujuan_id": persetujuan_id, "aksi": aksi_keluar}, dsn)

    if status == "selesai":
        _catat_ledger(tugas_id, alur["agen_id"], "tugas_selesai",
                      {"alur_id": str(alur_id), "biaya_rp": biaya}, dsn)
        # Uji kering TIDAK menghitung jalan sukses (bukan jalan nyata):
        # hitungan sukses + promosi ke prosedur hanya untuk mode jalan.
        if mode != "jalan":
            return {"sukses": True, "jalan_id": jalan_id, "status": "selesai",
                    "langkah_hasil": langkah_hasil, "biaya_rp": biaya, "alasan": ""}
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE alur SET jalan_sukses = jalan_sukses + 1,"
                    " gagal_berturut = 0 WHERE id = %s RETURNING jalan_sukses",
                    (alur_id,))
                sukses_ke = int(cur.fetchone()["jalan_sukses"])
            conn.commit()
        try:
            from puspa.moat.library import catat_alur_sukses

            catat_alur_sukses(user_id=user_id, nama=alur["nama"],
                              blok=[l.get("blok") for l in alur["langkah"]], dsn=dsn)
        except Exception:  # noqa: BLE001 — pustaka sekunder
            pass
        if sukses_ke == 3:
            try:
                from puspa.memory.procedural import record_successful_task

                record_successful_task(
                    user_id, f"alur:{alur['nama']}", alur["langkah"],
                    agen_id=alur["agen_id"], dsn=dsn)
            except Exception:  # noqa: BLE001 — prosedur sekunder
                pass
        return {"sukses": True, "jalan_id": jalan_id, "status": "selesai",
                "langkah_hasil": langkah_hasil, "biaya_rp": biaya, "alasan": ""}
    if status in ("menunggu_persetujuan", "menunggu_jawaban"):
        return {"sukses": False, "jalan_id": jalan_id, "status": status,
                "langkah_hasil": langkah_hasil, "biaya_rp": biaya,
                "alasan": alasan, "persetujuan_id": persetujuan_id}
    # Gagal: 2x berturut-turut -> dijeda otomatis + diberi tahu.
    # Uji kering yang gagal TIDAK menambah hitungan gagal (bukan jalan nyata).
    _catat_ledger(tugas_id, alur["agen_id"], "tugas_gagal",
                  {"alur_id": str(alur_id), "alasan": alasan}, dsn)
    if mode != "jalan":
        return {"sukses": False, "jalan_id": jalan_id, "status": "gagal",
                "langkah_hasil": langkah_hasil, "biaya_rp": biaya,
                "alasan": alasan, "dijeda_otomatis": False}
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE alur SET gagal_berturut = gagal_berturut + 1"
                " WHERE id = %s RETURNING gagal_berturut",
                (alur_id,))
            gagal = int(cur.fetchone()["gagal_berturut"])
        conn.commit()
    dijeda = False
    if gagal >= 2 and alur["status"] == "aktif":
        jeda_alur(alur_id, user_id=user_id, alasan=alasan, dsn=dsn)
        dijeda = True
    return {"sukses": False, "jalan_id": jalan_id, "status": "gagal",
            "langkah_hasil": langkah_hasil, "biaya_rp": biaya,
            "alasan": alasan, "dijeda_otomatis": dijeda}


def eksekusi_aksi_disetujui(persetujuan_id: str, *, user_id: str,
                            konteks=None, dsn=None) -> dict:
    """Eksekusi aksi keluar SETELAH user menyetujui (persetujuan sudah
    'diberikan'). Isi aksi diambil dari antrean — bukan dari input
    baru — jadi isi yang dieksekusi = isi yang disetujui."""
    from puspa.workflow.approvals import ambil_persetujuan

    row = ambil_persetujuan(persetujuan_id, user_id=user_id, dsn=dsn)
    if row is None:
        raise WorkflowError("persetujuan tidak ditemukan")
    if row["status"] != "diberikan":
        raise WorkflowError(f"persetujuan berstatus {row['status']!r} — tidak dieksekusi")
    aksi = row["aksi"]
    nama = aksi.get("blok")
    masukan = aksi.get("masukan", {}) or {}
    eksekutor = konteks or eksekutor_fixture()
    fn = eksekutor.get(nama)
    if fn is None:
        raise WorkflowError(f"eksekutor belum menyediakan blok {nama!r}")
    hasil = fn(**masukan)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jalan_alur SET status = 'selesai', selesai = now()"
                " WHERE id = %s", (row["jalan_id"],))
        conn.commit()
    return {"sukses": True, "hasil": hasil}


# ---------------------------------------------------------------- tick penjadwal

def tick_alur(*, dsn=None, sekarang: datetime | None = None,
              terlewat: bool = False) -> list[dict]:
    """Satu putaran penjadwal untuk alur (§2.14): jalankan pemicu
    jadwal alur yang jatuh tempo (atau terlewat, bila `terlewat`).

    Memakai ulang `jalan_pemicu` Fase 2 (kunci idempotensi +
    `pemicu_terpicu` / `pemicu_terlewat` di Buku Kerja), lalu
    menjalankan alurnya. KONFLIK DICATAT: `jalan_pemicu` Fase 2
    menggeser `pemicu_berikutnya` +1 hari (asumsi jadwal harian) —
    untuk alur bulanan/mingguan itu salah, jadi tick ini MENIMPA
    jadwal berikutnya dari definisi pemicu alur sendiri
    (`triggers.pemicu_berikutnya`). Perbaikan +1 hari di penjadwal
    Fase 2 adalah tindak lanjut wrap-up (Fase 2 tidak diubah di sini).
    """
    from puspa.scheduler.scheduler import (
        daftar_pemicu_jatuh_tempo, jalan_pemicu, jalankan_yang_terlewat,
    )
    from puspa.workflow.triggers import pemicu_berikutnya as hitung_berikutnya

    sekarang = sekarang or datetime.now(timezone.utc)
    if terlewat:
        due = jalankan_yang_terlewat(dsn=dsn, sekarang=sekarang)
        # jalankan_yang_terlewat sudah memanggil jalan_pemicu; ambil
        # daftar pemicunya dari hasil (pemicu_id + tugas_id).
        pemicu_due = [(j["pemicu_id"], j) for j in due]
    else:
        due = daftar_pemicu_jatuh_tempo(dsn=dsn, sekarang=sekarang)
        pemicu_due = [(p.id, None) for p in due]

    hasil: list[dict] = []
    for pemicu_id, jalan_pemicu_hasil in pemicu_due:
        # Ambil baris pemicu (untuk user_id + alur + jadwal).
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, alur, jadwal FROM pemicu WHERE id = %s",
                    (pemicu_id,))
                baris = cur.fetchone()
        if baris is None or not (baris["jadwal"] or "").startswith("alur:"):
            continue
        user_id = str(baris["user_id"])
        alur_id = baris["alur"]
        alur = ambil_alur(alur_id, user_id=user_id, dsn=dsn)
        if alur is None or alur["status"] != "aktif":
            continue
        if jalan_pemicu_hasil is None:
            # Tick biasa: klaim idempotensi dulu.
            from puspa.scheduler.scheduler import Pemicu as PemicuFase2

            with _connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pemicu_berikutnya FROM pemicu WHERE id = %s",
                        (pemicu_id,))
                    berikut = cur.fetchone()["pemicu_berikutnya"]
            pemicu_obj = PemicuFase2(
                id=pemicu_id, user_id=user_id, jenis="jadwal",
                jadwal=baris["jadwal"], alur=alur_id,
                pemicu_berikutnya=berikut)
            jalan_pemicu_hasil = jalan_pemicu(
                pemicu_obj, dsn=dsn, sekarang=sekarang, terlewat=terlewat)
            if jalan_pemicu_hasil is None:
                continue  # sudah pernah jalan (idempotensi)
        # Perbaiki jadwal berikutnya dari definisi pemicu alur.
        try:
            berikut_baru = hitung_berikutnya(alur["pemicu"], sekarang)
        except Exception:  # noqa: BLE001
            berikut_baru = None
        if berikut_baru is not None:
            with _connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE pemicu SET pemicu_berikutnya = %s WHERE id = %s",
                        (berikut_baru, pemicu_id))
                conn.commit()
        jalan = jalankan_alur(alur_id, user_id=user_id, pemicu_jenis="jadwal",
                              dsn=dsn)
        _catat_ledger(jalan_pemicu_hasil["tugas_id"], alur["agen_id"],
                      "pemicu_terlewat" if terlewat else "pemicu_terpicu",
                      {"alur_id": str(alur_id), "pemicu_id": pemicu_id,
                       "terlewat": terlewat}, dsn)
        hasil.append({"pemicu_id": pemicu_id, "alur_id": str(alur_id),
                      "terlewat": terlewat, "jalan": jalan})
    return hasil
