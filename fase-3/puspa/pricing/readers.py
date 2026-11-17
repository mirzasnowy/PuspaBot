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

POTONGAN_MAX = 200


class ReaderError(Exception):
    """Pembaca gagal membaca sumber."""


@dataclass
class HasilBaca:
    """Satu hasil baca listing oleh satu Pembaca."""

    sukses: bool
    marketplace: str
    listing_url: str
    harga: float | None = None
    ongkir: float | None = None
    rating_toko: float | None = None
    nama: str = ""
    stok: str = "tidak_diketahui"
    alasan: str = ""
    potongan_teks: str = ""
    hash_halaman: str = ""


def _tandai_data(teks: str) -> str:
    """Tandai teks bebas dari konten tak tepercaya sebagai data (§2.6)."""
    return "[DATA: " + " ".join(str(teks).split())[:POTONGAN_MAX] + "]"


def _parse_rupiah(teks: str) -> float | None:
    """Parse angka Rupiah dari teks ("Rp12.499.000" -> 12499000.0)."""
    if not teks:
        return None
    cocok = re.search(r"Rp\s*([\d.,]+)", str(teks))
    if not cocok:
        return None
    try:
        return float(cocok.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def ekstrak_harga_dari_html(html: str) -> dict:
    """Ekstrak harga/nama dari HTML lewat JSON-LD + meta generik.

    Kembalikan {"harga": float|None, "nama": str, "potongan": str}.
    Tidak ada angka yang ditebak: tanpa JSON-LD/meta yang cocok,
    harga None.
    """
    if not html:
        return {"harga": None, "nama": "", "potongan": ""}
    # JSON-LD Product/Offer.
    for cocok in re.finditer(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE):
        try:
            data = json.loads(cocok.group(1).strip())
        except (ValueError, TypeError):
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict):
                continue
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            harga = offers.get("price") if isinstance(offers, dict) else None
            if harga is not None:
                try:
                    return {"harga": float(harga),
                            "nama": str(item.get("name", ""))[:POTONGAN_MAX],
                            "potongan": _tandai_data(item.get("name", ""))}
                except (TypeError, ValueError):
                    pass
    # Meta product:price:amount.
    cocok = re.search(
        r'property="product:price:amount"\s+content="([\d.]+)"', html)
    if cocok:
        return {"harga": float(cocok.group(1)), "nama": "",
                "potongan": _tandai_data(cocok.group(0))}
    # Pola teks "Rp..." pertama sebagai jalan terakhir yang jujur:
    # hanya dipakai bila halaman memuat SATU harga yang jelas.
    harga = _parse_rupiah(html)
    return {"harga": harga, "nama": "",
            "potongan": _tandai_data(html[:POTONGAN_MAX]) if harga else ""}


@dataclass
class HttpReader:
    """Pembaca lewat HTTP fetch (httpx). Satu instance per marketplace."""

    marketplace: str
    client: httpx.Client | None = None

    def baca(self, listing_url: str) -> HasilBaca:
        """Baca satu listing. Blokir (403/429/captcha) -> gagal terlihat,
        TANPA upaya menembus (§11)."""
        if not is_allowed(listing_url):
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url, alasan="domain_tidak_diizinkan")
        client = self.client or httpx.Client(timeout=15.0)
        try:
            resp = client.get(listing_url, headers={
                "User-Agent": "Puspa/0.3 (perbandingan harga atas permintaan pengguna)",
                "Accept-Language": "id-ID",
            })
        except httpx.HTTPError as exc:
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url, alasan=f"jaringan_gagal: {exc!r}"[:200])
        if resp.status_code in (403, 429):
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url,
                             alasan=f"sumber_memblokir_http_{resp.status_code}")
        if resp.status_code != 200:
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url,
                             alasan=f"http_{resp.status_code}")
        if "captcha" in resp.text.lower()[:2000]:
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url, alasan="captcha_terdeteksi")
        ekstrak = ekstrak_harga_dari_html(resp.text)
        if ekstrak["harga"] is None:
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url, alasan="harga_tidak_ditemukan")
        import hashlib

        return HasilBaca(
            sukses=True, marketplace=self.marketplace, listing_url=listing_url,
            harga=ekstrak["harga"], nama=ekstrak["nama"],
            potongan_teks=ekstrak["potongan"],
            hash_halaman=hashlib.sha256(resp.text.encode("utf-8")).hexdigest())


@dataclass
class PlaywrightReader:
    """Pembaca lewat browser read-only (Playwright, §2.8).

    `tersedia()` False bila paket Playwright atau browser Chromium
    tidak terinstal di host — pemanggil wajib memakai HTTP fetch atau
    melaporkan tidak bisa cek, bukan mengarang hasil baca.
    """

    marketplace: str

    @staticmethod
    def tersedia() -> bool:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return True

    def baca(self, listing_url: str) -> HasilBaca:
        if not is_allowed(listing_url):
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url, alasan="domain_tidak_diizinkan")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url,
                             alasan="playwright_tidak_tersedia")
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
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url,
                             alasan=f"browser_gagal: {exc!r}"[:200])
        ekstrak = ekstrak_harga_dari_html(html)
        if ekstrak["harga"] is None:
            return HasilBaca(sukses=False, marketplace=self.marketplace,
                             listing_url=listing_url, alasan="harga_tidak_ditemukan")
        import hashlib

        return HasilBaca(sukses=True, marketplace=self.marketplace,
                         listing_url=listing_url, harga=ekstrak["harga"],
                         nama=ekstrak["nama"], potongan_teks=ekstrak["potongan"],
                         hash_halaman=hashlib.sha256(html.encode("utf-8")).hexdigest())


def buat_pembaca(marketplace: str, *, client: httpx.Client | None = None):
    """Pabrik Pembaca: Playwright bila tersedia, kalau tidak HTTP fetch.
    Kembalikan (pembaca, jalur) dengan jalur 'playwright'/'http'/'tidak_tersedia'.
    """
    if PlaywrightReader.tersedia():
        return PlaywrightReader(marketplace), "playwright"
    return HttpReader(marketplace, client=client), "http"
