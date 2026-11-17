"""Wedge 1 — bandingkan harga (§4.1 revisi 7).

Alur: agen membuat 3 Pembaca (satu per marketplace: Tokopedia,
Shopee, Blibli) yang bekerja paralel mengambil harga, ongkir, rating
toko; pencocokan lewat Graf Identitas (§3.2); 1 Pemeriksa membaca
ulang tiap angka (§2.5).

Gerbang yang ditegakkan di sini (bukan di reader):
1. Scope 9 kategori — di luar scope ditolak dengan peringatan jelas.
2. Syarat layanan (keputusan 15) — pembacaan LIVE marketplace yang
   melarang otomatis DITAHAN; agen bilang tidak bisa cek, tanpa
   upaya menembus blokir. Mode `fixture` (situs uji lokal / data uji)
   selalu diizinkan.
3. Pencocokan bertingkat — ragu -> tanya user, bukan tebak.
4. Angka yatim — tiap harga yang tampil punya bukti_id; harga tanpa
   bukti tidak ditulis ke graf dan tidak tampil.
5. Cache bukti bersama dipakai Pembaca saja; Pemeriksa membaca ulang
   independen (callable `baca_ulang` TIDAK memakai cache).

Paralelisme: 3 Pembaca dijalankan berurutan di sini karena pembaca
adalah I/O sinkron yang disuntikkan; pola sub-agen paralel (3 Pembaca
+ 1 Pemeriksa, maks 4 per agen) milik runtime Fase 2 (`SubagentPool`)
— `compare.py` adalah logika wedge yang dipanggil tiap sub-agen,
bukan pengganti kolam.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg

from puspa.evidence.evidence import catat_bukti
from puspa.evidence.verifier import periksa
from puspa.graphs.identity import (
    catat_harga, cocokkan_otomatis, harga_terkini, riwayat_harga,
    tautkan_listing)
from puspa.pricing import cache as cache_bukti
from puspa.pricing import credits as kredit
from puspa.pricing.catalog import di_luar_scope, penolakan_scope
from puspa.pricing.readers import HasilBaca
from puspa.pricing.tos import boleh_baca

MARKETPLACE = ("Tokopedia", "Shopee", "Blibli")
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def _catat_pembacaan(user_id: str, sumber: str, kategori: str, vonis: str, dsn=None) -> None:
    """Tulis satu pembacaan ke `pembacaan_sumber` (bahan Skor Keandalan)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pembacaan_sumber (user_id, sumber, kategori, vonis)"
                " VALUES (%s, %s, %s, %s)", (user_id, sumber, kategori, vonis))
        conn.commit()


def _catat_token(tugas_id: str, user_id: str, token: int, dsn=None) -> None:
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO metrik_token (tugas_id, user_id, jenis, token)"
                " VALUES (%s, %s, 'bandingkan_harga', %s)", (tugas_id, user_id, token))
        conn.commit()


def bandingkan_harga(*, user_id: str, agen_id: str, nomor_model: str,
                     merek: str | None = None, kategori: str,
                     listing_urls: dict[str, str],
                     pembaca: dict[str, object], baca_ulang,
                     tugas_id: str | None = None, token: int = 0,
                     mode: str = "live", dsn=None) -> dict:
    """Bandingkan harga satu barang di 3 marketplace.

    `pembaca`: mapping marketplace -> objek dengan `.baca(url)`.
    `baca_ulang`: callable Pemeriksa `baca_ulang(url) -> {"nilai", ...}`
    — membaca ulang LANGSUNG dari sumber (tanpa cache).
    `mode`: "live" (gerbang ToS berlaku) atau "fixture" (uji lokal).

    Kembalikan dict hasil dengan `hasil` per marketplace
    (harga/ongkir/rating/bukti_id/vonis atau `tidak_bisa_dicek` +
    alasan), `termurah`, `selisih`, dan `perlu_tanya` bila ragu.
    """
    tugas_id = tugas_id or f"tugas-{uuid.uuid4().hex[:12]}"
    if di_luar_scope(kategori):
        return {"sukses": False, "alasan": "di_luar_scope",
                "pesan": penolakan_scope(kategori)}
    cocok = cocokkan_otomatis(nomor_model=nomor_model, merek=merek, dsn=dsn)
    if cocok["perlu_tanya"]:
        return {"sukses": False, "alasan": "perlu_tanya",
                "pesan": "barang belum pasti cocok — pilih dari kandidat sebelum membandingkan",
                "kandidat": cocok["kandidat"], "perlu_tanya": True}
    produk = cocok["cocok"]
    hasil: dict[str, dict] = {}
    for pasar in MARKETPLACE:
        url = listing_urls.get(pasar)
        if not url:
            hasil[pasar] = {"tidak_bisa_dicek": True,
                            "alasan": "tautan_tidak_diberikan"}
            continue
        if mode == "live" and not boleh_baca(pasar, mode="live"):
            hasil[pasar] = {
                "tidak_bisa_dicek": True,
                "alasan": "syarat_layanan_menunggu_keputusan_nero",
                "pesan": f"{pasar} melarang pembacaan otomatis di syarat layanannya; "
                         "Puspa tidak membaca sampai Nero memutuskan lanjut atau ganti sumber "
                         "(keputusan 15)."}
            _catat_pembacaan(user_id, pasar, kategori, "Tidak bisa dipastikan", dsn)
            continue
        # Cache bukti bersama: hanya jalur Pembaca.
        kunci_cache = f"{pasar}:{url}"
        dari_cache = cache_bukti.ambil(kunci_cache, dsn=dsn) if mode == "live" else None
        if dari_cache is not None:
            nilai = dari_cache["nilai"]
            baca = HasilBaca(sukses=True, marketplace=pasar, listing_url=url,
                             harga=nilai.get("harga"), ongkir=nilai.get("ongkir", 0),
                             rating_toko=nilai.get("rating_toko"),
                             nama=nilai.get("nama", ""), potongan_teks="[DATA: cache]",
                             hash_halaman="")
            cache_hit = True
        else:
            baca = pembaca[pasar].baca(url)
            cache_hit = False
        if not baca.sukses or baca.harga is None:
            hasil[pasar] = {"tidak_bisa_dicek": True,
                            "alasan": baca.alasan or "tidak_bisa_dibaca",
                            "pesan": f"{pasar} tidak bisa dicek: {baca.alasan or 'tidak bisa dibaca'}"}
            _catat_pembacaan(user_id, pasar, kategori, "Tidak bisa dipastikan", dsn)
            continue
        # Bukti + Pemeriksa independen (baca ulang LANGSUNG, tanpa cache).
        bukti = catat_bukti(
            tugas_id=tugas_id, klaim=baca.nama or f"{merek or ''} {nomor_model}",
            nilai=baca.harga, satuan="Rp", sumber_url=url,
            potongan_teks=baca.potongan_teks or str(baca.harga),
            halaman="", id_pembaca=f"pembaca-{pasar.lower()}")
        bukti = periksa(bukti, baca_ulang=baca_ulang)
        # Skor Keandalan: sumber < 80% -> maksimal Belum pasti (§3.4).
        from puspa.moat.reliability import skor_sumber

        skor = skor_sumber(pasar, kategori, dsn=dsn)
        if skor["turunkan_vonis"] and bukti.vonis == "Terverifikasi":
            bukti.vonis = "Belum pasti"
        _catat_pembacaan(user_id, pasar, kategori, bukti.vonis, dsn)
        # Tulis ke graf (hanya hasil yang lolos periksa — vonis
        # "Tidak bisa dipastikan" tidak ditulis).
        bukti_id = bukti.bukti_id
        if bukti.vonis != "Tidak bisa dipastikan":
            tautan = tautkan_listing(produk_id=produk["id"], user_id=user_id,
                                     marketplace=pasar, listing_url=url, dsn=dsn)
            catat_harga(tautan_id=tautan["id"], user_id=user_id, harga=baca.harga,
                        ongkir=baca.ongkir or 0, rating_toko=baca.rating_toko,
                        stok=baca.stok, bukti_id=bukti_id, dsn=dsn)
            if not cache_hit:
                cache_bukti.simpan(kunci_cache, sumber=pasar,
                                   nilai={"harga": baca.harga, "ongkir": baca.ongkir or 0,
                                          "rating_toko": baca.rating_toko,
                                          "nama": baca.nama},
                                   bukti_id=bukti_id, dsn=dsn)
        hasil[pasar] = {
            "harga": baca.harga, "ongkir": baca.ongkir or 0,
            "rating_toko": baca.rating_toko, "listing_url": url,
            "bukti_id": bukti_id, "vonis": bukti.vonis,
            "waktu_ambil": bukti.waktu_ambil.isoformat(),
            "dari_cache": cache_hit,
        }
    harga_ok = {p: h["harga"] for p, h in hasil.items() if "harga" in h}
    termurah = min(harga_ok, key=harga_ok.get) if harga_ok else None
    selisih = (max(harga_ok.values()) - min(harga_ok.values())) if len(harga_ok) > 1 else 0
    _catat_token(tugas_id, user_id, token, dsn)
    return {"sukses": True, "produk": produk, "hasil": hasil,
            "termurah": termurah, "selisih": selisih,
            "tugas_id": tugas_id, "mode": mode}


def cek_detail(*, user_id: str, produk_id: str, dsn=None) -> dict:
    """Cek detail (riwayat 30 hari + harga terkini) = 1 kredit (§4.1).

    Kredit dipotong DULU; gagal -> KreditError, tidak ada angka yang
    tampil tanpa bukti (riwayat dari graf selalu ber-bukti_id).
    """
    sisa = kredit.pakai_kredit(user_id, dsn=dsn)
    return {"sisa_kredit": sisa,
            "harga_terkini": harga_terkini(produk_id, dsn=dsn),
            "riwayat_30_hari": riwayat_harga(produk_id, hari=30, dsn=dsn)}
