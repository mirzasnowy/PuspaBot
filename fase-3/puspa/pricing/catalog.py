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
    ("Samsung", "Galaxy S24", "SM-S921B", "HP", {"storage": "256GB", "warna": "hitam"}),
    ("Samsung", "Galaxy S24", "SM-S921B", "HP", {"storage": "512GB", "warna": "hitam"}),
    ("Apple", "iPhone 15", "A3090", "HP", {"storage": "128GB", "warna": "hitam"}),
    ("Apple", "MacBook Air M3", "MQKP3ID/A", "laptop", {"storage": "256GB", "warna": "silver"}),
    ("Lenovo", "IdeaPad Slim 5", "82XB00FUID", "laptop", {"storage": "512GB", "warna": "abu-abu"}),
    ("Samsung", "Galaxy Tab A9", "SM-X110", "tablet", {"storage": "64GB", "warna": "abu-abu"}),
    ("Apple", "Watch SE 2", "A2729", "smartwatch", {"warna": "hitam"}),
    ("Samsung", "Galaxy Buds2 Pro", "SM-R510", "earphone/TWS", {"warna": "putih"}),
    ("LG", "UHD 4K 55 inci", "55UR7500PSC", "TV", {}),
    ("Sony", "PlayStation 5 Slim", "CFI-2018A01", "konsol game", {"storage": "1TB"}),
    ("Canon", "EOS R50", "EOSR50", "kamera", {}),
    ("Daikin", "AC Split 1 PK", "FTKQ25", "peralatan rumah tangga bermerek", {}),
    ("LG", "Kulkas 2 Pintu", "GN-B422SQCL", "peralatan rumah tangga bermerek", {}),
    ("Samsung", "Mesin Cuci Front Load 8kg", "WW80T504DAN", "peralatan rumah tangga bermerek", {}),
]

# 10 barang uji default (keputusan 7) — satu per kategori, HP dua.
BARANG_UJI_DEFAULT: list[dict] = [
    {"merek": "Samsung", "seri": "Galaxy S24", "nomor_model": "SM-S921B",
     "kategori": "HP", "varian": {"storage": "256GB", "warna": "hitam"}},
    {"merek": "Apple", "seri": "iPhone 15", "nomor_model": "A3090",
     "kategori": "HP", "varian": {"storage": "128GB", "warna": "hitam"}},
    {"merek": "Apple", "seri": "MacBook Air M3", "nomor_model": "MQKP3ID/A",
     "kategori": "laptop", "varian": {"storage": "256GB", "warna": "silver"}},
    {"merek": "Samsung", "seri": "Galaxy Tab A9", "nomor_model": "SM-X110",
     "kategori": "tablet", "varian": {"storage": "64GB", "warna": "abu-abu"}},
    {"merek": "Apple", "seri": "Watch SE 2", "nomor_model": "A2729",
     "kategori": "smartwatch", "varian": {"warna": "hitam"}},
    {"merek": "Samsung", "seri": "Galaxy Buds2 Pro", "nomor_model": "SM-R510",
     "kategori": "earphone/TWS", "varian": {"warna": "putih"}},
    {"merek": "LG", "seri": "UHD 4K 55 inci", "nomor_model": "55UR7500PSC",
     "kategori": "TV", "varian": {}},
    {"merek": "Sony", "seri": "PlayStation 5 Slim", "nomor_model": "CFI-2018A01",
     "kategori": "konsol game", "varian": {"storage": "1TB"}},
    {"merek": "Canon", "seri": "EOS R50", "nomor_model": "EOSR50",
     "kategori": "kamera", "varian": {}},
    {"merek": "Daikin", "seri": "AC Split 1 PK", "nomor_model": "FTKQ25",
     "kategori": "peralatan rumah tangga bermerek", "varian": {}},
]

KATEGORI_SCOPE = (
    "HP", "laptop", "tablet", "smartwatch", "earphone/TWS", "TV",
    "konsol game", "kamera", "peralatan rumah tangga bermerek",
)

# Kategori yang ditolak tegas (§4.1, §5.2): bot menolak/menyesuaikan
# dengan peringatan jelas, bukan membangun versi diam-diam lemah.
KATEGORI_DITOLAK = ("fashion", "baju", "sepatu", "tas", "pakaian")


def di_luar_scope(kategori: str) -> bool:
    """True bila kategori di luar 9 kategori §4.1."""
    return str(kategori).strip().lower() not in {k.lower() for k in KATEGORI_SCOPE}


def penolakan_scope(kategori: str) -> str:
    """Pesan penolakan yang jelas untuk kategori di luar scope (§5.2)."""
    return (
        f"Kategori {kategori!r} di luar scope bandingkan harga Puspa "
        "(9 kategori: HP, laptop, tablet, smartwatch, earphone/TWS, TV, "
        "konsol game, kamera, peralatan rumah tangga bermerek). Fashion dan "
        "barang non-standar pencocokannya tidak andal — varian ukuran/warna "
        "membuat harga dari barang berbeda bisa tampil. Puspa tidak membangun "
        "versi diam-diam yang lemah untuk kategori ini."
    )


def isi_bibit(dsn=None) -> dict:
    """Isi graf identitas dari BIBIT_KATALOG. Kembalikan hitungan."""
    from puspa.graphs.identity import tambah_produk

    ditambah = 0
    for merek, seri, nomor_model, kategori, varian in BIBIT_KATALOG:
        tambah_produk(merek=merek, seri=seri, nomor_model=nomor_model,
                      kategori=kategori, varian=varian, dsn=dsn)
        ditambah += 1
    return {"bibit_ditulis": ditambah,
            "produk_unik": len({(m, n) for m, _s, n, _k, _v in BIBIT_KATALOG})}
