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

from puspa.evidence.evidence import record_evidence
from puspa.evidence.verifier import periksa
from puspa.graphs.identity import (
    record_price, cocokkan_automatic, price_terkini, history_price,
    tautkan_listing)
from puspa.pricing import cache as cache_evidence
from puspa.pricing import credits as credit
from puspa.pricing.catalog import di_luar_scope, penolakan_scope
from puspa.pricing.readers import ResultRead
from puspa.pricing.tos import boleh_read

MARKETPLACE = ("Tokopedia", "Shopee", "Blibli")
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def _record_pembacaan(user_id: str, sumber: str, category: str, vonis: str, dsn=None) -> None:
    """Tulis satu pembacaan ke `pembacaan_sumber` (bahan Skor Keandalan)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO source_readings (user_id, sumber, category, vonis)"
                " VALUES (%s, %s, %s, %s)", (user_id, sumber, category, vonis))
        conn.commit()


def _record_token(task_id: str, user_id: str, token: int, dsn=None) -> None:
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO token_metrics (task_id, user_id, type, token)"
                " VALUES (%s, %s, 'compare_prices', %s)", (task_id, user_id, token))
        conn.commit()


def compare_price(*, user_id: str, agent_id: str, model_number: str,
                     brand: str | None = None, category: str,
                     listing_urls: dict[str, str],
                     pembaca: dict[str, object], reread,
                     task_id: str | None = None, token: int = 0,
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
    task_id = task_id or f"tugas-{uuid.uuid4().hex[:12]}"
    if di_luar_scope(category):
        return {"success": False, "reason": "di_luar_scope",
                "message": penolakan_scope(category)}
    match = cocokkan_automatic(model_number=model_number, brand=brand, dsn=dsn)
    if match["perlu_ask"]:
        return {"success": False, "reason": "perlu_ask",
                "message": "barang belum pasti cocok — pilih dari kandidat sebelum membandingkan",
                "kandidat": match["kandidat"], "perlu_ask": True}
    product = match["match"]
    result: dict[str, dict] = {}
    for pasar in MARKETPLACE:
        url = listing_urls.get(pasar)
        if not url:
            result[pasar] = {"not_can_dicek": True,
                            "reason": "tautan_not_granted"}
            continue
        if mode == "live" and not boleh_read(pasar, mode="live"):
            result[pasar] = {
                "not_can_dicek": True,
                "reason": "syarat_service_pending_keputusan_nero",
                "message": f"{pasar} melarang pembacaan otomatis di syarat layanannya; "
                         "Puspa tidak membaca sampai Nero memutuskan lanjut atau ganti sumber "
                         "(keputusan 15)."}
            _record_pembacaan(user_id, pasar, category, "Cannot be determined", dsn)
            continue
        # Cache bukti bersama: hanya jalur Pembaca.
        key_cache = f"{pasar}:{url}"
        dari_cache = cache_evidence.take(key_cache, dsn=dsn) if mode == "live" else None
        if dari_cache is not None:
            value = dari_cache["value"]
            read = ResultRead(success=True, marketplace=pasar, listing_url=url,
                             price=value.get("price"), shipping=value.get("shipping", 0),
                             store_rating=value.get("store_rating"),
                             name=value.get("name", ""), text_excerpt="[DATA: cache]",
                             page_hash="")
            cache_hit = True
        else:
            read = pembaca[pasar].read(url)
            cache_hit = False
        if not read.success or read.price is None:
            result[pasar] = {"not_can_dicek": True,
                            "reason": read.reason or "not_can_dibaca",
                            "message": f"{pasar} tidak bisa dicek: {read.reason or 'tidak bisa dibaca'}"}
            _record_pembacaan(user_id, pasar, category, "Cannot be determined", dsn)
            continue
        # Bukti + Pemeriksa independen (baca ulang LANGSUNG, tanpa cache).
        evidence = record_evidence(
            task_id=task_id, klaim=read.name or f"{brand or ''} {model_number}",
            value=read.price, unit="Rp", sumber_url=url,
            text_excerpt=read.text_excerpt or str(read.price),
            halaman="", reader_id=f"pembaca-{pasar.lower()}")
        evidence = periksa(evidence, reread=reread)
        # Skor Keandalan: sumber < 80% -> maksimal Belum pasti (§3.4).
        from puspa.moat.reliability import skor_sumber

        skor = skor_sumber(pasar, category, dsn=dsn)
        if skor["turunkan_vonis"] and evidence.vonis == "Verified":
            evidence.vonis = "Uncertain"
        _record_pembacaan(user_id, pasar, category, evidence.vonis, dsn)
        # Tulis ke graf (hanya hasil yang lolos periksa — vonis
        # "Tidak bisa dipastikan" tidak ditulis).
        evidence_id = evidence.evidence_id
        if evidence.vonis != "Cannot be determined":
            tautan = tautkan_listing(product_id=product["id"], user_id=user_id,
                                     marketplace=pasar, listing_url=url, dsn=dsn)
            record_price(tautan_id=tautan["id"], user_id=user_id, price=read.price,
                        shipping=read.shipping or 0, store_rating=read.store_rating,
                        stock=read.stock, evidence_id=evidence_id, dsn=dsn)
            if not cache_hit:
                cache_evidence.save(key_cache, sumber=pasar,
                                   value={"price": read.price, "shipping": read.shipping or 0,
                                          "store_rating": read.store_rating,
                                          "name": read.name},
                                   evidence_id=evidence_id, dsn=dsn)
        result[pasar] = {
            "price": read.price, "shipping": read.shipping or 0,
            "store_rating": read.store_rating, "listing_url": url,
            "evidence_id": evidence_id, "vonis": evidence.vonis,
            "time_take": evidence.time_take.isoformat(),
            "dari_cache": cache_hit,
        }
    price_ok = {p: h["price"] for p, h in result.items() if "price" in h}
    termurah = min(price_ok, key=price_ok.get) if price_ok else None
    selisih = (max(price_ok.values()) - min(price_ok.values())) if len(price_ok) > 1 else 0
    _record_token(task_id, user_id, token, dsn)
    return {"success": True, "product": product, "result": result,
            "termurah": termurah, "selisih": selisih,
            "task_id": task_id, "mode": mode}


def check_detail(*, user_id: str, product_id: str, dsn=None) -> dict:
    """Cek detail (riwayat 30 hari + harga terkini) = 1 kredit (§4.1).

    Kredit dipotong DULU; gagal -> KreditError, tidak ada angka yang
    tampil tanpa bukti (riwayat dari graf selalu ber-bukti_id).
    """
    sisa = credit.use_credit(user_id, dsn=dsn)
    return {"sisa_credit": sisa,
            "price_terkini": price_terkini(product_id, dsn=dsn),
            "history_30_day": history_price(product_id, day=30, dsn=dsn)}
