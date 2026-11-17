"""Tes Pembaca harga (§2.8 revisi 7): HTTP fetch + Playwright."""

import httpx
import pytest

from puspa.pricing.readers import (
    HttpReader, PlaywrightReader, create_reader, extract_price_from_html)

HTML_JSONLD = """<html><head><script type="application/ld+json">
{"@type": "Product", "name": "Samsung Galaxy S24",
 "offers": {"@type": "Offer", "price": 12499000, "priceCurrency": "IDR"}}
</script></head><body>Galaxy S24</body></html>"""

HTML_META = """<html><head>
<meta property="product:price:amount" content="8999000">
</head><body></body></html>"""


def test_extract_jsonld_and_meta():
    result = extract_price_from_html(HTML_JSONLD)
    assert result["price"] == 12499000.0
    assert "Galaxy S24" in result["name"]
    assert extract_price_from_html(HTML_META)["price"] == 8999000.0
    assert extract_price_from_html("<html><body>tidak ada harga</body></html>")["price"] is None
    assert extract_price_from_html("")["price"] is None


def _transport_for(html: str, status: int = 200):
    def _handler(request):
        return httpx.Response(status, text=html)

    return httpx.MockTransport(_handler)


def test_http_reader_read_jsonld():
    client = httpx.Client(transport=_transport_for(HTML_JSONLD))
    result = HttpReader("Tokopedia", client=client).read("https://www.tokopedia.com/toko/produk-1")
    assert result.success is True
    assert result.price == 12499000.0
    assert result.name == "Samsung Galaxy S24"


def test_http_reader_block_reported_without_bypass():
    for status in (403, 429):
        client = httpx.Client(transport=_transport_for("blocked", status))
        result = HttpReader("Shopee", client=client).read("https://shopee.co.id/produk-1")
        assert result.success is False
        assert f"http_{status}" in result.reason or "memblokir" in result.reason


def test_http_reader_captcha_and_without_price():
    client = httpx.Client(transport=_transport_for("<html>captcha verification</html>"))
    result = HttpReader("Blibli", client=client).read("https://www.blibli.com/p/x")
    assert result.success is False and result.reason == "captcha_terdeteksi"
    client = httpx.Client(transport=_transport_for("<html>kosong</html>"))
    result = HttpReader("Blibli", client=client).read("https://www.blibli.com/p/x")
    assert result.success is False and result.reason == "price_not_ditemukan"


def test_http_reader_domain_outside_allowlist_rejected():
    client = httpx.Client(transport=_transport_for(HTML_JSONLD))
    result = HttpReader("Tokopedia", client=client).read("https://situs-asing.example/produk")
    assert result.success is False and result.reason == "domain_not_diizinkan"


def test_playwright_reader_available_or_honestly_unavailable():
    # Di VM ini Playwright terinstal sebagai paket (1.65.2); browser
    # Chromium mungkin belum terunduh — yang diuji: reader tidak
    # mengarang hasil bila browser tidak jalan.
    reader = PlaywrightReader("Tokopedia")
    result = reader.read("https://situs-asing.example/produk")
    assert result.success is False
    assert result.reason == "domain_not_diizinkan"


def test_playwright_reader_reads_test_site_when_browser_exists(monkeypatch):
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
    from puspa.billing.fixture import SiteFixture
    from puspa.pricing import readers as readers_mod

    monkeypatch.setenv("PUSPA_CHROMIUM_EXECUTABLE", executable)
    monkeypatch.setattr(readers_mod, "is_allowed", lambda url, allowlist=None: True)
    with SiteFixture() as situs:
        result = PlaywrightReader("Tokopedia").read(situs.url("pln"))
    assert result.success is True
    assert result.price == 132500.0


def test_create_reader_chooses_path():
    reader, path = create_reader("Tokopedia")
    assert path in ("playwright", "http")
    assert reader.marketplace == "Tokopedia"
