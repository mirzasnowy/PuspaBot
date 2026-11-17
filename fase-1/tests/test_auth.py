"""Tes Login Google + tautan email (C117, keputusan 16)."""

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from puspa.auth import (
    EmailLinkError,
    OAuthError,
    build_auth_url,
    complete_google_login,
    create_email_link,
    create_google_user,
    delete_tokens,
    exchange_code,
    find_user_by_email,
    load_tokens,
    refresh_tokens,
    revoke_tokens,
    store_tokens,
    verify_email_link,
    verify_id_token,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeHttp:
    """HTTP mock: token endpoint + userinfo + revoke."""

    def __init__(self, id_token="id-token-palsu"):
        self.id_token = id_token
        self.posts = []

    def post(self, url, data=None, **kwargs):
        self.posts.append((url, data))
        if "oauth2.googleapis.com/token" in url:
            if data.get("grant_type") == "refresh_token":
                return FakeResponse(200, {"access_token": "access-baru", "expires_in": 3600})
            return FakeResponse(200, {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "id_token": self.id_token,
                "expires_in": 3600,
            })
        if "oauth2.googleapis.com/revoke" in url:
            return FakeResponse(200, {})
        return FakeResponse(404, {})

    def get(self, url, headers=None, **kwargs):
        return FakeResponse(200, {"sub": "google-sub-1", "email": "Nero@Contoh.id"})


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _buat_jwt_dan_jwks():
    """Kunci RSA baru + JWT RS256 + JWKS yang cocok (untuk tes saja)."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()
    n = _b64url(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big"))
    e = _b64url(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big"))
    jwks = {"keys": [{"kid": "kunci-tes-1", "kty": "RSA", "n": n, "e": e}]}
    header = _b64url(json.dumps({"alg": "RS256", "kid": "kunci-tes-1"}).encode())
    payload = _b64url(json.dumps({
        "iss": "https://accounts.google.com",
        "aud": "client-id-tes",
        "sub": "google-sub-1",
        "email": "nero@contoh.id",
        "exp": int(time.time()) + 3600,
    }).encode())
    signature = private.sign(
        f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    return f"{header}.{payload}.{_b64url(signature)}", jwks


def test_auth_url_pkce_state_dan_challenge():
    hasil = build_auth_url(
        "client-id-tes", "https://contoh.id/callback",
        state="state-tetap", code_verifier="v" * 50,
    )
    assert "accounts.google.com" in hasil["url"]
    assert "code_challenge_method=S256" in hasil["url"]
    assert "state=state-tetap" in hasil["url"]
    assert hasil["state"] == "state-tetap"
    assert hasil["code_verifier"] == "v" * 50
    # Challenge = base64url(SHA256(verifier)).
    import hashlib

    expected = _b64url(hashlib.sha256(b"v" * 50).digest())
    assert hasil["code_challenge"] == expected
    assert "scope=openid+email+profile" in hasil["url"]
    with pytest.raises(ValueError):
        build_auth_url("c", "https://contoh.id/callback", code_verifier="pendek")


def test_exchange_refresh_revoke_dengan_http_mock():
    http = FakeHttp()
    tokens = exchange_code(
        "code-1", "v" * 50, "cid", "secret", "https://contoh.id/callback",
        http_client=http,
    )
    assert tokens["access_token"] == "access-1"
    baru = refresh_tokens("refresh-1", "cid", "secret", http_client=http)
    assert baru["access_token"] == "access-baru"
    assert revoke_tokens("access-1", http_client=http) is True


def test_verify_id_token_asli_dan_palsu():
    jwt, jwks = _buat_jwt_dan_jwks()
    payload = verify_id_token(jwt, jwks, "client-id-tes")
    assert payload["email"] == "nero@contoh.id"
    with pytest.raises(OAuthError):
        verify_id_token(jwt, jwks, "client-lain")  # aud salah
    with pytest.raises(OAuthError):
        verify_id_token(jwt + "x", jwks, "client-id-tes")  # signature rusak
    with pytest.raises(OAuthError):
        verify_id_token("bukan.jwt", jwks, "client-id-tes")


def test_login_google_lengkap_buat_user_dan_sesi(
    make_user, test_dsn, vault_master_key
):
    jwt, jwks = _buat_jwt_dan_jwks()
    http = FakeHttp(id_token=jwt)
    hasil = complete_google_login(
        "code-1", "v" * 50, "client-id-tes", "secret",
        "https://contoh.id/callback", jwks,
        device_label="laptop", http_client=http, dsn=test_dsn,
    )
    assert hasil["email"] == "nero@contoh.id"
    assert hasil["baru"] is True
    assert hasil["session_token"]
    # Token tersimpan terenkripsi di vault; login kedua memakai user sama.
    assert load_tokens(hasil["user_id"], dsn=test_dsn)["refresh_token"] == "refresh-1"
    hasil2 = complete_google_login(
        "code-2", "v" * 50, "client-id-tes", "secret",
        "https://contoh.id/callback", jwks,
        device_label="hp", http_client=http, dsn=test_dsn,
    )
    assert hasil2["user_id"] == hasil["user_id"] and hasil2["baru"] is False
    assert find_user_by_email("NERO@contoh.id", dsn=test_dsn) == hasil["user_id"]


def test_pengguna_google_only_tidak_pakai_kata_sandi_biasa(test_dsn):
    from puspa.accounts.users import AuthError, authenticate

    user_id = create_google_user("baru@contoh.id", dsn=test_dsn)
    assert user_id
    with pytest.raises(AuthError):
        authenticate("baru@contoh.id", "tebakan", dsn=test_dsn)
    with pytest.raises(ValueError):
        create_google_user("baru@contoh.id", dsn=test_dsn)  # duplikat


def test_token_vault_bisa_dihapus(make_user, test_dsn, vault_master_key):
    u = make_user()
    store_tokens(u["user_id"], {"access_token": "x"}, dsn=test_dsn)
    assert load_tokens(u["user_id"], dsn=test_dsn) == {"access_token": "x"}
    delete_tokens(u["user_id"], dsn=test_dsn)
    assert load_tokens(u["user_id"], dsn=test_dsn) is None


# --- Tautan email -----------------------------------------------------------

def test_tautan_email_sekali_pakai(email_link_secret, test_dsn):
    url = create_email_link("nero@contoh.id", "https://contoh.id/masuk", secret=email_link_secret)
    token = url.split("token=")[1]
    assert verify_email_link(token, secret=email_link_secret, dsn=test_dsn) == "nero@contoh.id"
    with pytest.raises(EmailLinkError):  # sudah dipakai
        verify_email_link(token, secret=email_link_secret, dsn=test_dsn)


def test_tautan_email_kedaluwarsa_dan_dipalsukan(email_link_secret, test_dsn):
    url = create_email_link(
        "nero@contoh.id", "https://contoh.id/masuk",
        secret=email_link_secret, ttl_seconds=-10,
    )
    token = url.split("token=")[1]
    with pytest.raises(EmailLinkError):
        verify_email_link(token, secret=email_link_secret, dsn=test_dsn)
    url2 = create_email_link("nero@contoh.id", "https://contoh.id/masuk", secret=email_link_secret)
    token2 = url2.split("token=")[1]
    with pytest.raises(EmailLinkError):
        verify_email_link(token2, secret="secret-salah", dsn=test_dsn)
    with pytest.raises(EmailLinkError):
        verify_email_link("token-asal", secret=email_link_secret, dsn=test_dsn)
