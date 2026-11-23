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
from typing import Protocol

from puspa.db import connect as _db_connect
from puspa.evidence.evidence import record_evidence
from puspa.evidence.verifier import verify
from puspa.graphs.identity import (
    current_prices,
    history_price,
    link_listing,
    match_automatic,
    record_price,
)
from puspa.pricing import cache as cache_evidence
from puspa.pricing import credits as credit
from puspa.pricing.catalog import out_of_scope, scope_rejection_message
from puspa.pricing.readers import ResultRead
from puspa.pricing.tos import may_read

MARKETPLACE = ("Tokopedia", "Shopee", "Blibli")
DEFAULT_DSN = None


class _ListingReader(Protocol):
    """Apa pun yang bisa membaca satu listing: HttpReader,
    PlaywrightReader, atau reader palsu di tes."""

    def read(self, listing_url: str) -> ResultRead: ...


def _record_reading(user_id: str, source: str, category: str, verdict: str, dsn=None) -> None:
    """Tulis satu pembacaan ke `pembacaan_sumber` (bahan Skor Keandalan)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with _db_connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO source_readings (user_id, sumber, category, vonis)"
                " VALUES (%s, %s, %s, %s)",
                (user_id, source, category, verdict),
            )
        conn.commit()


def _record_token(task_id: str, user_id: str, token: int, dsn=None) -> None:
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with _db_connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO token_metrics (task_id, user_id, type, token)"
                " VALUES (%s, %s, 'compare_prices', %s)",
                (task_id, user_id, token),
            )
        conn.commit()


def compare_price(
    *,
    user_id: str,
    agent_id: str,
    model_number: str,
    brand: str | None = None,
    category: str,
    listing_urls: dict[str, str],
    reader: dict[str, _ListingReader],
    reread,
    task_id: str | None = None,
    token: int = 0,
    mode: str = "live",
    dsn=None,
) -> dict:
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
    if out_of_scope(category):
        return {
            "success": False,
            "reason": "di_luar_scope",
            "message": scope_rejection_message(category),
        }
    match = match_automatic(model_number=model_number, brand=brand, dsn=dsn)
    if match["perlu_ask"]:
        return {
            "success": False,
            "reason": "perlu_ask",
            "message": "barang belum pasti cocok — pilih dari kandidat sebelum membandingkan",
            "kandidat": match["kandidat"],
            "perlu_ask": True,
        }
    product = match["match"]
    result: dict[str, dict] = {}
    for marketplace in MARKETPLACE:
        url = listing_urls.get(marketplace)
        if not url:
            result[marketplace] = {"not_can_dicek": True, "reason": "tautan_not_granted"}
            continue
        if mode == "live" and not may_read(marketplace, mode="live"):
            result[marketplace] = {
                "not_can_dicek": True,
                "reason": "syarat_service_pending_keputusan_nero",
                "message": f"{marketplace} melarang pembacaan otomatis di syarat layanannya; "
                "Puspa tidak membaca sampai Nero memutuskan lanjut atau ganti sumber "
                "(keputusan 15).",
            }
            _record_reading(user_id, marketplace, category, "Cannot be determined", dsn)
            continue
        # Cache bukti bersama: hanya jalur Pembaca.
        cache_key = f"{marketplace}:{url}"
        from_cache = cache_evidence.take(cache_key, dsn=dsn) if mode == "live" else None
        if from_cache is not None:
            value = from_cache["value"]
            read = ResultRead(
                success=True,
                marketplace=marketplace,
                listing_url=url,
                price=value.get("price"),
                shipping=value.get("shipping", 0),
                store_rating=value.get("store_rating"),
                name=value.get("name", ""),
                text_excerpt="[DATA: cache]",
                page_hash="",
            )
            cache_hit = True
        else:
            read = reader[marketplace].read(url)
            cache_hit = False
        if not read.success or read.price is None:
            result[marketplace] = {
                "not_can_dicek": True,
                "reason": read.reason or "not_can_dibaca",
                "message": f"{marketplace} tidak bisa dicek: {read.reason or 'tidak bisa dibaca'}",
            }
            _record_reading(user_id, marketplace, category, "Cannot be determined", dsn)
            continue
        # Bukti + Pemeriksa independen (baca ulang LANGSUNG, tanpa cache).
        evidence = record_evidence(
            task_id=task_id,
            claim=read.name or f"{brand or ''} {model_number}",
            value=read.price,
            unit="Rp",
            source_url=url,
            text_excerpt=read.text_excerpt or str(read.price),
            page="",
            reader_id=f"pembaca-{marketplace.lower()}",
        )
        evidence = verify(evidence, reread=reread)
        # Skor Keandalan: sumber < 80% -> maksimal Belum pasti (§3.4).
        from puspa.moat.reliability import source_score

        score = source_score(marketplace, category, dsn=dsn)
        if score["downgrade_verdict"] and evidence.verdict == "Verified":
            evidence.verdict = "Uncertain"
        _record_reading(user_id, marketplace, category, evidence.verdict, dsn)
        # Tulis ke graf (hanya hasil yang lolos periksa — vonis
        # "Tidak bisa dipastikan" tidak ditulis).
        evidence_id = evidence.evidence_id
        if evidence.verdict != "Cannot be determined":
            tautan = link_listing(
                product_id=product["id"],
                user_id=user_id,
                marketplace=marketplace,
                listing_url=url,
                dsn=dsn,
            )
            record_price(
                link_id=tautan["id"],
                user_id=user_id,
                price=read.price,
                shipping=read.shipping or 0,
                store_rating=read.store_rating,
                stock=read.stock,
                evidence_id=evidence_id,
                dsn=dsn,
            )
            if not cache_hit:
                cache_evidence.save(
                    cache_key,
                    source=marketplace,
                    value={
                        "price": read.price,
                        "shipping": read.shipping or 0,
                        "store_rating": read.store_rating,
                        "name": read.name,
                    },
                    evidence_id=evidence_id,
                    dsn=dsn,
                )
        result[marketplace] = {
            "price": read.price,
            "shipping": read.shipping or 0,
            "store_rating": read.store_rating,
            "listing_url": url,
            "evidence_id": evidence_id,
            "verdict": evidence.verdict,
            "time_take": evidence.time_take.isoformat(),
            "dari_cache": cache_hit,
        }
    price_ok = {p: h["price"] for p, h in result.items() if "price" in h}
    cheapest = min(price_ok, key=lambda p: price_ok[p]) if price_ok else None
    spread = (max(price_ok.values()) - min(price_ok.values())) if len(price_ok) > 1 else 0
    _record_token(task_id, user_id, token, dsn)
    return {
        "success": True,
        "product": product,
        "result": result,
        "cheapest": cheapest,
        "spread": spread,
        "task_id": task_id,
        "mode": mode,
    }


def check_detail(*, user_id: str, product_id: str, dsn=None) -> dict:
    """Cek detail (riwayat 30 hari + harga terkini) = 1 kredit (§4.1).

    Kredit dipotong DULU; gagal -> KreditError, tidak ada angka yang
    tampil tanpa bukti (riwayat dari graf selalu ber-bukti_id).
    """
    remaining = credit.use_credit(user_id, dsn=dsn)
    return {
        "remaining_credit": remaining,
        "price_terkini": current_prices(product_id, dsn=dsn),
        "history_30_day": history_price(product_id, day=30, dsn=dsn),
    }
