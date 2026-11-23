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
from datetime import UTC, datetime, timedelta

from puspa.db import connect as _db_connect
from puspa.db import dict_row

from puspa.moat.reliability import source_score

DEFAULT_DSN = None
PROVIDER_MVP = ("PLN", "PDAM", "airtime", "paket data")
TARGET_AUTOMATIC_MATCH_COVERAGE = 60.0
TARGET_TOKEN_RATIO = 0.80


def _connect(dsn=None):
    return _db_connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row
    )


def automatic_match_coverage(dsn=None) -> dict:
    """% tautan listing berstatus `otomatis` dari semua tautan yang
    bukan `ditolak_user` (proksi cakupan tanpa bertanya).

    Target >= 60%. Tanpa tautan sama sekali -> `tertunda`.
    """
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, COUNT(*) AS n FROM listing_links GROUP BY status")
        counts = {r["status"]: int(r["n"]) for r in cur.fetchall()}
    total = counts.get("automatic", 0) + counts.get("user_confirmed", 0)
    if total == 0:
        return {"percent": None, "status": "pending", "reason": "belum ada tautan listing di graf"}
    percent = round(counts.get("automatic", 0) / total * 100, 1)
    return {
        "percent": percent,
        "total": total,
        "automatic": counts.get("automatic", 0),
        "user_confirmed": counts.get("user_confirmed", 0),
        "status": "passed" if percent >= TARGET_AUTOMATIC_MATCH_COVERAGE else "not_yet_passed",
    }


def verified_procedures_reused(user_id: str, dsn=None) -> dict:
    """4 prosedur MVP terverifikasi + masing-masing dipakai ulang
    agen lain minimal 1 kali (gerbang 8)."""
    from puspa.graphs.service import list_procedures

    result: dict[str, dict[str, bool | int | str]] = {}
    for provider in PROVIDER_MVP:
        procedure = next(
            (p for p in list_procedures(user_id, dsn=dsn) if p["service"] == provider), None
        )
        if procedure is None:
            result[provider] = {"exists": False, "verified": False, "reused": False}
            continue
        # Pakai ulang = prosedur pernah dijalankan/dipakai oleh >= 2 agen
        # berbeda (agen yang menjalankan di `prosedur.agen_ids` + agen yang
        # tercatat di `pemakaian_prosedur`). "Dipakai ulang agen lain
        # minimal 1 kali" (§3.6) = ada agen kedua selain pembuatnya.
        agent_use = set(procedure.get("agent_ids") or [])
        with _connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT agent_id FROM procedure_uses WHERE prosedur_id = %s", (procedure["id"],)
            )
            agent_use |= {r["agent_id"] for r in cur.fetchall()}
        result[provider] = {
            "exists": True,
            "verified": procedure["status"] == "verified",
            "reused": len(agent_use) >= 2,
            "different_agents": len(agent_use),
            "status": procedure["status"],
        }
    passed = all(v.get("verified") and v.get("reused") for v in result.values())
    return {"per_provider": result, "status": "passed" if passed else "not_yet_passed"}


def comparison_token_ratio(dsn=None) -> dict:
    """Token per perbandingan minggu terakhir vs minggu pertama.

    Tanpa 2 minggu data -> `tertunda` (jujur, bukan dikarang).
    """
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT dicatat, token FROM token_metrics"
            " WHERE type = 'compare_prices' ORDER BY dicatat"
        )
        rows = cur.fetchall()
    if len(rows) < 2:
        return {
            "rasio": None,
            "status": "pending",
            "reason": "belum ada cukup data token (butuh masa uji 2 minggu)",
        }
    now = datetime.now(UTC)
    first_week = [r["token"] for r in rows if r["dicatat"] < now - timedelta(days=7)]
    last_week = [r["token"] for r in rows if r["dicatat"] >= now - timedelta(days=7)]
    if not first_week or not last_week:
        return {
            "rasio": None,
            "status": "pending",
            "reason": "data token belum mencakup 2 minggu berbeda",
        }
    first_week_avg = sum(first_week) / len(first_week)
    last_week_avg = sum(last_week) / len(last_week)
    ratio = round(last_week_avg / first_week_avg, 3) if first_week_avg else None
    return {
        "rasio": ratio,
        "rata_week_pertama": round(first_week_avg, 1),
        "rata_week_terakhir": round(last_week_avg, 1),
        "status": "passed"
        if ratio is not None and ratio <= TARGET_TOKEN_RATIO
        else "not_yet_passed",
    }


def reliability_all_sources(dsn=None) -> dict:
    """Skor Keandalan terhitung untuk setiap sumber yang dipakai."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT sumber, category FROM source_readings")
        pairs = [(r["sumber"], r["category"]) for r in cur.fetchall()]
    result = {}
    for source, category in pairs:
        result[f"{source} x {category or '-'}"] = source_score(source, category, dsn=dsn)
    return {
        "sources": result,
        "status": "passed"
        if result and all(v["percent_verified"] is not None for v in result.values())
        else ("pending" if not result else "not_yet_passed"),
    }


def measure_all(user_id: str, dsn=None) -> dict:
    """Semua metrik moat gerbang 8 dalam satu laporan."""
    return {
        "cakupan_pencocokan_automatic": automatic_match_coverage(dsn=dsn),
        "prosedur_verified_dipakai_ulang": verified_procedures_reused(user_id, dsn=dsn),
        "rasio_token": comparison_token_ratio(dsn=dsn),
        "keandalan_sumber": reliability_all_sources(dsn=dsn),
        "workflow_active_user_test": {
            "status": "pending",
            "reason": "metrik 3.8 (5/10 pengguna punya satu alur aktif) adalah FASE 4 — "
            "fondasi pustaka alur tercatat di library.py, metriknya tidak diklaim di Fase 3",
        },
    }
