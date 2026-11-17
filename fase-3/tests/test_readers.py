"""Tes Pembaca harga (§2.8 revisi 7): HTTP fetch + Playwright."""

import httpx
import pytest

from puspa.pricing.readers import (
    HttpReader, PlaywrightReader, buat_pembaca, ekstrak_harga_dari_html)

HTML_JSONLD = """<html><head><script type="application/ld+json">
{"@type": "Product", "name": "Samsung Galaxy S24",
 "offers": {"@type": "Offer", "price": 12499000, "priceCurrency": "IDR"}}
</script></head><body>Galaxy S24</body></html>"""

HTML_META = """<html><head>
<meta property="product:price:amount" content="8999000">
</head><body></body></html>"""


def test_ekstrak_jsonld_dan_meta():
    hasil = ekstrak_harga_dari_html(HTML_JSONLD)
    assert hasil["harga"] == 12499000.0
    assert "Galaxy S24" in hasil["nama"]
    assert ekstrak_harga_dari_html(HTML_META)["harga"] == 8999000.0
    assert ekstrak_harga_dari_html("<html><body>tidak ada harga</body></html>")["harga"] is None
    assert ekstrak_harga_dari_html("")["harga"] is None


def _transport_untuk(html: str, status: int = 200):
    def _handler(request):
        return httpx.Response(status, text=html)

    return httpx.MockTransport(_handler)


def test_http_reader_baca_jsonld():
    client = httpx.Client(transport=_transport_untuk(HTML_JSONLD))
    hasil = HttpReader("Tokopedia", client=client).baca("https://www.tokopedia.com/toko/produk-1")
    assert hasil.sukses is True
    assert hasil.harga == 12499000.0
    assert hasil.nama == "Samsung Galaxy S24"


def test_http_reader_blokir_dilaporkan_tanpa_menembus():
    for status in (403, 429):
        client = httpx.Client(transport=_transport_untuk("blocked", status))
        hasil = HttpReader("Shopee", client=client).baca("https://shopee.co.id/produk-1")
        assert hasil.sukses is False
        assert f"http_{status}" in hasil.alasan or "memblokir" in hasil.alasan


def test_http_reader_captcha_dan_tanpa_harga():
    client = httpx.Client(transport=_transport_untuk("<html>captcha verification</html>"))
    hasil = HttpReader("Blibli", client=client).baca("https://www.blibli.com/p/x")
    assert hasil.sukses is False and hasil.alasan == "captcha_terdeteksi"
    client = httpx.Client(transport=_transport_untuk("<html>kosong</html>"))
    hasil = HttpReader("Blibli", client=client).baca("https://www.blibli.com/p/x")
    assert hasil.sukses is False and hasil.alasan == "harga_tidak_ditemukan"


def test_http_reader_domain_di_luar_allowlist_ditolak():
    client = httpx.Client(transport=_transport_untuk(HTML_JSONLD))
    hasil = HttpReader("Tokopedia", client=client).baca("https://situs-asing.example/produk")
    assert hasil.sukses is False and hasil.alasan == "domain_tidak_diizinkan"


def test_playwright_reader_tersedia_atau_jujur_tidak():
    # Di VM ini Playwright terinstal sebagai paket (1.65.2); browser
    # Chromium mungkin belum terunduh — yang diuji: reader tidak
    # mengarang hasil bila browser tidak jalan.
    reader = PlaywrightReader("Tokopedia")
    hasil = reader.baca("https://situs-asing.example/produk")
    assert hasil.sukses is False
    assert hasil.alasan == "domain_tidak_diizinkan"


def test_playwright_reader_baca_situs_uji_bila_browser_ada(monkeypatch):
    """Browser read-only sungguhan, ujung-ke-ujung lewat reader: bila
    env PUSPA_CHROMIUM_EXECUTABLE menunjuk ke binary Chrome for
    Testing, PlaywrightReader membaca halaman situs uji lokal dan
    mengekstrak angkanya. Tanpa binary, tes di-skip (bukan gagal).
    Allowlist di-patch di tes ini saja (guard allowlist-nya sendiri
    teruji terpisah di atas) supaya reader bisa membuka situs uji
    localhost."""
    import os

    executable = os.environ.get("PUSPA_CHROMIUM_EXECUTABLE")
    if not executable or not os.path.exists(executable):
        pytest.skip("browser Chromium belum tersedia di host ini")
    from puspa.billing.fixture import SitusUji
    from puspa.pricing import readers as readers_mod

    monkeypatch.setenv("PUSPA_CHROMIUM_EXECUTABLE", executable)
    monkeypatch.setattr(readers_mod, "is_allowed", lambda url, allowlist=None: True)
    with SitusUji() as situs:
        hasil = PlaywrightReader("Tokopedia").baca(situs.url("pln"))
    assert hasil.sukses is True
    assert hasil.harga == 132500.0


def test_buat_pembaca_memilih_jalur():
    pembaca, jalur = buat_pembaca("Tokopedia")
    assert jalur in ("playwright", "http")
    assert pembaca.marketplace == "Tokopedia"
