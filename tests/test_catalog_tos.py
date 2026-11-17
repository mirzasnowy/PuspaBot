"""Tes bibit katalog + daftar barang uji + cek syarat layanan
(§3.8, keputusan 7, keputusan 15 revisi 7)."""

from puspa.pricing import catalog, tos


def test_content_bibit_mengisi_graf(make_user, test_dsn):
    make_user()
    result = catalog.content_bibit(dsn=test_dsn)
    assert result["bibit_ditulis"] == len(catalog.BIBIT_KATALOG)
    assert result["product_unik"] == 13
    # Semua 9 kategori terwakili di bibit (cek lewat produk langsung).
    from puspa.graphs.identity import search_product

    assert search_product("SM-S921B", dsn=test_dsn)["category"] == "phone"
    assert search_product("55UR7500PSC", dsn=test_dsn)["category"] == "TV"
    assert search_product("FTKQ25", dsn=test_dsn)["category"] == "branded home appliances"


def test_product_test_default_10_lintas_9_category():
    assert len(catalog.PRODUCT_TEST_DEFAULT) == 10
    category = {b["category"] for b in catalog.PRODUCT_TEST_DEFAULT}
    assert len(category) == 9
    assert all(b["model_number"] for b in catalog.PRODUCT_TEST_DEFAULT)


def test_scope_fashion_rejected_dengan_message_jelas():
    assert catalog.di_luar_scope("fashion") is True
    assert catalog.di_luar_scope("sepatu") is True
    assert catalog.di_luar_scope("phone") is False
    message = catalog.penolakan_scope("fashion")
    assert "di luar scope" in message and "tidak membangun" in message


def test_gerbang_tos_marketplace_live_ditahan():
    # Keputusan 15: ketiga marketplace melarang otomatis -> live ditahan.
    for pasar in ("Tokopedia", "Shopee", "Blibli"):
        assert tos.boleh_read(pasar, mode="live") is False
        assert tos.boleh_read(pasar, mode="fixture") is True
    # Provider tagihan: layanan resmi terbatas -> live diizinkan (atas
    # permintaan user, frekuensi rendah).
    for provider in ("PLN", "PDAM", "airtime", "paket data"):
        assert tos.boleh_read(provider, mode="live") is True
    assert tos.boleh_read("SumberTidakDikenal", mode="live") is False


def test_record_and_items_result_check_tos(test_dsn):
    result = tos.record_result_check(dsn=test_dsn)
    assert {h["sumber"] for h in result} == set(tos.RESULT_CHECK)
    tokopedia = next(h for h in result if h["sumber"] == "Tokopedia")
    assert tokopedia["result"] == "melarang_automatic"
    assert "B.12" in tokopedia["pasal"]
    assert tokopedia["keputusan"] == "awaiting_nero_decision"
