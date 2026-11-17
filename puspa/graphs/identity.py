"""Graf Identitas dan Harga (§3.2 revisi 7).

Dua lapisan di atas Postgres (§7: bukan graph DB eksternal):

- Lapisan identitas: produk kanonik (merek, seri, nomor model, varian
  ternormalisasi). Tiap listing ditautkan dengan status
  `otomatis` / `dikonfirmasi_user` / `ditolak_user`.
- Lapisan harga: deret waktu listing x waktu (harga, ongkir, rating
  toko, stok, bukti_id). TIDAK ADA angka tanpa bukti_id.

Pencocokan bertingkat (§3.2): (1) kunci model deterministik;
(2) kalau tidak pasti, tampilkan kandidat dan tanya user; (3) jawaban
user ditulis sebagai tautan `dikonfirmasi_user`; (4) TIDAK ADA
pencocokan tebakan yang senyap.

Pola yang diadaptasi (ditulis ulang, bukan disalin): LadybugDB (MIT,
fork hidup Kuzu) — pola graf embedded: node dan edge sebagai baris
relasional, bukan server graf terpisah. Di Puspa, node = produk
kanonik, edge = tautan listing; deret waktu = baris `harga_seri`.
Lihat REUSE.md.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

KATEGORI = (
    "phone", "laptop", "tablet", "smartwatch", "earphones/TWS", "TV",
    "game console", "camera", "branded home appliances",
)
MARKETPLACE = ("Tokopedia", "Shopee", "Blibli")
STATUS_TAUTAN = ("automatic", "user_confirmed", "user_rejected")


class IdentityError(Exception):
    """Graf identitas menolak masukan (kategori di luar scope, dsb.)."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def normalisasi_number_model(model_number: str) -> str:
    """Kunci model deterministik: huruf besar, tanpa spasi/strip/titik."""
    return re.sub(r"[^A-Z0-9]", "", str(model_number).upper())


def normalisasi_variant(variant: dict | None) -> dict:
    """Varian ternormalisasi: hanya kunci yang memengaruhi harga.

    Kunci yang dikenal: storage, warna, ram. Nilai dipangkas dan
    dilipat ke huruf kecil. Kunci lain dibuang (bukan ditebak).
    """
    variant = variant or {}
    logout = {}
    for key in ("storage", "warna", "ram"):
        if key in variant and variant[key] not in (None, ""):
            logout[key] = " ".join(str(variant[key]).lower().split())
    return logout


def _row_product(row) -> dict:
    return {"id": str(row["id"]), "brand": row["brand"], "series": row["series"],
            "model_number": row["model_number"], "category": row["category"],
            "variant": row["variant"], "created_at": row["created_at"]}


def _row_tautan(row) -> dict:
    return {"id": str(row["id"]), "product_id": str(row["product_id"]),
            "user_id": str(row["user_id"]), "marketplace": row["marketplace"],
            "listing_url": row["listing_url"], "status": row["status"],
            "created_at": row["created_at"]}


def add_product(*, brand: str, model_number: str, category: str,
                  series: str = "", variant: dict | None = None, dsn=None) -> dict:
    """Tambah (atau ambil yang sudah ada) produk kanonik.

    Kategori di luar 9 kategori §4.1 ditolak — fashion dan barang
    non-standar bukan scope wedge.
    """
    if category not in KATEGORI:
        raise IdentityError(
            f"kategori di luar scope wedge (9 kategori §4.1): {category!r}")
    if not brand or not model_number:
        raise IdentityError("merek dan model_number wajib diisi")
    key = normalisasi_number_model(model_number)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM canonical_products WHERE brand = %s AND model_number = %s",
                (brand, key))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO canonical_products (brand, series, model_number, category, variant)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (brand, series, key, category, Jsonb(normalisasi_variant(variant))))
                row = cur.fetchone()
        conn.commit()
    return _row_product(row)


def search_product(model_number: str, dsn=None) -> dict | None:
    """Cari produk kanonik lewat kunci model deterministik."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM canonical_products WHERE model_number = %s",
                (normalisasi_number_model(model_number),))
            row = cur.fetchone()
    return _row_product(row) if row else None


def kandidat_identitas(*, brand: str | None = None, series: str | None = None,
                       model_number: str | None = None, category: str | None = None,
                       dsn=None) -> list[dict]:
    """Kandidat identitas untuk ditampilkan ke user saat ragu (§3.2
    langkah 2). TIDAK PERNAH memilih sendiri — pemanggil yang bertanya.
    """
    syarat, value = [], []
    if model_number:
        syarat.append("model_number LIKE %s")
        value.append("%" + normalisasi_number_model(model_number) + "%")
    if brand:
        syarat.append("merek ILIKE %s")
        value.append("%" + brand + "%")
    if series:
        syarat.append("seri ILIKE %s")
        value.append("%" + series + "%")
    if category:
        syarat.append("kategori = %s")
        value.append(category)
    if not syarat:
        return []
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM canonical_products WHERE " + " AND ".join(syarat)
                + " ORDER BY brand, model_number LIMIT 10", value)
            return [_row_product(r) for r in cur.fetchall()]


def tautkan_listing(*, product_id: str, user_id: str, marketplace: str,
                    listing_url: str, status: str = "automatic", dsn=None) -> dict:
    """Tautkan listing ke produk kanonik dengan status tautan."""
    if marketplace not in MARKETPLACE:
        raise IdentityError(f"marketplace tidak dikenal: {marketplace!r}")
    if status not in STATUS_TAUTAN:
        raise IdentityError(f"status tautan tidak dikenal: {status!r}")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM listing_links WHERE marketplace = %s AND listing_url = %s",
                (marketplace, listing_url))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO listing_links (product_id, user_id, marketplace, listing_url, status)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (product_id, user_id, marketplace, listing_url, status))
                row = cur.fetchone()
        conn.commit()
    return _row_tautan(row)


def konfirmasi_tautan(tautan_id: str, *, user_id: str, accepted: bool, dsn=None) -> dict:
    """Jawaban user atas kandidat: tautan menjadi `dikonfirmasi_user`
    atau `ditolak_user` (§3.2 langkah 3)."""
    status = "user_confirmed" if accepted else "user_rejected"
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE listing_links SET status = %s WHERE id = %s AND user_id = %s RETURNING *",
                (status, tautan_id, user_id))
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"tautan tidak ditemukan: {tautan_id}")
        conn.commit()
    return _row_tautan(row)


def record_price(*, tautan_id: str, user_id: str, price, shipping=0,
                store_rating=None, stock: str = "unknown",
                evidence_id: str, created_at: datetime | None = None, dsn=None) -> dict:
    """Tulis satu titik deret waktu harga. Angka tanpa bukti_id DITOLAK
    (§3.2: tidak ada angka di graf tanpa bukti_id)."""
    if not evidence_id:
        raise IdentityError("harga tanpa evidence_id ditolak (angka yatim, §3.2)")
    try:
        price_f = float(price)
    except (TypeError, ValueError) as exc:
        raise IdentityError(f"harga tidak bisa di-parse: {price!r}") from exc
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO price_series (tautan_id, user_id, created_at, price, shipping,"
                " store_rating, stock, evidence_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id, created_at",
                (tautan_id, user_id, created_at or datetime.now(timezone.utc),
                 price_f, float(shipping), store_rating, stock, evidence_id))
            row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"]), "created_at": row["created_at"]}


def price_terkini(product_id: str, dsn=None) -> list[dict]:
    """Harga terkini tiap marketplace untuk satu produk kanonik."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (t.marketplace) t.marketplace, t.listing_url,"
                " t.status AS status_tautan, h.harga, h.ongkir, h.store_rating,"
                " h.stok, h.evidence_id, h.waktu"
                " FROM listing_links t JOIN price_series h ON h.tautan_id = t.id"
                " WHERE t.product_id = %s AND t.status <> 'user_rejected'"
                " ORDER BY t.marketplace, h.created_at DESC",
                (product_id,))
            return [{"marketplace": r["marketplace"], "listing_url": r["listing_url"],
                     "status_tautan": r["status_tautan"], "price": float(r["price"]),
                     "shipping": float(r["shipping"]),
                     "store_rating": float(r["store_rating"]) if r["store_rating"] is not None else None,
                     "stock": r["stock"], "evidence_id": r["evidence_id"],
                     "created_at": r["created_at"]} for r in cur.fetchall()]


def history_price(product_id: str, *, day: int = 30, dsn=None) -> list[dict]:
    """Riwayat harga 30 hari (API baca §3.2)."""
    sejak = datetime.now(timezone.utc) - timedelta(days=day)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.marketplace, h.created_at, h.price, h.shipping, h.store_rating, h.evidence_id"
                " FROM listing_links t JOIN price_series h ON h.tautan_id = t.id"
                " WHERE t.product_id = %s AND t.status <> 'user_rejected' AND h.created_at >= %s"
                " ORDER BY h.created_at ASC",
                (product_id, sejak))
            return [{"marketplace": r["marketplace"], "created_at": r["created_at"],
                     "price": float(r["price"]), "shipping": float(r["shipping"]),
                     "store_rating": float(r["store_rating"]) if r["store_rating"] is not None else None,
                     "evidence_id": r["evidence_id"]} for r in cur.fetchall()]


def cocokkan_automatic(*, model_number: str, brand: str | None = None,
                      dsn=None) -> dict:
    """Pencocokan bertingkat (§3.2).

    Kembalikan {"cocok": produk | None, "kandidat": [...],
    "perlu_tanya": bool}. Kunci model cocok persis -> otomatis.
    Tidak cocok -> kandidat untuk ditanyakan ke user. TIDAK ADA
    pencocokan tebakan yang senyap: `cocok` hanya terisi bila kunci
    model cocok persis.
    """
    product = search_product(model_number, dsn=dsn)
    if product is not None:
        return {"match": product, "kandidat": [], "perlu_ask": False}
    kandidat = kandidat_identitas(brand=brand, model_number=model_number, dsn=dsn)
    return {"match": None, "kandidat": kandidat, "perlu_ask": True}
