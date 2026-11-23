"""Cek syarat layanan sumber (keputusan 15, §10 revisi 7).

Hasil cek 17 Nov 2026 (dibaca dari halaman syarat resmi tiap sumber,
lihat STATUS_FASE3.md untuk kutipan pasal):

- Tokopedia: MELARANG robot/crawler/scraping (Syarat & Ketentuan
  B.12-13) — pembacaan otomatis tanpa izin dilarang.
- Shopee: MELARANG bot dan pemantauan/penyalinan otomatis tanpa
  izin tertulis (Ketentuan Layanan bag. 3) — jalur resmi yang
  diizinkan adalah Shopee Open Platform (butuh akun partner +
  otorisasi toko, bukan bagian MVP).
- Blibli: MELARANG pengambilan isi secara sistematis untuk menyusun
  database/kompilasi tanpa izin tertulis (pasal 2.2).
- PLN: cek tagihan adalah layanan resmi untuk pelanggan sendiri
  (layanan.pln.co.id, butuh login + captcha) — pembacaan otomatis
  lewat browser kemungkinan tertahan captcha; bukan larangan
  scraping eksplisit, tapi jalur otomatis tidak dijamin.

Konsekuensi kode (bukan sekadar catatan): pembacaan LIVE marketplace
DITAHAN oleh gerbang `boleh_baca` sampai Nero memutuskan lanjut atau
ganti sumber (keputusan 15). Kalau sumber memblokir, agen bilang
tidak bisa cek — TANPA upaya menembus blokir (§11). Pembacaan
fixture/situs uji lokal selalu diizinkan (bukan sumber pihak ketiga).
"""

from __future__ import annotations

import os
from datetime import date

from puspa.db import connect as _db_connect
from puspa.db import dict_row

DEFAULT_DSN = None

# Hasil cek yang dicatat (sumber -> ringkasan). `boleh_baca_live`
# False berarti gerbang menahan pembacaan live sampai keputusan Nero.
TOS_CHECK_RESULTS = {
    "Tokopedia": {
        "result": "forbids_automation",
        "clause": "Syarat & Ketentuan B.12-13: dilarang memakai robot, macro, crawler "
        "dan/atau perangkat otomatis untuk crawling/scraping tanpa izin Tokopedia.",
        "source_url": "https://www.tokopedia.com/help/article/terms-conditions",
    },
    "Shopee": {
        "result": "forbids_automation",
        "clause": "Ketentuan Layanan bag. 3: dilarang mengakses dengan bot/emulator dan "
        "memantau/menyalin otomatis tanpa izin tertulis; jalur resmi: Shopee Open Platform.",
        "source_url": "https://shopee.co.id/",
    },
    "Blibli": {
        "result": "forbids_automation",
        "clause": "Syarat & Ketentuan 2.2: dilarang mengambil isi secara sistematis untuk "
        "membuat/menyusun koleksi, kompilasi, database atau direktori tanpa izin tertulis.",
        "source_url": "https://www.blibli.com/faq/tentang-blibli/syarat-ketentuan/",
    },
    "PLN": {
        "result": "limited_official_service",
        "clause": "Cek tagihan layanan resmi pelanggan sendiri di layanan.pln.co.id; "
        "butuh login + captcha — jalur otomatis tidak dijamin, bukan larangan eksplisit.",
        "source_url": "https://layanan.pln.co.id/informasi-tagihan-listrik-pembelian-token-pln",
    },
    "PDAM": {
        "result": "limited_official_service",
        "clause": "Tiap daerah punya situs sendiri; cek tagihan layanan resmi pelanggan sendiri. "
        "MVP hanya PDAM milik Nero + pengguna uji (§4.2).",
        "source_url": "",
    },
    "airtime": {
        "result": "limited_official_service",
        "clause": "Cek pulsa/paket data lewat kanal resmi operator (aplikasi/UMB); "
        "tidak ada API publik umum. Jalur otomatis menunggu prosedur per operator.",
        "source_url": "",
    },
    "paket data": {
        "result": "limited_official_service",
        "clause": "Sama seperti pulsa: kanal resmi operator, tanpa API publik umum.",
        "source_url": "",
    },
}

# Gerbang baca live: hanya sumber dengan hasil `layanan_resmi_terbatas`
# yang boleh dibaca live (itu pun hanya atas permintaan user dan
# berfrekuensi rendah); sumber `melarang_otomatis` DITAHAN.
LIVE_READ_ALLOWED = {"PLN", "PDAM", "airtime", "paket data"}


def _connect(dsn=None):
    return _db_connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row
    )


def may_read(source: str, *, mode: str = "live") -> bool:
    """Gerbang baca (§11): True bila sumber boleh dibaca.

    `mode="fixture"` (situs uji lokal / data uji) selalu True.
    `mode="live"` mengikuti HASIL_CEK + keputusan Nero: sumber yang
    melarang otomatis ditahan sampai ada keputusan lanjut/ganti.
    """
    if mode == "fixture":
        return True
    if source not in TOS_CHECK_RESULTS:
        return False
    return source in LIVE_READ_ALLOWED


def record_result_check(dsn=None, checked_on: date | None = None) -> list[dict]:
    """Tulis hasil cek syarat layanan ke tabel `cek_syarat_layanan`."""
    checked_on = checked_on or date(2026, 11, 17)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            for source, content in TOS_CHECK_RESULTS.items():
                cur.execute(
                    "INSERT INTO tos_checks (sumber, result, pasal, source_url, checked_on)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (sumber) DO UPDATE SET result = EXCLUDED.result,"
                    " pasal = EXCLUDED.pasal, source_url = EXCLUDED.source_url,"
                    " checked_on = EXCLUDED.checked_on",
                    (
                        source,
                        content["result"],
                        content["clause"],
                        content["source_url"],
                        checked_on,
                    ),
                )
        conn.commit()
    return list_check_results(dsn=dsn)


def list_check_results(dsn=None) -> list[dict]:
    """Baca hasil cek syarat layanan yang tercatat."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM tos_checks ORDER BY sumber")
        return [
            {
                "source": r["sumber"],
                "result": r["result"],
                "clause": r["pasal"],
                "source_url": r["source_url"],
                "checked_on": r["checked_on"],
                "decision": r["keputusan"],
            }
            for r in cur.fetchall()
        ]
