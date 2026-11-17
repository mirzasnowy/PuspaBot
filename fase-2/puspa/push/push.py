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
import hashlib
import hmac as hmac_mod
import json
import os
import struct
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


def buat_kunci_vapid() -> dict:
    """Buat pasangan kunci VAPID baru. Kembalikan dict kunci publik
    (b64url, untuk service worker) + privat (b64url, simpan aman)."""
    privat = ec.generate_private_key(ec.SECP256R1())
    publik = privat.public_key().public_numbers()
    x = publik.x.to_bytes(32, "big")
    y = publik.y.to_bytes(32, "big")
    kunci_publik = _b64url(b"\x04" + x + y)
    d = privat.private_numbers().private_value.to_bytes(32, "big")
    return {"publik": kunci_publik, "privat": _b64url(d)}


def _muat_privat(privat_b64: str):
    d = int.from_bytes(_b64url_decode(privat_b64), "big")
    return ec.derive_private_key(d, ec.SECP256R1())


def _muat_publik(publik_b64: str):
    raw = _b64url_decode(publik_b64)
    if len(raw) != 65 or raw[0] != 4:
        raise PushError("kunci publik VAPID tidak valid")
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:65], "big"),
        ec.SECP256R1()).public_key()


def _jwt_vapid(*, endpoint: str, subjek: str, privat_b64: str,
               kedaluwarsa_detik: int = 12 * 3600) -> str:
    """JWT VAPID ES256: aud = origin endpoint, exp = 12 jam."""
    from urllib.parse import urlparse

    aud = f"{urlparse(endpoint).scheme}://{urlparse(endpoint).netloc}"
    header = _b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps(
        {"aud": aud, "exp": int(time.time()) + kedaluwarsa_detik, "sub": subjek},
        separators=(",", ":")).encode())
    pesan = f"{header}.{payload}".encode()
    privat = _muat_privat(privat_b64)
    der = privat.sign(pesan, ec.ECDSA(hashes.SHA256()))
    # DER -> raw r||s (JOSE).
    r_len = der[3]
    r = der[4:4 + r_len]
    s = der[4 + r_len + 2:]
    r = r.rjust(32, b"\x00")[-32:]
    s = s.rjust(32, b"\x00")[-32:]
    return f"{header}.{payload}.{_b64url(r + s)}"


def _hkdf(garam: bytes, ikm: bytes, info: bytes, panjang: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=panjang, salt=garam, info=info).derive(ikm)


def enkripsi_payload(isi: bytes, *, p256dh_penerima_b64: str, auth_b64: str) -> tuple[bytes, str, str]:
    """Enkripsi aes128gcm. Kembalikan (body, kunci_publik_server_b64, garam_b64)."""
    penerima = _muat_publik(p256dh_penerima_b64)
    auth = _b64url_decode(auth_b64)
    server_privat = ec.generate_private_key(ec.SECP256R1())
    server_publik = server_privat.public_key()
    shared = server_privat.exchange(ec.ECDH(), penerima)
    garam = os.urandom(16)
    info_kunci = b"WebPush: info\x00" + _b64url_decode(p256dh_penerima_b64) + _b64url_decode(
        _b64url(server_publik.public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)))
    prk = _hkdf(auth, shared, info_kunci, 32)
    kunci = _hkdf(garam, prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(garam, prk, b"Content-Encoding: nonce\x00", 12)
    isi_pad = isi + b"\x02"  # delimiter padding (RFC 8291)
    badan = AESGCM(kunci).encrypt(nonce, isi_pad, None)
    publik_server = _b64url(server_publik.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    return badan, publik_server, _b64url(garam)


def dekripsi_payload(badan: bytes, *, garam_b64: str, kunci_server_b64: str,
                     privat_penerima_b64: str, auth_b64: str) -> bytes:
    """Kebalikan `enkripsi_payload` (untuk tes bolak-balik)."""
    penerima_privat = _muat_privat(privat_penerima_b64)
    server_publik = _muat_publik(kunci_server_b64)
    shared = penerima_privat.exchange(ec.ECDH(), server_publik)
    garam = _b64url_decode(garam_b64)
    auth = _b64url_decode(auth_b64)
    publik_penerima = _b64url(penerima_privat.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    info_kunci = b"WebPush: info\x00" + _b64url_decode(publik_penerima) + _b64url_decode(kunci_server_b64)
    prk = _hkdf(auth, shared, info_kunci, 32)
    kunci = _hkdf(garam, prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(garam, prk, b"Content-Encoding: nonce\x00", 12)
    jelas = AESGCM(kunci).decrypt(nonce, badan, None)
    return jelas[:-1]  # buang delimiter


def kirim(*, endpoint: str, p256dh: str, auth: str, isi: bytes | None,
          vapid_publik: str, vapid_privat: str, subjek: str = "mailto:puspa@contoh.id",
          http_client=None, ttl: int = 86400) -> dict:
    """Kirim satu push. `isi=None` = push kosong (bangunkan saja)."""
    jwt = _jwt_vapid(endpoint=endpoint, subjek=subjek, privat_b64=vapid_privat)
    headers = {"Authorization": f"vapid t={jwt}, k={vapid_publik}", "TTL": str(ttl)}
    if isi is None:
        badan, headers["Content-Length"] = b"", "0"
    else:
        badan, kunci_server, garam = enkripsi_payload(isi, p256dh_penerima_b64=p256dh, auth_b64=auth)
        headers["Content-Encoding"] = "aes128gcm"
        headers["Encryption"] = f"salt={garam}"
        headers["Crypto-Key"] = f"dh={kunci_server}"
        headers["Content-Length"] = str(len(badan))
    client = http_client or httpx.Client(timeout=30.0)
    resp = client.post(endpoint, content=badan, headers=headers)
    if resp.status_code not in (200, 201, 202):
        raise PushError(f"push gagal: {resp.status_code}")
    return {"status": resp.status_code, "endpoint": endpoint}


def simpan_langganan(user_id: str, *, endpoint: str, p256dh: str, auth: str,
                     dsn=None) -> str:
    """Simpan langganan push pengguna. Kembalikan id langganan."""
    import uuid

    import psycopg

    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
    langganan_id = f"push-{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO langganan_push (id, user_id, endpoint, p256dh, auth)"
                " VALUES (%s, %s, %s, %s, %s)",
                (langganan_id, user_id, endpoint, p256dh, auth),
            )
        conn.commit()
    return langganan_id


def daftar_langganan(user_id: str, dsn=None) -> list[dict]:
    """Semua langganan push satu pengguna."""
    import psycopg

    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, endpoint, p256dh, auth FROM langganan_push WHERE user_id = %s",
                        (user_id,))
            return [dict(r) for r in cur.fetchall()]
