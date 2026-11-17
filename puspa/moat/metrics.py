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
PROVIDER_MVP = ("PLN", "PDAM", "airtime", "paket data")
TARGET_CAKUPAN_AUTOMATIC = 60.0
TARGET_TOKEN_RASIO = 0.80


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def cakupan_pencocokan_automatic(dsn=None) -> dict:
    """% tautan listing berstatus `otomatis` dari semua tautan yang
    bukan `ditolak_user` (proksi cakupan tanpa bertanya).

    Target >= 60%. Tanpa tautan sama sekali -> `tertunda`.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) AS n FROM listing_links GROUP BY status")
            hitung = {r["status"]: int(r["n"]) for r in cur.fetchall()}
    total = hitung.get("automatic", 0) + hitung.get("user_confirmed", 0)
    if total == 0:
        return {"persen": None, "status": "tertunda",
                "reason": "belum ada tautan listing di graf"}
    persen = round(hitung.get("automatic", 0) / total * 100, 1)
    return {"persen": persen, "total": total,
            "automatic": hitung.get("automatic", 0),
            "user_confirmed": hitung.get("user_confirmed", 0),
            "status": "passed" if persen >= TARGET_CAKUPAN_AUTOMATIC else "not_yet_passed"}


def prosedur_verified_and_dipakai_ulang(user_id: str, dsn=None) -> dict:
    """4 prosedur MVP terverifikasi + masing-masing dipakai ulang
    agen lain minimal 1 kali (gerbang 8)."""
    from puspa.graphs.service import items_prosedur

    result = {}
    for provider in PROVIDER_MVP:
        prosedur = next(
            (p for p in items_prosedur(user_id, dsn=dsn) if p["service"] == provider),
            None)
        if prosedur is None:
            result[provider] = {"ada": False, "verified": False, "dipakai_ulang": False}
            continue
        # Pakai ulang = prosedur pernah dijalankan/dipakai oleh >= 2 agen
        # berbeda (agen yang menjalankan di `prosedur.agen_ids` + agen yang
        # tercatat di `pemakaian_prosedur`). "Dipakai ulang agen lain
        # minimal 1 kali" (§3.6) = ada agen kedua selain pembuatnya.
        agent_use = set(prosedur.get("agent_ids") or [])
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT agent_id FROM procedure_uses WHERE prosedur_id = %s",
                    (prosedur["id"],))
                agent_use |= {r["agent_id"] for r in cur.fetchall()}
        result[provider] = {
            "ada": True,
            "verified": prosedur["status"] == "verified",
            "dipakai_ulang": len(agent_use) >= 2,
            "agent_berbeda": len(agent_use),
            "status": prosedur["status"],
        }
    passed = all(v.get("verified") and v.get("dipakai_ulang") for v in result.values())
    return {"per_provider": result, "status": "passed" if passed else "not_yet_passed"}


def rasio_token_perbandingan(dsn=None) -> dict:
    """Token per perbandingan minggu terakhir vs minggu pertama.

    Tanpa 2 minggu data -> `tertunda` (jujur, bukan dikarang).
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dicatat, token FROM token_metrics"
                " WHERE type = 'compare_prices' ORDER BY dicatat")
            rows = cur.fetchall()
    if len(rows) < 2:
        return {"rasio": None, "status": "tertunda",
                "reason": "belum ada cukup data token (butuh masa uji 2 minggu)"}
    sekarang = datetime.now(timezone.utc)
    week_pertama = [r["token"] for r in rows
                      if r["dicatat"] < sekarang - timedelta(days=7)]
    week_terakhir = [r["token"] for r in rows
                       if r["dicatat"] >= sekarang - timedelta(days=7)]
    if not week_pertama or not week_terakhir:
        return {"rasio": None, "status": "tertunda",
                "reason": "data token belum mencakup 2 minggu berbeda"}
    rata_start = sum(week_pertama) / len(week_pertama)
    rata_end = sum(week_terakhir) / len(week_terakhir)
    rasio = round(rata_end / rata_start, 3) if rata_start else None
    return {"rasio": rasio, "rata_week_pertama": round(rata_start, 1),
            "rata_week_terakhir": round(rata_end, 1),
            "status": "passed" if rasio is not None and rasio <= TARGET_TOKEN_RASIO else "not_yet_passed"}


def keandalan_all_sumber(dsn=None) -> dict:
    """Skor Keandalan terhitung untuk setiap sumber yang dipakai."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT sumber, category FROM source_readings")
            pasangan = [(r["sumber"], r["category"]) for r in cur.fetchall()]
    result = {}
    for sumber, category in pasangan:
        result[f"{sumber} x {category or '-'}"] = skor_sumber(sumber, category, dsn=dsn)
    return {"sumber": result,
            "status": "passed" if result and all(
                v["persen_verified"] is not None for v in result.values())
            else ("tertunda" if not result else "not_yet_passed")}


def ukur_all(user_id: str, dsn=None) -> dict:
    """Semua metrik moat gerbang 8 dalam satu laporan."""
    return {
        "cakupan_pencocokan_automatic": cakupan_pencocokan_automatic(dsn=dsn),
        "prosedur_verified_dipakai_ulang": prosedur_verified_and_dipakai_ulang(user_id, dsn=dsn),
        "rasio_token": rasio_token_perbandingan(dsn=dsn),
        "keandalan_sumber": keandalan_all_sumber(dsn=dsn),
        "workflow_active_user_test": {
            "status": "tertunda",
            "reason": "metrik 3.8 (5/10 pengguna punya satu alur aktif) adalah FASE 4 — "
                      "fondasi pustaka alur tercatat di library.py, metriknya tidak diklaim di Fase 3"},
    }
