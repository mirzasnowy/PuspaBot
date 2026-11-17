"""Token izin Puspa (definisi 2.6, CONTRACTS.md bagian "Token izin").

Token = ``base64url(payload_json_kanonis).base64url(signature)``,
signature Ed25519 atas bytes payload kanonis. Kunci dibuat saat
runtime/tes dan TIDAK PERNAH disimpan di file.

Payload (wajib): ``user_id, agen_id, sub_agen_id`` (null untuk token
agen), ``tools: [str], domains: [str], konektor: {nama: [scope]},
anggaran: {langkah_max, token_max, rupiah_max}, exp (unix),
iat (unix), parent`` (hash token induk atau null). Field tambahan
(mis. ``tugas_id``) diizinkan dan dipertahankan apa adanya.

Pencabutan bersifat in-memory (persistensi fase 2). ``token_id``
sebuah token adalah sha256 hex dari string tokennya; untuk payload
dict, sha256 dari JSON kanonis payload itu.
"""

import base64
import hashlib
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

__all__ = [
    "TokenError",
    "canonical_json",
    "decode_payload",
    "issue_child",
    "issue_token",
    "is_revoked",
    "is_subset",
    "revoke",
    "revoke_all_for_task",
    "token_id",
    "verify_token",
]


class TokenError(Exception):
    """Semua kegagalan token izin (signature salah, kedaluwarsa,
    field hilang, bukan subset, dicabut)."""


# Field wajib payload token.
REQUIRED_FIELDS = frozenset(
    {
        "user_id",
        "agent_id",
        "subagent_id",
        "tools",
        "domains",
        "connectors",
        "anggaran",
        "exp",
        "iat",
        "parent",
    }
)

# Field wajib di dalam anggaran.
ANGGARAN_FIELDS = frozenset({"step_max", "token_max", "rupiah_max"})

# Registri in-memory: token_id -> payload (untuk revoke_all_for_tugas).
_issued: dict[str, dict] = {}

# Set pencabutan in-memory: token_id yang dicabut.
_revoked: set[str] = set()


def canonical_json(obj) -> bytes:
    """JSON kanonis: kunci terurut, tanpa spasi, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def token_id(token) -> str:
    """Identitas token: sha256 hex dari string token, atau dari JSON
    kanonis kalau yang diberikan adalah payload dict."""
    if isinstance(token, dict):
        raw = canonical_json(token)
    elif isinstance(token, str):
        raw = token.encode("utf-8")
    else:
        raise TokenError("token_id butuh token string atau payload dict")
    return hashlib.sha256(raw).hexdigest()


def _normalize_id(token_or_id: str) -> str:
    """Terima token lengkap atau token_id; kembalikan token_id."""
    if isinstance(token_or_id, str) and "." in token_or_id:
        try:
            # Token lengkap: "payload.signature" (dua segmen b64url).
            parts = token_or_id.split(".")
            if len(parts) == 2:
                _b64url_decode(parts[0])
                _b64url_decode(parts[1])
                return token_id(token_or_id)
        except Exception:
            pass
    return token_or_id


def _validate_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise TokenError("payload token harus dict")
    missing = REQUIRED_FIELDS - set(payload)
    if missing:
        raise TokenError(f"payload token kehilangan field: {sorted(missing)}")
    if not isinstance(payload["tools"], list) or not all(
        isinstance(t, str) for t in payload["tools"]
    ):
        raise TokenError("field tools harus list[str]")
    if not isinstance(payload["domains"], list) or not all(
        isinstance(d, str) for d in payload["domains"]
    ):
        raise TokenError("field domains harus list[str]")
    if not isinstance(payload["connectors"], dict) or not all(
        isinstance(k, str) and isinstance(v, list) and all(isinstance(s, str) for s in v)
        for k, v in payload["connectors"].items()
    ):
        raise TokenError("field konektor harus {nama: [scope]}")
    anggaran = payload["anggaran"]
    if not isinstance(anggaran, dict):
        raise TokenError("field anggaran harus dict")
    missing_anggaran = ANGGARAN_FIELDS - set(anggaran)
    if missing_anggaran:
        raise TokenError(f"anggaran kehilangan field: {sorted(missing_anggaran)}")
    for key in ANGGARAN_FIELDS:
        if not isinstance(anggaran[key], (int, float)) or isinstance(anggaran[key], bool):
            raise TokenError(f"anggaran.{key} harus angka")
    for key in ("exp", "iat"):
        if not isinstance(payload[key], int) or isinstance(payload[key], bool):
            raise TokenError(f"field {key} harus unix timestamp (int)")
    if payload["subagent_id"] is not None and not isinstance(payload["subagent_id"], str):
        raise TokenError("field subagent_id harus str atau null")
    if payload["parent"] is not None and not isinstance(payload["parent"], str):
        raise TokenError("field parent harus str atau null")


def issue_token(payload: dict, signing_key: Ed25519PrivateKey) -> str:
    """Tandatangani payload dan kembalikan token string.

    Payload pemanggil tidak diubah; salinannya yang ditandatangani.
    """
    _validate_payload(payload)
    copy = json.loads(canonical_json(payload).decode("utf-8"))
    raw = canonical_json(copy)
    signature = signing_key.sign(raw)
    token = f"{_b64url_encode(raw)}.{_b64url_encode(signature)}"
    _issued[token_id(token)] = copy
    return token


def decode_payload(token: str) -> dict:
    """Ambil payload dari token TANPA memverifikasi signature.

    Hanya untuk keperluan internal (mis. membaca token_id sebelum
    verifikasi). Jangan dipakai untuk mengambil keputusan izin.
    """
    if not isinstance(token, str) or token.count(".") != 1:
        raise TokenError("format token salah: harus payload.signature")
    raw_b64, _sig_b64 = token.split(".")
    try:
        raw = _b64url_decode(raw_b64)
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise TokenError("payload token tidak bisa dibaca") from exc
    if not isinstance(payload, dict):
        raise TokenError("payload token harus objek JSON")
    return payload


def verify_token(token: str, public_key: Ed25519PublicKey) -> dict:
    """Verifikasi signature + field + kedaluwarsa + pencabutan.

    Kembalikan payload dict. Gagal apa pun -> raise TokenError.
    """
    if not isinstance(token, str) or token.count(".") != 1:
        raise TokenError("format token salah: harus payload.signature")
    raw_b64, sig_b64 = token.split(".")
    try:
        raw = _b64url_decode(raw_b64)
        signature = _b64url_decode(sig_b64)
    except Exception as exc:
        raise TokenError("token bukan base64url yang valid") from exc
    try:
        public_key.verify(signature, raw)
    except InvalidSignature as exc:
        raise TokenError("signature token tidak valid") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise TokenError("payload token bukan JSON yang valid") from exc
    _validate_payload(payload)
    if payload["exp"] <= int(time.time()):
        raise TokenError("token sudah kedaluwarsa")
    if is_revoked(token_id(token)):
        raise TokenError("token sudah dicabut")
    _issued[token_id(token)] = payload
    return payload


def is_subset(child: dict, parent: dict) -> bool:
    """Benar kalau izin child seluruhnya di dalam izin parent.

    tools ⊆, domains ⊆, tiap scope konektor ⊆, tiap anggaran ≤,
    exp ≤, user_id sama, agen_id sama; sub_agen_id hanya boleh
    terisi kalau parent adalah token agen (sub_agen_id null).
    """
    try:
        if child["user_id"] != parent["user_id"]:
            return False
        if child["agent_id"] != parent["agent_id"]:
            return False
        if set(child["tools"]) - set(parent["tools"]):
            return False
        if set(child["domains"]) - set(parent["domains"]):
            return False
        for name, scopes in child["connectors"].items():
            if set(scopes) - set(parent["connectors"].get(name, [])):
                return False
        for key in ANGGARAN_FIELDS:
            if child["anggaran"][key] > parent["anggaran"][key]:
                return False
        if child["exp"] > parent["exp"]:
            return False
        # Sub-agen tidak bisa membuat sub-agen (satu level saja, 2.2).
        if child.get("subagent_id") is not None and parent.get("subagent_id") is not None:
            return False
        return True
    except (KeyError, TypeError, AttributeError):
        return False


def issue_child(parent_token: str, requested_payload: dict, signing_key, public_key) -> str:
    """Terbitkan token anak dari token induk yang sudah terverifikasi.

    Gagal verifikasi induk atau bukan subset -> TokenError dan tidak
    ada token yang keluar. Field ``parent`` anak diisi hash token induk.
    """
    parent_payload = verify_token(parent_token, public_key)
    _validate_payload(requested_payload)
    if not is_subset(requested_payload, parent_payload):
        raise TokenError("payload anak bukan himpunan bagian dari token induk")
    child = json.loads(canonical_json(requested_payload).decode("utf-8"))
    child["parent"] = token_id(parent_token)
    return issue_token(child, signing_key)


def revoke(token_or_id) -> None:
    """Cabut satu token (terima token lengkap atau token_id)."""
    _revoked.add(_normalize_id(token_or_id))


def is_revoked(token_or_id) -> bool:
    """Benar kalau token (lengkap atau token_id) sudah dicabut."""
    return _normalize_id(token_or_id) in _revoked


def revoke_all_for_task(task_id: str) -> list[str]:
    """Cabut semua token terdaftar yang membawa ``tugas_id`` ini.

    Dipakai saat Hentikan (2.2/2.6): token kedaluwarsa saat tugas
    selesai atau dihentikan. Token yang tidak membawa tugas_id tidak
    tersentuh. Kembalikan daftar token_id yang dicabut.
    """
    revoked = []
    for tid, payload in list(_issued.items()):
        if payload.get("task_id") == task_id and tid not in _revoked:
            _revoked.add(tid)
            revoked.append(tid)
    return revoked
