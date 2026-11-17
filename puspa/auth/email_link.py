"""Tautan email sebagai cadangan login (keputusan 16, C117).

Login utama: Google. Cadangan: tautan sekali pakai yang dikirim ke
email user (tanpa kata sandi). Token = email + kedaluwarsa + HMAC
(secret dari env `PUSPA_EMAIL_LINK_SECRET`); stateless kecuali tabel
sekali-pakai `tautan_email_digunakan` (token yang sudah dipakai
ditolak — replay tidak bisa).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import psycopg

from ..memory.schema import resolve_dsn

#: Masa berlaku tautan: 15 menit.
LINK_TTL_SECONDS = 900


class EmailLinkError(Exception):
    """Tautan email tidak valid / kedaluwarsa / sudah dipakai."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _secret():
    secret = os.environ.get("PUSPA_EMAIL_LINK_SECRET")
    if not secret:
        raise EmailLinkError("PUSPA_EMAIL_LINK_SECRET tidak diset")
    return secret.encode()


def create_email_link(email, base_url, secret=None, ttl_seconds=LINK_TTL_SECONDS,
                      now=None):
    """Buat URL tautan login sekali pakai untuk `email`.

    Kembalikan URL lengkap. Token TIDAK disimpan (hanya hash-nya yang
    dicatat saat dipakai).
    """
    email_n = email.strip().lower()
    if not email_n:
        raise ValueError("email kosong")
    secret = secret or _secret().decode()
    exp = int((now or time.time()) + ttl_seconds)
    payload = f"{email_n}|{exp}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = _b64url(f"{payload}|{sig}".encode())
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}token={token}"


def verify_email_link(token, secret=None, dsn=None, now=None):
    """Verifikasi tautan; kembalikan email. Tandai token sudah dipakai.

    Raise EmailLinkError kalau format salah, signature salah,
    kedaluwarsa, atau sudah dipakai.
    """
    secret = secret or _secret().decode()
    try:
        raw = _b64url_decode(token).decode()
        email_n, exp, sig = raw.split("|")
        exp = int(exp)
    except Exception as exc:
        raise EmailLinkError("token tidak bisa dibaca") from exc
    payload = f"{email_n}|{exp}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise EmailLinkError("signature tautan tidak valid")
    if exp < int(now or time.time()):
        raise EmailLinkError("tautan kedaluwarsa")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM used_email_links WHERE token_hash = %s",
                (token_hash,),
            )
            if cur.fetchone() is not None:
                raise EmailLinkError("tautan sudah dipakai")
            cur.execute(
                "INSERT INTO used_email_links (token_hash) VALUES (%s)",
                (token_hash,),
            )
        conn.commit()
    return email_n
