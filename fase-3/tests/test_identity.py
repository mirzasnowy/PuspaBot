"""Tes Graf Identitas dan Harga (§3.2 revisi 7)."""

import pytest

from puspa.graphs import identity as graf


def test_normalisasi_nomor_model_deterministik():
    assert graf.normalisasi_nomor_model("sm-s921b") == "SMS921B"
    assert graf.normalisasi_nomor_model("SM S921-B") == "SMS921B"
    assert graf.normalisasi_nomor_model("MQKP3ID/A") == "MQKP3IDA"


def test_normalisasi_varian_hanya_kunci_harga():
    assert graf.normalisasi_varian({"storage": "256GB", "warna": "Hitam", "berat": "200g"}) == {
        "storage": "256gb", "warna": "hitam"}
    assert graf.normalisasi_varian(None) == {}


def test_tambah_dan_cari_produk(make_user, test_dsn):
    user = make_user()
    produk = graf.tambah_produk(merek="Samsung", seri="Galaxy S24", nomor_model="SM-S921B",
                                kategori="HP", varian={"storage": "256GB"}, dsn=test_dsn)
    assert produk["nomor_model"] == "SMS921B"
    assert produk["varian"] == {"storage": "256gb"}
    # Idempotent: tambah lagi -> produk yang sama.
    lagi = graf.tambah_produk(merek="Samsung", nomor_model="sm-s921b", kategori="HP", dsn=test_dsn)
    assert lagi["id"] == produk["id"]
    assert graf.cari_produk("SM-S921B", dsn=test_dsn)["id"] == produk["id"]
    assert graf.cari_produk("TIDAK-ADA", dsn=test_dsn) is None


def test_kategori_di_luar_scope_ditolak(make_user, test_dsn):
    make_user()
    with pytest.raises(graf.IdentityError):
        graf.tambah_produk(merek="Nike", nomor_model="AIRMAX90", kategori="fashion", dsn=test_dsn)


def test_tautan_status_dan_konfirmasi_user(make_user, test_dsn):
    user = make_user()
    produk = graf.tambah_produk(merek="Samsung", nomor_model="SM-S921B", kategori="HP", dsn=test_dsn)
    tautan = graf.tautkan_listing(produk_id=produk["id"], user_id=user["user_id"],
                                  marketplace="Tokopedia",
                                  listing_url="https://www.tokopedia.com/toko/x",
                                  dsn=test_dsn)
    assert tautan["status"] == "otomatis"
    dikonfirmasi = graf.konfirmasi_tautan(tautan["id"], user_id=user["user_id"],
                                          diterima=True, dsn=test_dsn)
    assert dikonfirmasi["status"] == "dikonfirmasi_user"
    ditolak = graf.konfirmasi_tautan(tautan["id"], user_id=user["user_id"],
                                     diterima=False, dsn=test_dsn)
    assert ditolak["status"] == "ditolak_user"
    with pytest.raises(KeyError):
        graf.konfirmasi_tautan("00000000-0000-0000-0000-000000000000",
                               user_id=user["user_id"], diterima=True, dsn=test_dsn)


def test_harga_tanpa_bukti_ditolak(make_user, test_dsn):
    user = make_user()
    produk = graf.tambah_produk(merek="Samsung", nomor_model="SM-S921B", kategori="HP", dsn=test_dsn)
    tautan = graf.tautkan_listing(produk_id=produk["id"], user_id=user["user_id"],
                                  marketplace="Shopee",
                                  listing_url="https://shopee.co.id/x", dsn=test_dsn)
    with pytest.raises(graf.IdentityError):
        graf.catat_harga(tautan_id=tautan["id"], user_id=user["user_id"],
                         harga=12000000, bukti_id="", dsn=test_dsn)


def test_harga_terkini_dan_riwayat_30_hari(make_user, test_dsn):
    user = make_user()
    produk = graf.tambah_produk(merek="Samsung", nomor_model="SM-S921B", kategori="HP", dsn=test_dsn)
    for pasar, url, harga in (
            ("Tokopedia", "https://www.tokopedia.com/toko/a", 12999000),
            ("Shopee", "https://shopee.co.id/a", 12499000),
            ("Blibli", "https://www.blibli.com/p/a", 12750000)):
        tautan = graf.tautkan_listing(produk_id=produk["id"], user_id=user["user_id"],
                                      marketplace=pasar, listing_url=url, dsn=test_dsn)
        graf.catat_harga(tautan_id=tautan["id"], user_id=user["user_id"], harga=harga,
                         ongkir=15000, rating_toko=4.9, bukti_id=f"bukti-{pasar}", dsn=test_dsn)
    terkini = graf.harga_terkini(produk["id"], dsn=test_dsn)
    assert {t["marketplace"]: t["harga"] for t in terkini} == {
        "Tokopedia": 12999000.0, "Shopee": 12499000.0, "Blibli": 12750000.0}
    assert all(t["bukti_id"] for t in terkini)
    riwayat = graf.riwayat_harga(produk["id"], hari=30, dsn=test_dsn)
    assert len(riwayat) == 3
    assert all(r["bukti_id"] for r in riwayat)


def test_cocokkan_otomatis_tanpa_tebakan(make_user, test_dsn):
    user = make_user()
    graf.tambah_produk(merek="Samsung", seri="Galaxy S24", nomor_model="SM-S921B",
                       kategori="HP", dsn=test_dsn)
    cocok = graf.cocokkan_otomatis(nomor_model="SM-S921B", dsn=test_dsn)
    assert cocok["cocok"] is not None and cocok["perlu_tanya"] is False
    ragu = graf.cocokkan_otomatis(nomor_model="SM-S999Z", merek="Samsung", dsn=test_dsn)
    assert ragu["cocok"] is None and ragu["perlu_tanya"] is True
    # Kandidat ditampilkan, bukan dipilih diam-diam.
    assert isinstance(ragu["kandidat"], list)
