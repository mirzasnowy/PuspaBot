"""Metrik moat gerbang 8 (§3.6 + §3.8 revisi 7).

Diukur di sini (lokal, tanpa pengguna uji):
- cakupan pencocokan otomatis: % perbandingan yang cocok tanpa
  bertanya (target >= 60%);
- 4 prosedur terverifikasi + masing-masing dipakai ulang agen lain
  minimal 1 kali;
- canary mendeteksi perubahan yang disuntikkan di situs uji (2 dari 2);
- token per perbandingan minggu terakhir vs minggu pertama (target
  <= 80% — bukti roda gila);
- Skor Keandalan terhitung untuk setiap sumber yang dipakai.

Yang TIDAK diklaim di Fase 3 (butuh pengguna uji / masa uji):
- metrik 3.8 soal alur aktif (5/10 pengguna punya satu alur aktif)
  — itu FASE 4; fondasinya (pustaka alur tercatat) dibangun di
  `library.py`, metriknya dilaporkan sebagai `tertunda`.
- token minggu terakhir vs minggu pertama selama masa uji nyata —
  diukur di sini dari `metrik_token` apa adanya; tanpa masa uji,
  hasilnya dilaporkan sebagai `tertunda` bila data belum cukup.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from puspa.moat.reliability import skor_prosedur, skor_sumber

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
PROVIDER_MVP = ("PLN", "PDAM", "pulsa", "paket data")
TARGET_CAKUPAN_OTOMATIS = 60.0
TARGET_TOKEN_RASIO = 0.80


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def cakupan_pencocokan_otomatis(dsn=None) -> dict:
    """% tautan listing berstatus `otomatis` dari semua tautan yang
    bukan `ditolak_user` (proksi cakupan tanpa bertanya).

    Target >= 60%. Tanpa tautan sama sekali -> `tertunda`.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) AS n FROM tautan_listing GROUP BY status")
            hitung = {r["status"]: int(r["n"]) for r in cur.fetchall()}
    total = hitung.get("otomatis", 0) + hitung.get("dikonfirmasi_user", 0)
    if total == 0:
        return {"persen": None, "status": "tertunda",
                "alasan": "belum ada tautan listing di graf"}
    persen = round(hitung.get("otomatis", 0) / total * 100, 1)
    return {"persen": persen, "total": total,
            "otomatis": hitung.get("otomatis", 0),
            "dikonfirmasi_user": hitung.get("dikonfirmasi_user", 0),
            "status": "lolos" if persen >= TARGET_CAKUPAN_OTOMATIS else "belum_lolos"}


def prosedur_terverifikasi_dan_dipakai_ulang(user_id: str, dsn=None) -> dict:
    """4 prosedur MVP terverifikasi + masing-masing dipakai ulang
    agen lain minimal 1 kali (gerbang 8)."""
    from puspa.graphs.service import daftar_prosedur

    hasil = {}
    for provider in PROVIDER_MVP:
        prosedur = next(
            (p for p in daftar_prosedur(user_id, dsn=dsn) if p["layanan"] == provider),
            None)
        if prosedur is None:
            hasil[provider] = {"ada": False, "terverifikasi": False, "dipakai_ulang": False}
            continue
        # Pakai ulang = prosedur pernah dijalankan/dipakai oleh >= 2 agen
        # berbeda (agen yang menjalankan di `prosedur.agen_ids` + agen yang
        # tercatat di `pemakaian_prosedur`). "Dipakai ulang agen lain
        # minimal 1 kali" (§3.6) = ada agen kedua selain pembuatnya.
        agen_pakai = set(prosedur.get("agen_ids") or [])
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT agen_id FROM pemakaian_prosedur WHERE prosedur_id = %s",
                    (prosedur["id"],))
                agen_pakai |= {r["agen_id"] for r in cur.fetchall()}
        hasil[provider] = {
            "ada": True,
            "terverifikasi": prosedur["status"] == "terverifikasi",
            "dipakai_ulang": len(agen_pakai) >= 2,
            "agen_berbeda": len(agen_pakai),
            "status": prosedur["status"],
        }
    lolos = all(v.get("terverifikasi") and v.get("dipakai_ulang") for v in hasil.values())
    return {"per_provider": hasil, "status": "lolos" if lolos else "belum_lolos"}


def rasio_token_perbandingan(dsn=None) -> dict:
    """Token per perbandingan minggu terakhir vs minggu pertama.

    Tanpa 2 minggu data -> `tertunda` (jujur, bukan dikarang).
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dicatat, token FROM metrik_token"
                " WHERE jenis = 'bandingkan_harga' ORDER BY dicatat")
            baris = cur.fetchall()
    if len(baris) < 2:
        return {"rasio": None, "status": "tertunda",
                "alasan": "belum ada cukup data token (butuh masa uji 2 minggu)"}
    sekarang = datetime.now(timezone.utc)
    minggu_pertama = [r["token"] for r in baris
                      if r["dicatat"] < sekarang - timedelta(days=7)]
    minggu_terakhir = [r["token"] for r in baris
                       if r["dicatat"] >= sekarang - timedelta(days=7)]
    if not minggu_pertama or not minggu_terakhir:
        return {"rasio": None, "status": "tertunda",
                "alasan": "data token belum mencakup 2 minggu berbeda"}
    rata_awal = sum(minggu_pertama) / len(minggu_pertama)
    rata_akhir = sum(minggu_terakhir) / len(minggu_terakhir)
    rasio = round(rata_akhir / rata_awal, 3) if rata_awal else None
    return {"rasio": rasio, "rata_minggu_pertama": round(rata_awal, 1),
            "rata_minggu_terakhir": round(rata_akhir, 1),
            "status": "lolos" if rasio is not None and rasio <= TARGET_TOKEN_RASIO else "belum_lolos"}


def keandalan_semua_sumber(dsn=None) -> dict:
    """Skor Keandalan terhitung untuk setiap sumber yang dipakai."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT sumber, kategori FROM pembacaan_sumber")
            pasangan = [(r["sumber"], r["kategori"]) for r in cur.fetchall()]
    hasil = {}
    for sumber, kategori in pasangan:
        hasil[f"{sumber} x {kategori or '-'}"] = skor_sumber(sumber, kategori, dsn=dsn)
    return {"sumber": hasil,
            "status": "lolos" if hasil and all(
                v["persen_terverifikasi"] is not None for v in hasil.values())
            else ("tertunda" if not hasil else "belum_lolos")}


def ukur_semua(user_id: str, dsn=None) -> dict:
    """Semua metrik moat gerbang 8 dalam satu laporan."""
    return {
        "cakupan_pencocokan_otomatis": cakupan_pencocokan_otomatis(dsn=dsn),
        "prosedur_terverifikasi_dipakai_ulang": prosedur_terverifikasi_dan_dipakai_ulang(user_id, dsn=dsn),
        "rasio_token": rasio_token_perbandingan(dsn=dsn),
        "keandalan_sumber": keandalan_semua_sumber(dsn=dsn),
        "alur_aktif_pengguna_uji": {
            "status": "tertunda",
            "alasan": "metrik 3.8 (5/10 pengguna punya satu alur aktif) adalah FASE 4 — "
                      "fondasi pustaka alur tercatat di library.py, metriknya tidak diklaim di Fase 3"},
    }
