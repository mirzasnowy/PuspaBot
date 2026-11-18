"""Web Push (VAPID) dari PWA (§2.14 revisi 7).

- Kunci VAPID (EC P-256) dibuat sekali; kunci publik dibagikan ke
  service worker, kunci privat TIDAK PERNAH keluar dari server.
- JWT VAPID ditandatangani ES256 (RFC 8292) — dibuat sendiri dengan
  `cryptography`, tanpa dependensi baru.
- Isi notifikasi dienkripsi aes128gcm (RFC 8291): ECDH P-256 + HKDF
  + AES-GCM. `enkripsi_payload` / `dekripsi_payload` diuji bolak-balik
  di tes (dekripsi memakai kunci privat penerima).
- Kirim lewat `httpx` (client disuntikkan; di tes `MockTransport`).
  Tanpa payload (bangunkan saja) juga didukung: `kirim(..., isi=None)`
  mengirim push kosong yang sah (RFC 8030) — dipakai bila enkripsi
  tidak dibutuhkan; PWA lalu mengambil isi dari server.

Batas yang jujur dicatat: di iOS, push hanya jalan kalau PWA
dipasang ke layar utama. Onboarding menguji kirim dan menampilkan
peringatan kalau notif tidak bisa menjangkau user (lihat PWA).
"""

from __future__ import annotations

import base64
import json
import os
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class PushError(Exception):
    """Web Push gagal (kunci, enkripsi, atau pengiriman)."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_vapid_key() -> dict:
    """Buat pasangan kunci VAPID baru. Kembalikan dict kunci publik
    (b64url, untuk service worker) + privat (b64url, simpan aman)."""
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_numbers()
    x = public.x.to_bytes(32, "big")
    y = public.y.to_bytes(32, "big")
    public_key = _b64url(b"\x04" + x + y)
    d = private.private_numbers().private_value.to_bytes(32, "big")
    return {"public": public_key, "private": _b64url(d)}


def _load_private(private_b64: str):
    d = int.from_bytes(_b64url_decode(private_b64), "big")
    return ec.derive_private_key(d, ec.SECP256R1())


def _load_public(public_b64: str):
    raw = _b64url_decode(public_b64)
    if len(raw) != 65 or raw[0] != 4:
        raise PushError("kunci publik VAPID tidak valid")
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:65], "big"), ec.SECP256R1()
    ).public_key()


def _jwt_vapid(
    *, endpoint: str, subject: str, private_b64: str, expiry_seconds: int = 12 * 3600
) -> str:
    """JWT VAPID ES256: aud = origin endpoint, exp = 12 jam."""
    from urllib.parse import urlparse

    aud = f"{urlparse(endpoint).scheme}://{urlparse(endpoint).netloc}"
    header = _b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"aud": aud, "exp": int(time.time()) + expiry_seconds, "sub": subject},
            separators=(",", ":"),
        ).encode()
    )
    message = f"{header}.{payload}".encode()
    private = _load_private(private_b64)
    der = private.sign(message, ec.ECDSA(hashes.SHA256()))
    # DER -> raw r||s (JOSE).
    r_len = der[3]
    r = der[4 : 4 + r_len]
    s = der[4 + r_len + 2 :]
    r = r.rjust(32, b"\x00")[-32:]
    s = s.rjust(32, b"\x00")[-32:]
    return f"{header}.{payload}.{_b64url(r + s)}"


def _hkdf(salt: bytes, ikm: bytes, info: bytes, long: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=long, salt=salt, info=info).derive(ikm)


def encrypt_payload(
    content: bytes, *, p256dh_recipient_b64: str, auth_b64: str
) -> tuple[bytes, str, str]:
    """Enkripsi aes128gcm. Kembalikan (body, kunci_publik_server_b64, garam_b64)."""
    recipient = _load_public(p256dh_recipient_b64)
    auth = _b64url_decode(auth_b64)
    server_private = ec.generate_private_key(ec.SECP256R1())
    server_public_key = server_private.public_key()
    shared = server_private.exchange(ec.ECDH(), recipient)
    salt = os.urandom(16)
    info_key = (
        b"WebPush: info\x00"
        + _b64url_decode(p256dh_recipient_b64)
        + _b64url_decode(
            _b64url(
                server_public_key.public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                )
            )
        )
    )
    prk = _hkdf(auth, shared, info_key, 32)
    key = _hkdf(salt, prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, prk, b"Content-Encoding: nonce\x00", 12)
    content_pad = content + b"\x02"  # delimiter padding (RFC 8291)
    body = AESGCM(key).encrypt(nonce, content_pad, None)
    server_public_b64 = _b64url(
        server_public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    return body, server_public_b64, _b64url(salt)


def decrypt_payload(
    body: bytes, *, salt_b64: str, server_key_b64: str, recipient_private_b64: str, auth_b64: str
) -> bytes:
    """Kebalikan `enkripsi_payload` (untuk tes bolak-balik)."""
    recipient_private = _load_private(recipient_private_b64)
    server_public_key = _load_public(server_key_b64)
    shared = recipient_private.exchange(ec.ECDH(), server_public_key)
    salt = _b64url_decode(salt_b64)
    auth = _b64url_decode(auth_b64)
    recipient_public_b64 = _b64url(
        recipient_private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    info_key = (
        b"WebPush: info\x00" + _b64url_decode(recipient_public_b64) + _b64url_decode(server_key_b64)
    )
    prk = _hkdf(auth, shared, info_key, 32)
    key = _hkdf(salt, prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, prk, b"Content-Encoding: nonce\x00", 12)
    plaintext = AESGCM(key).decrypt(nonce, body, None)
    return plaintext[:-1]  # buang delimiter


def send(
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    content: bytes | None,
    vapid_public: str,
    vapid_private: str,
    subject: str = "mailto:puspa@contoh.id",
    http_client=None,
    ttl: int = 86400,
) -> dict:
    """Kirim satu push. `isi=None` = push kosong (bangunkan saja)."""
    jwt = _jwt_vapid(endpoint=endpoint, subject=subject, private_b64=vapid_private)
    headers = {"Authorization": f"vapid t={jwt}, k={vapid_public}", "TTL": str(ttl)}
    if content is None:
        body, headers["Content-Length"] = b"", "0"
    else:
        body, server_key, salt = encrypt_payload(
            content, p256dh_recipient_b64=p256dh, auth_b64=auth
        )
        headers["Content-Encoding"] = "aes128gcm"
        headers["Encryption"] = f"salt={salt}"
        headers["Crypto-Key"] = f"dh={server_key}"
        headers["Content-Length"] = str(len(body))
    client = http_client or httpx.Client(timeout=30.0)
    resp = client.post(endpoint, content=body, headers=headers)
    if resp.status_code not in (200, 201, 202):
        raise PushError(f"push gagal: {resp.status_code}")
    return {"status": resp.status_code, "endpoint": endpoint}


def save_subscription(user_id: str, *, endpoint: str, p256dh: str, auth: str, dsn=None) -> str:
    """Simpan langganan push pengguna. Kembalikan id langganan."""
    import uuid

    import psycopg

    dsn = dsn or os.environ.get(
        "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
    )
    subscription_id = f"push-{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO push_subscriptions (id, user_id, endpoint, p256dh, auth)"
                " VALUES (%s, %s, %s, %s, %s)",
                (subscription_id, user_id, endpoint, p256dh, auth),
            )
        conn.commit()
    return subscription_id


def list_subscriptions(user_id: str, dsn=None) -> list[dict]:
    """Semua langganan push satu pengguna."""
    import psycopg

    dsn = dsn or os.environ.get(
        "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
    )
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = %s",
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]
