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

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

# Hasil cek yang dicatat (sumber -> ringkasan). `boleh_baca_live`
# False berarti gerbang menahan pembacaan live sampai keputusan Nero.
HASIL_CEK = {
    "Tokopedia": {
        "hasil": "melarang_otomatis",
        "pasal": "Syarat & Ketentuan B.12-13: dilarang memakai robot, macro, crawler "
                 "dan/atau perangkat otomatis untuk crawling/scraping tanpa izin Tokopedia.",
        "url_sumber": "https://www.tokopedia.com/help/article/terms-conditions",
    },
    "Shopee": {
        "hasil": "melarang_otomatis",
        "pasal": "Ketentuan Layanan bag. 3: dilarang mengakses dengan bot/emulator dan "
                 "memantau/menyalin otomatis tanpa izin tertulis; jalur resmi: Shopee Open Platform.",
        "url_sumber": "https://shopee.co.id/",
    },
    "Blibli": {
        "hasil": "melarang_otomatis",
        "pasal": "Syarat & Ketentuan 2.2: dilarang mengambil isi secara sistematis untuk "
                 "membuat/menyusun koleksi, kompilasi, database atau direktori tanpa izin tertulis.",
        "url_sumber": "https://www.blibli.com/faq/tentang-blibli/syarat-ketentuan/",
    },
    "PLN": {
        "hasil": "layanan_resmi_terbatas",
        "pasal": "Cek tagihan layanan resmi pelanggan sendiri di layanan.pln.co.id; "
                 "butuh login + captcha — jalur otomatis tidak dijamin, bukan larangan eksplisit.",
        "url_sumber": "https://layanan.pln.co.id/informasi-tagihan-listrik-pembelian-token-pln",
    },
    "PDAM": {
        "hasil": "layanan_resmi_terbatas",
        "pasal": "Tiap daerah punya situs sendiri; cek tagihan layanan resmi pelanggan sendiri. "
                 "MVP hanya PDAM milik Nero + pengguna uji (§4.2).",
        "url_sumber": "",
    },
    "pulsa": {
        "hasil": "layanan_resmi_terbatas",
        "pasal": "Cek pulsa/paket data lewat kanal resmi operator (aplikasi/UMB); "
                 "tidak ada API publik umum. Jalur otomatis menunggu prosedur per operator.",
        "url_sumber": "",
    },
    "paket data": {
        "hasil": "layanan_resmi_terbatas",
        "pasal": "Sama seperti pulsa: kanal resmi operator, tanpa API publik umum.",
        "url_sumber": "",
    },
}

# Gerbang baca live: hanya sumber dengan hasil `layanan_resmi_terbatas`
# yang boleh dibaca live (itu pun hanya atas permintaan user dan
# berfrekuensi rendah); sumber `melarang_otomatis` DITAHAN.
BOLEH_BACA_LIVE = {"PLN", "PDAM", "pulsa", "paket data"}


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def boleh_baca(sumber: str, *, mode: str = "live") -> bool:
    """Gerbang baca (§11): True bila sumber boleh dibaca.

    `mode="fixture"` (situs uji lokal / data uji) selalu True.
    `mode="live"` mengikuti HASIL_CEK + keputusan Nero: sumber yang
    melarang otomatis ditahan sampai ada keputusan lanjut/ganti.
    """
    if mode == "fixture":
        return True
    if sumber not in HASIL_CEK:
        return False
    return sumber in BOLEH_BACA_LIVE


def catat_hasil_cek(dsn=None, tanggal: date | None = None) -> list[dict]:
    """Tulis hasil cek syarat layanan ke tabel `cek_syarat_layanan`."""
    tanggal = tanggal or date(2026, 11, 17)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            for sumber, isi in HASIL_CEK.items():
                cur.execute(
                    "INSERT INTO cek_syarat_layanan (sumber, hasil, pasal, url_sumber, tanggal_cek)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (sumber) DO UPDATE SET hasil = EXCLUDED.hasil,"
                    " pasal = EXCLUDED.pasal, url_sumber = EXCLUDED.url_sumber,"
                    " tanggal_cek = EXCLUDED.tanggal_cek",
                    (sumber, isi["hasil"], isi["pasal"], isi["url_sumber"], tanggal))
        conn.commit()
    return daftar_hasil_cek(dsn=dsn)


def daftar_hasil_cek(dsn=None) -> list[dict]:
    """Baca hasil cek syarat layanan yang tercatat."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM cek_syarat_layanan ORDER BY sumber")
            return [{"sumber": r["sumber"], "hasil": r["hasil"], "pasal": r["pasal"],
                     "url_sumber": r["url_sumber"], "tanggal_cek": r["tanggal_cek"],
                     "keputusan": r["keputusan"]} for r in cur.fetchall()]
