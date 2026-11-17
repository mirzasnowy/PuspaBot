"""Tes bibit katalog + daftar barang uji + cek syarat layanan
(§3.8, keputusan 7, keputusan 15 revisi 7)."""

from puspa.pricing import catalog, tos


def test_isi_bibit_mengisi_graf(make_user, test_dsn):
    make_user()
    hasil = catalog.isi_bibit(dsn=test_dsn)
    assert hasil["bibit_ditulis"] == len(catalog.BIBIT_KATALOG)
    assert hasil["produk_unik"] == 13
    # Semua 9 kategori terwakili di bibit (cek lewat produk langsung).
    from puspa.graphs.identity import cari_produk

    assert cari_produk("SM-S921B", dsn=test_dsn)["kategori"] == "HP"
    assert cari_produk("55UR7500PSC", dsn=test_dsn)["kategori"] == "TV"
    assert cari_produk("FTKQ25", dsn=test_dsn)["kategori"] == "peralatan rumah tangga bermerek"


def test_barang_uji_default_10_lintas_9_kategori():
    assert len(catalog.BARANG_UJI_DEFAULT) == 10
    kategori = {b["kategori"] for b in catalog.BARANG_UJI_DEFAULT}
    assert len(kategori) == 9
    assert all(b["nomor_model"] for b in catalog.BARANG_UJI_DEFAULT)


def test_scope_fashion_ditolak_dengan_pesan_jelas():
    assert catalog.di_luar_scope("fashion") is True
    assert catalog.di_luar_scope("sepatu") is True
    assert catalog.di_luar_scope("HP") is False
    pesan = catalog.penolakan_scope("fashion")
    assert "di luar scope" in pesan and "tidak membangun" in pesan


def test_gerbang_tos_marketplace_live_ditahan():
    # Keputusan 15: ketiga marketplace melarang otomatis -> live ditahan.
    for pasar in ("Tokopedia", "Shopee", "Blibli"):
        assert tos.boleh_baca(pasar, mode="live") is False
        assert tos.boleh_baca(pasar, mode="fixture") is True
    # Provider tagihan: layanan resmi terbatas -> live diizinkan (atas
    # permintaan user, frekuensi rendah).
    for provider in ("PLN", "PDAM", "pulsa", "paket data"):
        assert tos.boleh_baca(provider, mode="live") is True
    assert tos.boleh_baca("SumberTidakDikenal", mode="live") is False


def test_catat_dan_daftar_hasil_cek_tos(test_dsn):
    hasil = tos.catat_hasil_cek(dsn=test_dsn)
    assert {h["sumber"] for h in hasil} == set(tos.HASIL_CEK)
    tokopedia = next(h for h in hasil if h["sumber"] == "Tokopedia")
    assert tokopedia["hasil"] == "melarang_otomatis"
    assert "B.12" in tokopedia["pasal"]
    assert tokopedia["keputusan"] == "menunggu_keputusan_nero"
