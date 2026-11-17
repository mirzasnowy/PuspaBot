"""Ikat sesi login ke token izin agen untuk satu turn (definisi 2.13 + 2.6).

Alur turn setup Puspa (dipakai backend/PWA, fase 2; diuji di sini):

1. User login -> ``create_session`` (``puspa.accounts``) memberi token sesi.
2. Saat turn dimulai, ``bind_turn`` memvalidasi token sesi itu
   (``validate_session`` -> ``user_id``), lalu menerbitkan token izin
   agen Ed25519 (``issue_token``) untuk ``agen_id`` ini.
3. Pemanggil memverifikasi token string itu sekali
   (``payload_from_token``) dan menaruh payload dict-nya di
   ``RequestContext.attributes["puspa_token"]`` dan/atau ContextVar
   Puspa (``puspa.chassis.context.token_context``). Tool dan registry
   membaca payload dict itu — TIDAK PERNAH string mentah.

Token agen: ``sub_agen_id`` null (token agen, bukan sub-agen),
``tugas_id`` opsional di payload (dipakai ``revoke_all_for_tugas``
saat tugas selesai/dihentikan). Anggaran default: 25 langkah,
100.000 token, Rp0 (batas belanja default definisi 2.6 — pembayaran
apa pun tetap butuh persetujuan + limit yang dinaikkan user).
"""

from __future__ import annotations

import time

from puspa.accounts.sessions import validate_session
from puspa.policy.tokens import issue_token, verify_token

__all__ = ["bind_turn", "payload_from_token"]


def bind_turn(
    *,
    session_token: str,
    signing_key,
    public_key,
    agen_id: str,
    tools,
    domains=None,
    konektor=None,
    anggaran=None,
    exp_seconds: int = 3600,
    tugas_id: str | None = None,
    dsn=None,
) -> tuple[str, str, dict]:
    """Validasi sesi login lalu terbitkan token izin agen.

    Kembalikan ``(user_id, token_string, payload_dict)``. ``payload_dict``
    adalah payload yang sudah diverifikasi (hasil ``verify_token``) dan
    siap ditaruh di ``RequestContext.attributes`` / ContextVar.
    Raise ``SessionError`` kalau sesi tidak valid, ``TokenError`` kalau
    payload tidak bisa diterbitkan.
    """
    user_id = validate_session(session_token, dsn=dsn)
    now = int(time.time())
    payload = {
        "user_id": user_id,
        "agen_id": agen_id,
        "sub_agen_id": None,
        "tools": list(tools),
        "domains": list(domains or []),
        "konektor": dict(konektor or {}),
        "anggaran": dict(
            anggaran
            or {"langkah_max": 25, "token_max": 100_000, "rupiah_max": 0}
        ),
        "exp": now + exp_seconds,
        "iat": now,
        "parent": None,
    }
    if tugas_id is not None:
        payload["tugas_id"] = tugas_id
    token = issue_token(payload, signing_key)
    verified = verify_token(token, public_key)
    return user_id, token, verified


def payload_from_token(token: str, public_key) -> dict:
    """Verifikasi token string; kembalikan payload dict. Gagal -> TokenError."""
    return verify_token(token, public_key)
