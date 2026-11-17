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
    "HP", "laptop", "tablet", "smartwatch", "earphone/TWS", "TV",
    "konsol game", "kamera", "peralatan rumah tangga bermerek",
)
MARKETPLACE = ("Tokopedia", "Shopee", "Blibli")
STATUS_TAUTAN = ("otomatis", "dikonfirmasi_user", "ditolak_user")


class IdentityError(Exception):
    """Graf identitas menolak masukan (kategori di luar scope, dsb.)."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def normalisasi_nomor_model(nomor_model: str) -> str:
    """Kunci model deterministik: huruf besar, tanpa spasi/strip/titik."""
    return re.sub(r"[^A-Z0-9]", "", str(nomor_model).upper())


def normalisasi_varian(varian: dict | None) -> dict:
    """Varian ternormalisasi: hanya kunci yang memengaruhi harga.

    Kunci yang dikenal: storage, warna, ram. Nilai dipangkas dan
    dilipat ke huruf kecil. Kunci lain dibuang (bukan ditebak).
    """
    varian = varian or {}
    keluar = {}
    for kunci in ("storage", "warna", "ram"):
        if kunci in varian and varian[kunci] not in (None, ""):
            keluar[kunci] = " ".join(str(varian[kunci]).lower().split())
    return keluar


def _row_produk(row) -> dict:
    return {"id": str(row["id"]), "merek": row["merek"], "seri": row["seri"],
            "nomor_model": row["nomor_model"], "kategori": row["kategori"],
            "varian": row["varian"], "dibuat": row["dibuat"]}


def _row_tautan(row) -> dict:
    return {"id": str(row["id"]), "produk_id": str(row["produk_id"]),
            "user_id": str(row["user_id"]), "marketplace": row["marketplace"],
            "listing_url": row["listing_url"], "status": row["status"],
            "dibuat": row["dibuat"]}


def tambah_produk(*, merek: str, nomor_model: str, kategori: str,
                  seri: str = "", varian: dict | None = None, dsn=None) -> dict:
    """Tambah (atau ambil yang sudah ada) produk kanonik.

    Kategori di luar 9 kategori §4.1 ditolak — fashion dan barang
    non-standar bukan scope wedge.
    """
    if kategori not in KATEGORI:
        raise IdentityError(
            f"kategori di luar scope wedge (9 kategori §4.1): {kategori!r}")
    if not merek or not nomor_model:
        raise IdentityError("merek dan nomor_model wajib diisi")
    kunci = normalisasi_nomor_model(nomor_model)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM produk_kanonik WHERE merek = %s AND nomor_model = %s",
                (merek, kunci))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO produk_kanonik (merek, seri, nomor_model, kategori, varian)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (merek, seri, kunci, kategori, Jsonb(normalisasi_varian(varian))))
                row = cur.fetchone()
        conn.commit()
    return _row_produk(row)


def cari_produk(nomor_model: str, dsn=None) -> dict | None:
    """Cari produk kanonik lewat kunci model deterministik."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM produk_kanonik WHERE nomor_model = %s",
                (normalisasi_nomor_model(nomor_model),))
            row = cur.fetchone()
    return _row_produk(row) if row else None


def kandidat_identitas(*, merek: str | None = None, seri: str | None = None,
                       nomor_model: str | None = None, kategori: str | None = None,
                       dsn=None) -> list[dict]:
    """Kandidat identitas untuk ditampilkan ke user saat ragu (§3.2
    langkah 2). TIDAK PERNAH memilih sendiri — pemanggil yang bertanya.
    """
    syarat, nilai = [], []
    if nomor_model:
        syarat.append("nomor_model LIKE %s")
        nilai.append("%" + normalisasi_nomor_model(nomor_model) + "%")
    if merek:
        syarat.append("merek ILIKE %s")
        nilai.append("%" + merek + "%")
    if seri:
        syarat.append("seri ILIKE %s")
        nilai.append("%" + seri + "%")
    if kategori:
        syarat.append("kategori = %s")
        nilai.append(kategori)
    if not syarat:
        return []
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM produk_kanonik WHERE " + " AND ".join(syarat)
                + " ORDER BY merek, nomor_model LIMIT 10", nilai)
            return [_row_produk(r) for r in cur.fetchall()]


def tautkan_listing(*, produk_id: str, user_id: str, marketplace: str,
                    listing_url: str, status: str = "otomatis", dsn=None) -> dict:
    """Tautkan listing ke produk kanonik dengan status tautan."""
    if marketplace not in MARKETPLACE:
        raise IdentityError(f"marketplace tidak dikenal: {marketplace!r}")
    if status not in STATUS_TAUTAN:
        raise IdentityError(f"status tautan tidak dikenal: {status!r}")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM tautan_listing WHERE marketplace = %s AND listing_url = %s",
                (marketplace, listing_url))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO tautan_listing (produk_id, user_id, marketplace, listing_url, status)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (produk_id, user_id, marketplace, listing_url, status))
                row = cur.fetchone()
        conn.commit()
    return _row_tautan(row)


def konfirmasi_tautan(tautan_id: str, *, user_id: str, diterima: bool, dsn=None) -> dict:
    """Jawaban user atas kandidat: tautan menjadi `dikonfirmasi_user`
    atau `ditolak_user` (§3.2 langkah 3)."""
    status = "dikonfirmasi_user" if diterima else "ditolak_user"
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tautan_listing SET status = %s WHERE id = %s AND user_id = %s RETURNING *",
                (status, tautan_id, user_id))
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"tautan tidak ditemukan: {tautan_id}")
        conn.commit()
    return _row_tautan(row)


def catat_harga(*, tautan_id: str, user_id: str, harga, ongkir=0,
                rating_toko=None, stok: str = "tidak_diketahui",
                bukti_id: str, waktu: datetime | None = None, dsn=None) -> dict:
    """Tulis satu titik deret waktu harga. Angka tanpa bukti_id DITOLAK
    (§3.2: tidak ada angka di graf tanpa bukti_id)."""
    if not bukti_id:
        raise IdentityError("harga tanpa bukti_id ditolak (angka yatim, §3.2)")
    try:
        harga_f = float(harga)
    except (TypeError, ValueError) as exc:
        raise IdentityError(f"harga tidak bisa di-parse: {harga!r}") from exc
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO harga_seri (tautan_id, user_id, waktu, harga, ongkir,"
                " rating_toko, stok, bukti_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id, waktu",
                (tautan_id, user_id, waktu or datetime.now(timezone.utc),
                 harga_f, float(ongkir), rating_toko, stok, bukti_id))
            row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"]), "waktu": row["waktu"]}


def harga_terkini(produk_id: str, dsn=None) -> list[dict]:
    """Harga terkini tiap marketplace untuk satu produk kanonik."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (t.marketplace) t.marketplace, t.listing_url,"
                " t.status AS status_tautan, h.harga, h.ongkir, h.rating_toko,"
                " h.stok, h.bukti_id, h.waktu"
                " FROM tautan_listing t JOIN harga_seri h ON h.tautan_id = t.id"
                " WHERE t.produk_id = %s AND t.status <> 'ditolak_user'"
                " ORDER BY t.marketplace, h.waktu DESC",
                (produk_id,))
            return [{"marketplace": r["marketplace"], "listing_url": r["listing_url"],
                     "status_tautan": r["status_tautan"], "harga": float(r["harga"]),
                     "ongkir": float(r["ongkir"]),
                     "rating_toko": float(r["rating_toko"]) if r["rating_toko"] is not None else None,
                     "stok": r["stok"], "bukti_id": r["bukti_id"],
                     "waktu": r["waktu"]} for r in cur.fetchall()]


def riwayat_harga(produk_id: str, *, hari: int = 30, dsn=None) -> list[dict]:
    """Riwayat harga 30 hari (API baca §3.2)."""
    sejak = datetime.now(timezone.utc) - timedelta(days=hari)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.marketplace, h.waktu, h.harga, h.ongkir, h.rating_toko, h.bukti_id"
                " FROM tautan_listing t JOIN harga_seri h ON h.tautan_id = t.id"
                " WHERE t.produk_id = %s AND t.status <> 'ditolak_user' AND h.waktu >= %s"
                " ORDER BY h.waktu ASC",
                (produk_id, sejak))
            return [{"marketplace": r["marketplace"], "waktu": r["waktu"],
                     "harga": float(r["harga"]), "ongkir": float(r["ongkir"]),
                     "rating_toko": float(r["rating_toko"]) if r["rating_toko"] is not None else None,
                     "bukti_id": r["bukti_id"]} for r in cur.fetchall()]


def cocokkan_otomatis(*, nomor_model: str, merek: str | None = None,
                      dsn=None) -> dict:
    """Pencocokan bertingkat (§3.2).

    Kembalikan {"cocok": produk | None, "kandidat": [...],
    "perlu_tanya": bool}. Kunci model cocok persis -> otomatis.
    Tidak cocok -> kandidat untuk ditanyakan ke user. TIDAK ADA
    pencocokan tebakan yang senyap: `cocok` hanya terisi bila kunci
    model cocok persis.
    """
    produk = cari_produk(nomor_model, dsn=dsn)
    if produk is not None:
        return {"cocok": produk, "kandidat": [], "perlu_tanya": False}
    kandidat = kandidat_identitas(merek=merek, nomor_model=nomor_model, dsn=dsn)
    return {"cocok": None, "kandidat": kandidat, "perlu_tanya": True}
