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

Retrieval dual-level (pola LightRAG, MIT, ditulis ulang, bukan
disalin): pencarian produk menggabungkan dua level, entitas
spesifik (nomor model) dan konsep abstrak (merek, seri, kategori),
di atas Postgres + pgvector. `rank_products` murni (in-memory);
`search_products_dual` adalah jalur database yang sama. Aturan
bukti tetap: tidak ada fakta graf yang ditampilkan tanpa proof
id. Setiap hasil membawa `evidence_ids` (id tautan listing dan
id bukti harga); fakta harga hanya dimuat bila `evidence_id`
ada, yang sudah ditegakkan `record_price`.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta

from puspa.db import Jsonb, dict_row
from puspa.db import connect as _db_connect
from puspa.memory.episodic import extract_concepts, extract_entities

DEFAULT_DSN = None

CATEGORIES = (
    "phone",
    "laptop",
    "tablet",
    "smartwatch",
    "earphones/TWS",
    "TV",
    "game console",
    "camera",
    "branded home appliances",
)
MARKETPLACE = ("Tokopedia", "Shopee", "Blibli")
LINK_STATUSES = ("automatic", "user_confirmed", "user_rejected")


class IdentityError(Exception):
    """Graf identitas menolak masukan (kategori di luar scope, dsb.)."""


def _connect(dsn=None):
    return _db_connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def normalize_model_number(model_number: str) -> str:
    """Kunci model deterministik: huruf besar, tanpa spasi/strip/titik."""
    return re.sub(r"[^A-Z0-9]", "", str(model_number).upper())


def normalize_variant(variant: dict | None) -> dict:
    """Varian ternormalisasi: hanya kunci yang memengaruhi harga.

    Kunci yang dikenal: storage, warna, ram. Nilai dipangkas dan
    dilipat ke huruf kecil. Kunci lain dibuang (bukan ditebak).
    """
    variant = variant or {}
    output = {}
    for key in ("storage", "color", "ram"):
        if key in variant and variant[key] not in (None, ""):
            output[key] = " ".join(str(variant[key]).lower().split())
    return output


def _row_product(row) -> dict:
    return {
        "id": str(row["id"]),
        "brand": row["brand"],
        "series": row["series"],
        "model_number": row["model_number"],
        "category": row["category"],
        "variant": row["variant"],
        "created_at": row["created_at"],
    }


def _row_link(row) -> dict:
    return {
        "id": str(row["id"]),
        "product_id": str(row["product_id"]),
        "user_id": str(row["user_id"]),
        "marketplace": row["marketplace"],
        "listing_url": row["listing_url"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def add_product(
    *,
    brand: str,
    model_number: str,
    category: str,
    series: str = "",
    variant: dict | None = None,
    dsn=None,
) -> dict:
    """Tambah (atau ambil yang sudah ada) produk kanonik.

    Kategori di luar 9 kategori §4.1 ditolak — fashion dan barang
    non-standar bukan scope wedge.
    """
    if category not in CATEGORIES:
        raise IdentityError(f"kategori di luar scope wedge (9 kategori §4.1): {category!r}")
    if not brand or not model_number:
        raise IdentityError("merek dan model_number wajib diisi")
    key = normalize_model_number(model_number)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM canonical_products WHERE brand = %s AND model_number = %s",
                (brand, key),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO canonical_products (brand, series, model_number, category, variant)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (brand, series, key, category, Jsonb(normalize_variant(variant))),
                )
                row = cur.fetchone()
        conn.commit()
    return _row_product(row)


def search_product(model_number: str, dsn=None) -> dict | None:
    """Cari produk kanonik lewat kunci model deterministik."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM canonical_products WHERE model_number = %s",
            (normalize_model_number(model_number),),
        )
        row = cur.fetchone()
    return _row_product(row) if row else None


def identity_candidates(
    *,
    brand: str | None = None,
    series: str | None = None,
    model_number: str | None = None,
    category: str | None = None,
    dsn=None,
) -> list[dict]:
    """Kandidat identitas untuk ditampilkan ke user saat ragu (§3.2
    langkah 2). TIDAK PERNAH memilih sendiri — pemanggil yang bertanya.
    """
    conditions, values = [], []
    if model_number:
        conditions.append("model_number LIKE %s")
        values.append("%" + normalize_model_number(model_number) + "%")
    if brand:
        conditions.append("brand ILIKE %s")
        values.append("%" + brand + "%")
    if series:
        conditions.append("series ILIKE %s")
        values.append("%" + series + "%")
    if category:
        conditions.append("category = %s")
        values.append(category)
    if not conditions:
        return []
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM canonical_products WHERE "
            + " AND ".join(conditions)
            + " ORDER BY brand, model_number LIMIT 10",
            values,
        )
        return [_row_product(r) for r in cur.fetchall()]


def link_listing(
    *,
    product_id: str,
    user_id: str,
    marketplace: str,
    listing_url: str,
    status: str = "automatic",
    dsn=None,
) -> dict:
    """Tautkan listing ke produk kanonik dengan status tautan."""
    if marketplace not in MARKETPLACE:
        raise IdentityError(f"marketplace tidak dikenal: {marketplace!r}")
    if status not in LINK_STATUSES:
        raise IdentityError(f"status tautan tidak dikenal: {status!r}")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM listing_links WHERE marketplace = %s AND listing_url = %s",
                (marketplace, listing_url),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO listing_links (product_id, user_id, marketplace, listing_url, status)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (product_id, user_id, marketplace, listing_url, status),
                )
                row = cur.fetchone()
        conn.commit()
    return _row_link(row)


def confirm_link(link_id: str, *, user_id: str, accepted: bool, dsn=None) -> dict:
    """Jawaban user atas kandidat: tautan menjadi `dikonfirmasi_user`
    atau `ditolak_user` (§3.2 langkah 3)."""
    status = "user_confirmed" if accepted else "user_rejected"
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE listing_links SET status = %s WHERE id = %s AND user_id = %s RETURNING *",
                (status, link_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"tautan tidak ditemukan: {link_id}")
        conn.commit()
    return _row_link(row)


def record_price(
    *,
    link_id: str,
    user_id: str,
    price,
    shipping=0,
    store_rating=None,
    stock: str = "unknown",
    evidence_id: str,
    created_at: datetime | None = None,
    dsn=None,
) -> dict:
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
                (
                    link_id,
                    user_id,
                    created_at or datetime.now(UTC),
                    price_f,
                    float(shipping),
                    store_rating,
                    stock,
                    evidence_id,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"]), "created_at": row["created_at"]}


def current_prices(product_id: str, dsn=None) -> list[dict]:
    """Harga terkini tiap marketplace untuk satu produk kanonik."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT marketplace, listing_url, link_status, price,"
            " shipping, store_rating, stock, evidence_id, created_at"
            " FROM (SELECT t.marketplace AS marketplace,"
            " t.listing_url AS listing_url,"
            " t.status AS link_status, h.price AS price,"
            " h.shipping AS shipping, h.store_rating AS store_rating,"
            " h.stock AS stock, h.evidence_id AS evidence_id,"
            " h.created_at AS created_at,"
            " ROW_NUMBER() OVER (PARTITION BY t.marketplace"
            " ORDER BY h.created_at DESC) AS rn"
            " FROM listing_links t JOIN price_series h ON h.tautan_id = t.id"
            " WHERE t.product_id = %s AND t.status <> 'user_rejected'"
            ") WHERE rn = 1",
            (product_id,),
        )
        return [
            {
                "marketplace": r["marketplace"],
                "listing_url": r["listing_url"],
                "link_status": r["link_status"],
                "price": float(r["price"]),
                "shipping": float(r["shipping"]),
                "store_rating": float(r["store_rating"]) if r["store_rating"] is not None else None,
                "stock": r["stock"],
                "evidence_id": r["evidence_id"],
                "created_at": r["created_at"],
            }
            for r in cur.fetchall()
        ]


def history_price(product_id: str, *, day: int = 30, dsn=None) -> list[dict]:
    """Riwayat harga 30 hari (API baca §3.2)."""
    since = datetime.now(UTC) - timedelta(days=day)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.marketplace, h.created_at, h.price, h.shipping, h.store_rating, h.evidence_id"
            " FROM listing_links t JOIN price_series h ON h.tautan_id = t.id"
            " WHERE t.product_id = %s AND t.status <> 'user_rejected' AND h.created_at >= %s"
            " ORDER BY h.created_at ASC",
            (product_id, since),
        )
        return [
            {
                "marketplace": r["marketplace"],
                "created_at": r["created_at"],
                "price": float(r["price"]),
                "shipping": float(r["shipping"]),
                "store_rating": float(r["store_rating"]) if r["store_rating"] is not None else None,
                "evidence_id": r["evidence_id"],
            }
            for r in cur.fetchall()
        ]


def match_automatic(*, model_number: str, brand: str | None = None, dsn=None) -> dict:
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
    candidates = identity_candidates(brand=brand, model_number=model_number, dsn=dsn)
    return {"match": None, "kandidat": candidates, "perlu_ask": True}


# --- Dual-level retrieval (LightRAG pattern, rewritten) ------------


def rank_products(products, query):
    """Rank canonical products by dual-level retrieval (pure).

    `products` is a list of product dicts (brand, series,
    model_number, category). Entity level: a model-like code
    from the query matches the product model number. Concept
    level: query keywords overlap the brand / series / category
    words. Returns a list of {"product", "level", "score"},
    entity matches first, then concept matches, best first.
    Products matching neither level are dropped.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    query_entities = extract_entities(query)
    query_concepts = set(extract_concepts(query))
    query_model = normalize_model_number(query)
    ranked = []
    for product in products or []:
        model_key = normalize_model_number(product.get("model_number") or "")
        entity_hit = bool(model_key) and (
            model_key in query_model
            or any(normalize_model_number(e) == model_key for e in query_entities)
            or any(normalize_model_number(e) in model_key for e in query_entities if e)
        )
        concept_words = set()
        for field in ("brand", "series", "category"):
            concept_words.update(extract_concepts(str(product.get(field) or "")))
        concept_overlap = (
            len(query_concepts & concept_words) / len(query_concepts) if query_concepts else 0.0
        )
        if entity_hit:
            ranked.append({"product": product, "level": "entity", "score": 1.0})
        elif concept_overlap > 0:
            ranked.append(
                {
                    "product": product,
                    "level": "concept",
                    "score": round(concept_overlap, 4),
                }
            )
    ranked.sort(key=lambda r: (r["level"] != "entity", -r["score"]))
    return ranked


def product_passages(products):
    """Turn product dicts into graph passages (pure, in-memory).

    One passage per product: brand, series, model number,
    category. The passage id is "product:<id>" so results map
    back to the product.
    """
    passages = []
    for product in products or []:
        text = " ".join(
            str(product.get(field) or "")
            for field in ("brand", "series", "model_number", "category")
        ).strip()
        passages.append(
            {
                "id": f"product:{product.get('id')}",
                "content": text,
            }
        )
    return passages


def rank_products_pipeline(products, query, mode="local", top_k=5, embedder=None):
    """Rank products through the retrieval pipeline (pure).

    The products are indexed into a knowledge graph and read back
    through a LightRAG query mode, so a query can reach a product
    through a shared brand or series node instead of only by an
    exact model-number hit. No price is returned here, so no
    evidence-free price exists; price evidence still comes from
    `search_products_dual`. The retrieval package is imported
    lazily so this module keeps no import cycle with it.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    from puspa.retrieval.knowledge_graph import KnowledgeGraph
    from puspa.retrieval.modes import query as mode_query

    products = list(products or [])
    graph = KnowledgeGraph()
    for passage in product_passages(products):
        graph.add_document(passage["id"], passage["content"])
    ranked = mode_query(graph, query, mode=mode, embedder=embedder, top_k=top_k)
    by_id = {f"product:{p.get('id')}": p for p in products}
    out = []
    for result in ranked:
        product_id = result["passage_id"].split("#p")[0]
        product = by_id.get(product_id)
        if product is None:
            continue
        out.append(
            {
                "product": product,
                "level": result.get("level", "concept"),
                "score": result["score"],
                "mode": result.get("mode", mode),
                "evidence_ids": [],
            }
        )
    return out


def search_products_dual(query, *, dsn=None):
    """Dual-level product search on Postgres (entity + concept).

    Entity candidates come from model-number matching,
    concept candidates from brand / series keyword matching.
    Every result carries `evidence_ids` (listing link ids and
    price evidence ids) and `prices` (current prices, each
    with its own `evidence_id`); a product with no price
    evidence simply reports empty prices, never an
    evidence-free price. Returns
    {"entity_matches": [...], "concept_matches": [...]}.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    entity_codes = [e for e in extract_entities(query) if any(ch.isdigit() for ch in e)]
    concept_tokens = extract_concepts(query)
    candidates = []
    seen = set()
    for code in entity_codes:
        for product in identity_candidates(model_number=code, dsn=dsn):
            if product["id"] not in seen:
                candidates.append(product)
                seen.add(product["id"])
    for token in concept_tokens[:3]:
        for product in identity_candidates(brand=token, dsn=dsn):
            if product["id"] not in seen:
                candidates.append(product)
                seen.add(product["id"])
        for product in identity_candidates(series=token, dsn=dsn):
            if product["id"] not in seen:
                candidates.append(product)
                seen.add(product["id"])
    ranked = rank_products(candidates, query)
    entity_matches, concept_matches = [], []
    for item in ranked:
        product = item["product"]
        prices = current_prices(product["id"], dsn=dsn)
        evidence_ids = [p["evidence_id"] for p in prices if p.get("evidence_id")]
        entry = {
            "product": product,
            "level": item["level"],
            "score": item["score"],
            "prices": prices,
            "evidence_ids": evidence_ids,
        }
        if item["level"] == "entity":
            entity_matches.append(entry)
        else:
            concept_matches.append(entry)
    return {"entity_matches": entity_matches, "concept_matches": concept_matches}
