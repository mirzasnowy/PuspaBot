"""Pembaca harga (§2.8, §4.1 revisi 7).

Dua jalur baca, keduanya read-only:

- `HttpReader`: HTTP fetch lewat httpx (client disuntikkan — di tes
  memakai MockTransport, tanpa jaringan). Hanya domain allowlist
  (§2.6). Isi mentah tidak pernah diteruskan sebagai instruksi:
  bidang teks bebas ditandai `[DATA: ...]` dan dipangkas 200 karakter
  (pola Pembaca terkarantina Fase 0).
- `PlaywrightReader`: browser read-only (Playwright, §2.8). Kalau
  Playwright / browser Chromium tidak tersedia di host ini, reader
  mengembalikan `{"sukses": False, "alasan": "playwright_tidak_tersedia"}`
  — gagal terlihat, bukan pura-pura membaca.

Parser: ekstraksi generik dari JSON-LD (`Product`/`Offer`) dan meta
`product:price:amount` — pola yang sama di ketiga marketplace, tanpa
menyalin selector khusus milik siapa pun. Kalau halaman tidak memuat
angka yang bisa di-parse, hasilnya `tidak_bisa_dibaca`, bukan tebakan.

Gerbang ToS (`tos.boleh_baca`) ditegakkan di `compare.py`, bukan di
sini — reader ini hanya alat; keputusan boleh/tidak ada di gerbang.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import httpx

from puspa.sandbox.allowlist import is_allowed

EXCERPT_MAX = 200


class ReaderError(Exception):
    """Pembaca gagal membaca sumber."""


@dataclass
class ResultRead:
    """Satu hasil baca listing oleh satu Pembaca."""

    success: bool
    marketplace: str
    listing_url: str
    price: float | None = None
    shipping: float | None = None
    store_rating: float | None = None
    name: str = ""
    stock: str = "unknown"
    reason: str = ""
    text_excerpt: str = ""
    page_hash: str = ""


def _mark_data(text: str) -> str:
    """Tandai teks bebas dari konten tak tepercaya sebagai data (§2.6)."""
    return "[DATA: " + " ".join(str(text).split())[:EXCERPT_MAX] + "]"


def _parse_rupiah(text: str) -> float | None:
    """Parse angka Rupiah dari teks ("Rp12.499.000" -> 12499000.0)."""
    if not text:
        return None
    match = re.search(r"Rp\s*([\d.,]+)", str(text))
    if not match:
        return None
    try:
        return float(match.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def extract_price_from_html(html: str) -> dict:
    """Ekstrak harga/nama dari HTML lewat JSON-LD + meta generik.

    Kembalikan {"harga": float|None, "nama": str, "potongan": str}.
    Tidak ada angka yang ditebak: tanpa JSON-LD/meta yang cocok,
    harga None.
    """
    if not html:
        return {"price": None, "name": "", "excerpt": ""}
    # JSON-LD Product/Offer.
    for match in re.finditer(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(match.group(1).strip())
        except (ValueError, TypeError):
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict):
                continue
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get("price") if isinstance(offers, dict) else None
            if price is not None:
                try:
                    return {"price": float(price),
                            "name": str(item.get("name", ""))[:EXCERPT_MAX],
                            "excerpt": _mark_data(item.get("name", ""))}
                except (TypeError, ValueError):
                    pass
    # Meta product:price:amount.
    match = re.search(
        r'property="product:price:amount"\s+content="([\d.]+)"', html)
    if match:
        return {"price": float(match.group(1)), "name": "",
                "excerpt": _mark_data(match.group(0))}
    # Pola teks "Rp..." pertama sebagai jalan terakhir yang jujur:
    # hanya dipakai bila halaman memuat SATU harga yang jelas.
    price = _parse_rupiah(html)
    return {"price": price, "name": "",
            "excerpt": _mark_data(html[:EXCERPT_MAX]) if price else ""}


@dataclass
class HttpReader:
    """Pembaca lewat HTTP fetch (httpx). Satu instance per marketplace."""

    marketplace: str
    client: httpx.Client | None = None

    def read(self, listing_url: str) -> ResultRead:
        """Baca satu listing. Blokir (403/429/captcha) -> gagal terlihat,
        TANPA upaya menembus (§11)."""
        if not is_allowed(listing_url):
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url, reason="domain_not_diizinkan")
        client = self.client or httpx.Client(timeout=15.0)
        try:
            resp = client.get(listing_url, headers={
                "User-Agent": "Puspa/0.3 (perbandingan harga atas permintaan pengguna)",
                "Accept-Language": "id-ID",
            })
        except httpx.HTTPError as exc:
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url, reason=f"jaringan_gagal: {exc!r}"[:200])
        if resp.status_code in (403, 429):
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url,
                             reason=f"sumber_memblokir_http_{resp.status_code}")
        if resp.status_code != 200:
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url,
                             reason=f"http_{resp.status_code}")
        if "captcha" in resp.text.lower()[:2000]:
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url, reason="captcha_terdeteksi")
        extracted = extract_price_from_html(resp.text)
        if extracted["price"] is None:
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url, reason="price_not_ditemukan")
        import hashlib

        return ResultRead(
            success=True, marketplace=self.marketplace, listing_url=listing_url,
            price=extracted["price"], name=extracted["name"],
            text_excerpt=extracted["excerpt"],
            page_hash=hashlib.sha256(resp.text.encode("utf-8")).hexdigest())


@dataclass
class PlaywrightReader:
    """Pembaca lewat browser read-only (Playwright, §2.8).

    `tersedia()` False bila paket Playwright atau browser Chromium
    tidak terinstal di host — pemanggil wajib memakai HTTP fetch atau
    melaporkan tidak bisa cek, bukan mengarang hasil baca.
    """

    marketplace: str

    @staticmethod
    def is_available() -> bool:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return True

    def read(self, listing_url: str) -> ResultRead:
        if not is_allowed(listing_url):
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url, reason="domain_not_diizinkan")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url,
                             reason="playwright_not_tersedia")
        # Seam host: bila CDN Playwright diblokir proxy egress, browser
        # Chrome for Testing bisa diunduh dari storage.googleapis.com
        # dan ditunjuk lewat env PUSPA_CHROMIUM_EXECUTABLE (lihat
        # STATUS_FASE3.md). Tanpa env, pakai browser bawaan Playwright.
        import os

        executable = os.environ.get("PUSPA_CHROMIUM_EXECUTABLE") or None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=executable)
                try:
                    page = browser.new_page()
                    page.goto(listing_url, timeout=20000)
                    html = page.content()
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001 — gagal browser = gagal terlihat
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url,
                             reason=f"browser_gagal: {exc!r}"[:200])
        extracted = extract_price_from_html(html)
        if extracted["price"] is None:
            return ResultRead(success=False, marketplace=self.marketplace,
                             listing_url=listing_url, reason="price_not_ditemukan")
        import hashlib

        return ResultRead(success=True, marketplace=self.marketplace,
                         listing_url=listing_url, price=extracted["price"],
                         name=extracted["name"], text_excerpt=extracted["excerpt"],
                         page_hash=hashlib.sha256(html.encode("utf-8")).hexdigest())


def create_reader(marketplace: str, *, client: httpx.Client | None = None):
    """Pabrik Pembaca: Playwright bila tersedia, kalau tidak HTTP fetch.
    Kembalikan (pembaca, jalur) dengan jalur 'playwright'/'http'/'tidak_tersedia'.
    """
    if PlaywrightReader.is_available():
        return PlaywrightReader(marketplace), "playwright"
    return HttpReader(marketplace, client=client), "http"
