"""Situs uji lokal untuk wedge tagihan (§4.2, §3.3 revisi 7).

Kenapa fixture: nomor pelanggan PLN/PDAM + nomor HP Nero (keputusan 5)
BELUM ada, dan situs resmi PLN memakai login + captcha — jadi canary
+ uji tagihan live menunggu nomor Nero. Sementara itu, prosedur
ditemukan, ditulis, diverifikasi, dan canary dijalankan terhadap
situs uji lokal ini (data uji, bukan data pribadi).

Server HTTP kecil (stdlib saja, tanpa dependensi baru) yang menyajikan
halaman tagihan per provider dengan nilai yang diketahui. `ubah`
dipakai uji suntik canary: mengubah nilai di situs uji, canary harus
mendeteksi perubahan itu sebelum dipakai user (§3.6).
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar
from urllib.parse import quote, unquote

# Nilai uji (bukan tagihan nyata siapa pun).
DATA_TEST: dict[str, dict[str, Any]] = {
    "pln": {
        "amount": 132500,
        "due_date": "2026-11-20",
        "status": "not_yet_pay",
        "period": "November 2026",
    },
    "pdam": {
        "amount": 87500,
        "due_date": "2026-11-25",
        "status": "not_yet_pay",
        "period": "November 2026",
    },
    "airtime": {"amount": 50000, "due_date": None, "status": "active", "period": "balance"},
    "paket data": {
        "amount": 75000,
        "due_date": "2026-12-01",
        "status": "active",
        "period": "30 hari",
    },
}


class _Handler(BaseHTTPRequestHandler):
    data: ClassVar[dict] = dict(DATA_TEST)

    def log_message(self, *args):  # diam — jangan penuhi log tes
        pass

    def do_GET(self):
        provider = unquote(self.path.strip("/").split("/")[0].split("?")[0]).lower()
        if provider not in self.data:
            self.send_response(404)
            self.end_headers()
            return
        content = self.data[provider]
        html = (
            "<html><head><title>Tagihan</title></head><body>"
            f"<h1>Tagihan {provider}</h1>"
            f"<p>Jumlah tagihan: Rp{content['amount']:,}</p>".replace(",", ".")
            + (f"<p>Jatuh tempo: {content['due_date']}</p>" if content["due_date"] else "")
            + f"<p>Status: {content['status']}</p>"
            + f"<p>Periode: {content['period']}</p>"
            + "</body></html>"
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SiteFixture:
    """Situs uji tagihan di localhost (port acak)."""

    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def url(self, provider: str) -> str:
        return f"http://127.0.0.1:{self.port}/{quote(provider, safe='')}"

    @staticmethod
    def change(provider: str, **value) -> None:
        """Suntik perubahan ke situs uji (untuk uji canary §3.6)."""
        _Handler.data[provider] = {**_Handler.data[provider], **value}

    @staticmethod
    def restore(provider: str) -> None:
        _Handler.data[provider] = dict(DATA_TEST[provider])
