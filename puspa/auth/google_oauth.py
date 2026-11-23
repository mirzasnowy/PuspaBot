"""Login Google — OAuth 2.0 + PKCE (definisi 2.15, keputusan 16, C117).

MENGGANTIKAN login email + kata sandi Argon2id Fase 0 (users.py tetap
ada sebagai fondasi, jangan hapus). Sesi per perangkat (sessions.py)
+ vault (vault.py) DIPAKAI ULANG tanpa perubahan.

Alur: authorization code + PKCE (S256), token disimpan di vault
terenkripsi (nama `google_oauth`), scope minimal login
(`openid email profile`). Token konektor Gmail/Drive/Calendar (scope
lebih luas) ditambahkan Fase 2 — di sini hanya login.

BLOKIR YANG DIKETAHUI (lihat STATUS_FASE1.md): publikasi aplikasi
Google OAuth ke "In production" butuh browser Nero; status Testing
membuat refresh token kedaluwarsa 7 hari. Kode di sini lengkap +
diuji dengan HTTP mock + JWKS palsu; publikasi adalah langkah Nero
yang tertunda.

Keamanan:
- `state` acak per login (lawan CSRF); pemanggil wajib memeriksa
  state yang kembali sama dengan yang dikirim.
- `code_verifier` acak 43-128 karakter, `code_challenge` S256.
- Client secret + token TIDAK PERNAH ditulis ke file/log — secret
  hanya dari env `PUSPA_GOOGLE_CLIENT_SECRET`.
- Akun Google-only: baris `pengguna` dibuat dengan hash kata sandi
  acak yang tidak diketahui siapa pun (kolom `password_hash` NOT NULL
  di skema Fase 0); login kata sandi tidak dipakai lagi.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets

from puspa.db import connect as _db_connect
from puspa.db import dict_row

from ..memory.schema import resolve_dsn

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
ISSUER = "https://accounts.google.com"

#: Scope minimal untuk LOGIN saja (bukan konektor).
LOGIN_SCOPES = ("openid", "email", "profile")

VAULT_NAME = "google_oauth"


class OAuthError(Exception):
    """Alur OAuth gagal (token ditolak, ID token tidak valid, ...)."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return _b64url(digest)


def build_auth_url(client_id, redirect_uri, scopes=None, state=None, code_verifier=None):
    """Bangun URL otorisasi + kembalikan state/verifier/challenge.

    Pemanggil menyimpan `state` + `code_verifier` di sesi browser
    (httpOnly) lalu memeriksa `state` saat callback.
    """
    from urllib.parse import urlencode

    scopes = tuple(scopes or LOGIN_SCOPES)
    state = state or secrets.token_urlsafe(24)
    code_verifier = code_verifier or secrets.token_urlsafe(48)
    if not 43 <= len(code_verifier) <= 128:
        raise ValueError("code_verifier harus 43-128 karakter")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": _code_challenge(code_verifier),
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return {
        "url": AUTH_URL + "?" + urlencode(params),
        "state": state,
        "code_verifier": code_verifier,
        "code_challenge": params["code_challenge"],
    }


def _http_post(http_client, url, data):
    if http_client is None:
        import httpx

        with httpx.Client(timeout=30) as client:
            resp = client.post(url, data=data)
    else:
        resp = http_client.post(url, data=data)
    if resp.status_code != 200:
        raise OAuthError(f"token endpoint menolak: HTTP {resp.status_code}")
    return resp.json()


def exchange_code(code, code_verifier, client_id, client_secret, redirect_uri, http_client=None):
    """Tukar authorization code -> token (access + refresh + id_token)."""
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    tokens = _http_post(http_client, TOKEN_URL, data)
    if "access_token" not in tokens:
        raise OAuthError("token endpoint tidak mengembalikan access_token")
    return tokens


def fetch_jwks(http_client=None):
    """Ambil JWKS Google untuk verifikasi id_token."""
    if http_client is None:
        import httpx

        with httpx.Client(timeout=30) as client:
            resp = client.get(JWKS_URL)
    else:
        resp = http_client.get(JWKS_URL)
    if resp.status_code != 200:
        raise OAuthError(f"JWKS endpoint menolak: HTTP {resp.status_code}")
    return resp.json()


def refresh_tokens(refresh_token, client_id, client_secret, http_client=None):
    """Tukar refresh token -> access token baru."""
    data = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }
    tokens = _http_post(http_client, TOKEN_URL, data)
    if "access_token" not in tokens:
        raise OAuthError("refresh token ditolak")
    return tokens


def revoke_tokens(token, http_client=None):
    """Cabut token di Google (pencabutan login/konektor)."""
    if http_client is None:
        import httpx

        with httpx.Client(timeout=30) as client:
            resp = client.post(REVOKE_URL, data={"token": token})
    else:
        resp = http_client.post(REVOKE_URL, data={"token": token})
    if resp.status_code != 200:
        raise OAuthError(f"revoke ditolak: HTTP {resp.status_code}")
    return True


def fetch_userinfo(access_token, http_client=None):
    """Ambil profil userinfo (sub, email) dengan access token."""
    if http_client is None:
        import httpx

        with httpx.Client(timeout=30) as client:
            resp = client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    else:
        resp = http_client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code != 200:
        raise OAuthError(f"userinfo ditolak: HTTP {resp.status_code}")
    info = resp.json()
    if "sub" not in info or "email" not in info:
        raise OAuthError("userinfo tidak memuat sub/email")
    return info


def _rsa_public_key_from_jwk(jwk):
    """Bangun kunci publik RSA dari satu entri JWKS (n, e base64url)."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    def _b64_to_int(value):
        padded = value + "=" * (-len(value) % 4)
        return int.from_bytes(base64.urlsafe_b64decode(padded), "big")

    numbers = rsa.RSAPublicNumbers(_b64_to_int(jwk["e"]), _b64_to_int(jwk["n"]))
    return numbers.public_key()


def verify_id_token(id_token, jwks, audience):
    """Verifikasi ID token Google (RS256, iss, aud, exp).

    `jwks`: dict JWKS standar {"keys": [{"kid","kty":"RSA","n","e"}]}.
    Kembalikan payload (dict). Raise OAuthError kalau tidak valid.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        header_b64, payload_b64, sig_b64 = id_token.split(".")
    except ValueError as exc:
        raise OAuthError("id_token bukan JWT") from exc

    def _decode(segment):
        padded = segment + "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))

    try:
        header = _decode(header_b64)
        payload = _decode(payload_b64)
        signature = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    except Exception as exc:
        raise OAuthError("id_token tidak bisa di-decode") from exc
    if header.get("alg") != "RS256":
        raise OAuthError("id_token bukan RS256")
    key = None
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == header.get("kid") and jwk.get("kty") == "RSA":
            key = _rsa_public_key_from_jwk(jwk)
            break
    if key is None:
        raise OAuthError("kunci JWKS untuk kid tidak ditemukan")
    signed = f"{header_b64}.{payload_b64}".encode()
    try:
        key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise OAuthError("signature id_token tidak valid") from exc
    if payload.get("iss") not in (ISSUER, "accounts.google.com"):
        raise OAuthError("issuer id_token tidak dikenal")
    if payload.get("aud") != audience:
        raise OAuthError("audience id_token tidak cocok")
    import time

    if payload.get("exp", 0) < int(time.time()):
        raise OAuthError("id_token kedaluwarsa")
    return payload


def store_tokens(user_id, tokens, dsn=None):
    """Simpan token OAuth ke vault terenkripsi (nama `google_oauth`)."""
    from puspa.accounts.vault import put as vault_put

    vault_put(user_id, VAULT_NAME, json.dumps(tokens), dsn=dsn)


def load_tokens(user_id, dsn=None):
    """Muat token OAuth dari vault; None kalau belum ada."""
    from puspa.accounts.vault import get as vault_get

    try:
        raw = vault_get(user_id, VAULT_NAME, dsn=dsn)
    except KeyError:
        return None
    return json.loads(raw) if raw is not None else None


def delete_tokens(user_id, dsn=None):
    """Hapus token OAuth dari vault (cabut lokal)."""
    from puspa.accounts.vault import delete as vault_delete

    vault_delete(user_id, VAULT_NAME, dsn=dsn)


def _connect(dsn=None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


def find_user_by_email(email, dsn=None):
    """Cari user_id dari email (normalisasi seperti users.py)."""
    email_n = email.strip().lower()
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email_n,))
        row = cur.fetchone()
    return str(row["id"]) if row else None


def create_google_user(email, dsn=None):
    """Buat pengguna Google-only (hash kata sandi acak tak diketahui).

    Kembalikan user_id. Raise ValueError kalau email sudah terdaftar.
    """
    from argon2 import PasswordHasher

    from puspa.db import errors as pg_errors

    email_n = email.strip().lower()
    if not email_n:
        raise ValueError("email kosong")
    # Kata sandi acak 48 byte — tidak disimpan, tidak diketahui siapa
    # pun; kolom password_hash NOT NULL di skema Fase 0.
    password_hash = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=1).hash(
        secrets.token_urlsafe(48)
    )
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                    (email_n, password_hash),
                )
            except pg_errors.UniqueViolation as exc:
                raise ValueError(f"email sudah terdaftar: {email_n}") from exc
            row = cur.fetchone()
        conn.commit()
    return str(row["id"])


def complete_google_login(
    code,
    code_verifier,
    client_id,
    client_secret,
    redirect_uri,
    jwks,
    device_label="perangkat",
    http_client=None,
    dsn=None,
):
    """Selesaikan login Google: tukar code -> verifikasi ID -> sesi.

    Kembalikan {user_id, email, session_token, baru}. Sesi dibuat
    lewat sessions.py Fase 0 (dipakai ulang, tanpa perubahan).
    """
    from puspa.accounts.ports import create_session

    tokens = exchange_code(
        code,
        code_verifier,
        client_id,
        client_secret,
        redirect_uri,
        http_client=http_client,
    )
    payload = verify_id_token(tokens.get("id_token", ""), jwks, client_id)
    email = payload.get("email")
    if not email:
        raise OAuthError("id_token tidak memuat email")
    user_id = find_user_by_email(email, dsn=dsn)
    new = False
    if user_id is None:
        user_id = create_google_user(email, dsn=dsn)
        new = True
    store_tokens(user_id, tokens, dsn=dsn)
    session_token = create_session(user_id, device_label, dsn=dsn)
    return {
        "user_id": user_id,
        "email": email,
        "session_token": session_token,
        "new": new,
    }


def google_client_secret():
    """Client secret Google hanya dari env (tidak pernah dari file)."""
    secret = os.environ.get("PUSPA_GOOGLE_CLIENT_SECRET")
    if not secret:
        raise OAuthError("PUSPA_GOOGLE_CLIENT_SECRET tidak diset")
    return secret
