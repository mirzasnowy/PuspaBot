"""Bibit katalog (§3.8 revisi 7) + daftar barang uji default.

Bibit katalog: graf identitas diisi dari halaman spesifikasi resmi
merek untuk kategori uji (nomor model, varian) sebelum pengguna uji
masuk. Tanpa bibit, target pencocokan otomatis 60% menunggu
konfirmasi dari 11 orang.

Daftar barang uji default (keputusan 7: "Dipilih Nero saat fase 3"):
10 barang lintas 9 kategori, diambil dari bibit katalog. Daftar ini
DEFAULT yang masuk akal — daftar final menunggu konfirmasi Nero
(lihat STATUS_FASE3.md). Nomor model di bawah adalah nomor model
publik yang umum dipakai di spesifikasi resmi merek; varian hanya
yang memengaruhi harga (storage/warna).

CATATAN JUJUR: bibit di sini adalah data awal yang ditulis dari
pengetahuan spesifikasi publik, BUKAN hasil tarikan langsung dari
halaman spesifikasi resmi merek saat build (tarikan live marketplace
ditahan gerbang ToS, lihat `tos.py`). Bibit ditandai
`sumber_bibit="bibit_awal"` supaya bisa diganti hasil tarikan resmi
begitu Nero memutuskan sumber (keputusan 15).
"""

from __future__ import annotations

# (merek, seri, nomor_model, kategori, varian)
BIBIT_KATALOG: list[tuple[str, str, str, str, dict]] = [
    ("Samsung", "Galaxy S24", "SM-S921B", "phone", {"storage": "256GB", "warna": "hitam"}),
    ("Samsung", "Galaxy S24", "SM-S921B", "phone", {"storage": "512GB", "warna": "hitam"}),
    ("Apple", "iPhone 15", "A3090", "phone", {"storage": "128GB", "warna": "hitam"}),
    ("Apple", "MacBook Air M3", "MQKP3ID/A", "laptop", {"storage": "256GB", "warna": "silver"}),
    ("Lenovo", "IdeaPad Slim 5", "82XB00FUID", "laptop", {"storage": "512GB", "warna": "abu-abu"}),
    ("Samsung", "Galaxy Tab A9", "SM-X110", "tablet", {"storage": "64GB", "warna": "abu-abu"}),
    ("Apple", "Watch SE 2", "A2729", "smartwatch", {"warna": "hitam"}),
    ("Samsung", "Galaxy Buds2 Pro", "SM-R510", "earphones/TWS", {"warna": "putih"}),
    ("LG", "UHD 4K 55 inci", "55UR7500PSC", "TV", {}),
    ("Sony", "PlayStation 5 Slim", "CFI-2018A01", "game console", {"storage": "1TB"}),
    ("Canon", "EOS R50", "EOSR50", "camera", {}),
    ("Daikin", "AC Split 1 PK", "FTKQ25", "branded home appliances", {}),
    ("LG", "Kulkas 2 Pintu", "GN-B422SQCL", "branded home appliances", {}),
    ("Samsung", "Mesin Cuci Front Load 8kg", "WW80T504DAN", "branded home appliances", {}),
]

# 10 barang uji default (keputusan 7) — satu per kategori, HP dua.
PRODUCT_TEST_DEFAULT: list[dict] = [
    {"brand": "Samsung", "series": "Galaxy S24", "model_number": "SM-S921B",
     "category": "phone", "variant": {"storage": "256GB", "warna": "hitam"}},
    {"brand": "Apple", "series": "iPhone 15", "model_number": "A3090",
     "category": "phone", "variant": {"storage": "128GB", "warna": "hitam"}},
    {"brand": "Apple", "series": "MacBook Air M3", "model_number": "MQKP3ID/A",
     "category": "laptop", "variant": {"storage": "256GB", "warna": "silver"}},
    {"brand": "Samsung", "series": "Galaxy Tab A9", "model_number": "SM-X110",
     "category": "tablet", "variant": {"storage": "64GB", "warna": "abu-abu"}},
    {"brand": "Apple", "series": "Watch SE 2", "model_number": "A2729",
     "category": "smartwatch", "variant": {"warna": "hitam"}},
    {"brand": "Samsung", "series": "Galaxy Buds2 Pro", "model_number": "SM-R510",
     "category": "earphones/TWS", "variant": {"warna": "putih"}},
    {"brand": "LG", "series": "UHD 4K 55 inci", "model_number": "55UR7500PSC",
     "category": "TV", "variant": {}},
    {"brand": "Sony", "series": "PlayStation 5 Slim", "model_number": "CFI-2018A01",
     "category": "game console", "variant": {"storage": "1TB"}},
    {"brand": "Canon", "series": "EOS R50", "model_number": "EOSR50",
     "category": "camera", "variant": {}},
    {"brand": "Daikin", "series": "AC Split 1 PK", "model_number": "FTKQ25",
     "category": "branded home appliances", "variant": {}},
]

CATEGORY_SCOPE = (
    "phone", "laptop", "tablet", "smartwatch", "earphones/TWS", "TV",
    "game console", "camera", "branded home appliances",
)

# Kategori yang ditolak tegas (§4.1, §5.2): bot menolak/menyesuaikan
# dengan peringatan jelas, bukan membangun versi diam-diam lemah.
CATEGORY_REJECTED = ("fashion", "baju", "sepatu", "tas", "clothing")


def di_luar_scope(category: str) -> bool:
    """True bila kategori di luar 9 kategori §4.1."""
    return str(category).strip().lower() not in {k.lower() for k in CATEGORY_SCOPE}


def penolakan_scope(category: str) -> str:
    """Pesan penolakan yang jelas untuk kategori di luar scope (§5.2)."""
    return (
        f"Kategori {category!r} di luar scope bandingkan harga Puspa "
        "(9 kategori: HP, laptop, tablet, smartwatch, earphone/TWS, TV, "
        "konsol game, kamera, peralatan rumah tangga bermerek). Fashion dan "
        "barang non-standar pencocokannya tidak andal — varian ukuran/warna "
        "membuat harga dari barang berbeda bisa tampil. Puspa tidak membangun "
        "versi diam-diam yang lemah untuk kategori ini."
    )


def content_bibit(dsn=None) -> dict:
    """Isi graf identitas dari BIBIT_KATALOG. Kembalikan hitungan."""
    from puspa.graphs.identity import add_product

    ditambah = 0
    for brand, series, model_number, category, variant in BIBIT_KATALOG:
        add_product(brand=brand, series=series, model_number=model_number,
                      category=category, variant=variant, dsn=dsn)
        ditambah += 1
    return {"bibit_ditulis": ditambah,
            "product_unik": len({(m, n) for m, _s, n, _k, _v in BIBIT_KATALOG})}
