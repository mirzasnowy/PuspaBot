"""Browser-facing OAuth error pages (desktop fix round, M8).

``/auth/google`` and ``/auth/google/callback`` are only ever
opened in a browser, so their errors must be human pages, never
raw JSON. A raw ``{"error": ...}`` in the browser is a dead end:
the user cannot act on it, and the desktop app cannot see it.
Every page here says what happened in plain language and what to
do next, in the Puspa look (cream, red, rounded card).
"""

from __future__ import annotations

import html

from fastapi.responses import HTMLResponse

_PAGE = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<title>{title} - PuspaBot</title>
<style>
body{{background:#FFF5F2;color:#1C1C1C;font-family:system-ui,sans-serif;margin:0;padding:48px 24px}}
main{{max-width:560px;margin:0 auto;background:#fff;border:1px solid #F0DDE0;border-radius:24px;padding:32px}}
h1{{color:#D7404D;font-size:24px;margin:0 0 12px}}
p{{line-height:1.6;margin:0 0 12px}}
</style></head><body><main><h1>{title}</h1><p>{message}</p><p>{hint}</p>
</main></body></html>"""


def oauth_error_page(title: str, message: str, hint: str, status_code: int = 503) -> HTMLResponse:
    """One human error page. All three parts are escaped: the
    reason can carry text from Google's responses."""
    return HTMLResponse(
        _PAGE.format(
            title=html.escape(title),
            message=html.escape(message),
            hint=html.escape(hint),
        ),
        status_code=status_code,
    )


def not_configured_page() -> HTMLResponse:
    """No Google OAuth client exists for this backend yet."""
    return oauth_error_page(
        "Login Google belum disiapkan",
        "PuspaBot di komputer ini belum punya kredensial login Google, "
        "jadi login tidak bisa dimulai.",
        "Buat kredensialnya di Google Cloud Console (tipe Desktop app), "
        "lalu masukkan Client ID dan Client Secret di aplikasi PuspaBot, "
        "menu Pengaturan Lainnya.",
    )


def state_mismatch_page() -> HTMLResponse:
    """The OAuth round trip did not match (stale or double-opened link)."""
    return oauth_error_page(
        "Login tidak cocok",
        "Tautan login ini sudah tidak berlaku atau dibuka dua kali.",
        "Tutup tab ini dan tekan tombol Masuk dengan Google di aplikasi PuspaBot.",
        status_code=400,
    )


def oauth_failed_page(reason: str) -> HTMLResponse:
    """Google refused the login (token exchange, ID token, ...)."""
    return oauth_error_page(
        "Login Google gagal",
        f"Google menolak login: {reason}",
        "Coba lagi dari aplikasi PuspaBot. Kalau terus gagal, periksa "
        "Client ID dan Client Secret di Pengaturan Lainnya.",
        status_code=400,
    )
