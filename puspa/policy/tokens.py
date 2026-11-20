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
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

__all__ = [
    "DEFAULT_TOKEN_TTL_SECONDS",
    "MAX_TOKEN_TTL_SECONDS",
    "TOOL_PERMISSION_LEVELS",
    "TOOL_PERMISSION_RANK",
    "TokenError",
    "canonical_json",
    "decode_payload",
    "is_revoked",
    "is_subset",
    "issue_child",
    "issue_short_lived_token",
    "issue_token",
    "jti_of",
    "revoke",
    "revoke_all_for_task",
    "token_id",
    "tool_permission",
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
        "budget",
        "exp",
        "iat",
        "parent",
    }
)

# Field wajib di dalam anggaran.
BUDGET_FIELDS = frozenset({"step_max", "token_max", "rupiah_max"})

# Per-tool permission model, adapted from the opencode / claude code
# pattern (see docs/brain-repo-list.md): every tool has one of three
# levels instead of a single per-turn allow. "allow" runs without
# asking, "ask" needs a user approval for that use, "deny" never runs.
# A token may carry an optional "tool_permissions" map
# {tool_name: level}; a tool missing from the map falls back to the
# plain "tools" list (listed -> "allow", unlisted -> "deny"), so
# tokens issued before this field existed keep their old meaning.
TOOL_PERMISSION_LEVELS = frozenset({"allow", "ask", "deny"})

# Ordering used by is_subset: a child may only narrow a permission,
# never widen it (deny < ask < allow).
TOOL_PERMISSION_RANK = {"deny": 0, "ask": 1, "allow": 2}

# Registri in-memory: token_id -> payload (untuk revoke_all_for_tugas).
_issued: dict[str, dict] = {}

# Set pencabutan in-memory: token_id yang dicabut.
_revoked: set[str] = set()


def canonical_json(obj) -> bytes:
    """JSON kanonis: kunci terurut, tanpa spasi, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


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


# E-11 (plan section 6.6): permission tokens are short-lived by
# design. A token lives minutes, not hours, so a stolen token is only
# briefly useful and a stop takes effect at the next expiry even if a
# revocation write is delayed. ``issue_short_lived_token`` is the
# issuance path for agent and subagent tokens; ``issue_token`` keeps
# accepting caller-supplied ``exp`` for pre-existing issuers and
# tests, but every token it signs still carries a ``jti``.
#: Default lifetime of a freshly issued agent token, in seconds.
DEFAULT_TOKEN_TTL_SECONDS = 300
#: Hard ceiling for a short-lived token's lifetime, in seconds.
MAX_TOKEN_TTL_SECONDS = 900


def jti_of(payload: dict) -> str | None:
    """The token id (``jti``) carried by a payload, or None.

    The ``jti`` is the stable identity of one token: revocation
    lists key on it, and the Gate reports it. Tokens issued before
    E-11 carry no ``jti`` and report None (they can still be
    revoked by token hash or by an agent-level revoke-all).
    """
    if not isinstance(payload, dict):
        return None
    jti = payload.get("jti")
    return jti if isinstance(jti, str) and jti else None


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
    budget = payload["budget"]
    if not isinstance(budget, dict):
        raise TokenError("field anggaran harus dict")
    missing_budget = BUDGET_FIELDS - set(budget)
    if missing_budget:
        raise TokenError(f"anggaran kehilangan field: {sorted(missing_budget)}")
    for key in BUDGET_FIELDS:
        if not isinstance(budget[key], (int, float)) or isinstance(budget[key], bool):
            raise TokenError(f"anggaran.{key} harus angka")
    for key in ("exp", "iat"):
        if not isinstance(payload[key], int) or isinstance(payload[key], bool):
            raise TokenError(f"field {key} harus unix timestamp (int)")
    if payload["subagent_id"] is not None and not isinstance(payload["subagent_id"], str):
        raise TokenError("field subagent_id harus str atau null")
    if payload["parent"] is not None and not isinstance(payload["parent"], str):
        raise TokenError("field parent harus str atau null")
    # E-11: a token may carry a jti (issue_token always stamps one).
    # When present it must be a non-empty string; absence is allowed
    # only for tokens issued before E-11 (legacy, still verifiable).
    if "jti" in payload and (not isinstance(payload["jti"], str) or not payload["jti"]):
        raise TokenError("field jti harus string tak kosong")
    permissions = payload.get("tool_permissions")
    if permissions is not None and (
        not isinstance(permissions, dict)
        or not all(
            isinstance(k, str) and isinstance(v, str) and v in TOOL_PERMISSION_LEVELS
            for k, v in permissions.items()
        )
    ):
        raise TokenError("field tool_permissions harus {nama_tool: 'allow'|'ask'|'deny'}")


def issue_token(payload: dict, signing_key: Ed25519PrivateKey) -> str:
    """Tandatangani payload dan kembalikan token string.

    Payload pemanggil tidak diubah; salinannya yang ditandatangani.

    E-11: when the payload carries no ``jti``, one is stamped
    (``uuid4`` hex) before signing, so every token issued through
    this function has a stable identity for revocation. A caller
    supplied ``jti`` is kept as-is.
    """
    _validate_payload(payload)
    copy = json.loads(canonical_json(payload).decode("utf-8"))
    if not copy.get("jti"):
        copy["jti"] = uuid.uuid4().hex
    raw = canonical_json(copy)
    signature = signing_key.sign(raw)
    token = f"{_b64url_encode(raw)}.{_b64url_encode(signature)}"
    _issued[token_id(token)] = copy
    return token


def issue_short_lived_token(
    payload: dict,
    signing_key: Ed25519PrivateKey,
    *,
    ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
) -> str:
    """Issue a token that lives minutes, not hours (E-11).

    Stamps a fresh ``jti``, sets ``iat`` to now and ``exp`` to
    ``iat + ttl_seconds`` on a copy of the payload (the caller's
    dict is untouched), then signs it through :func:`issue_token`.
    ``ttl_seconds`` must be between 1 and
    :data:`MAX_TOKEN_TTL_SECONDS` (15 minutes); anything longer is
    refused instead of silently issued. This is the issuance path
    for agent and subagent tokens.
    """
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
        raise TokenError("ttl_seconds must be an integer number of seconds")
    if ttl_seconds < 1 or ttl_seconds > MAX_TOKEN_TTL_SECONDS:
        raise TokenError(
            f"ttl_seconds must be between 1 and {MAX_TOKEN_TTL_SECONDS}, got {ttl_seconds}"
        )
    now = int(time.time())
    copy = json.loads(canonical_json(payload).decode("utf-8"))
    copy["jti"] = uuid.uuid4().hex
    copy["iat"] = now
    copy["exp"] = now + ttl_seconds
    return issue_token(copy, signing_key)


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


def verify_token(token: str, public_key: Ed25519PublicKey, *, revocations=None) -> dict:
    """Verifikasi signature + field + kedaluwarsa + pencabutan.

    Kembalikan payload dict. Gagal apa pun -> raise TokenError.

    ``revocations`` (E-11) is an optional durable revocation list
    (a :class:`puspa.policy.revocations.RevocationList` or any
    object with ``is_revoked_payload(payload) -> bool``). When
    given, a token listed by ``jti`` or covered by an agent-level
    revoke-all is refused here — so a refresh (``issue_child``,
    which re-verifies the parent) fails after a stop, in every
    process, not just the one that recorded the revocation.
    Without it, only the process-local in-memory set is checked
    (the pre-E-11 behaviour).
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
    if revocations is not None and revocations.is_revoked_payload(payload):
        raise TokenError("token sudah dicabut")
    _issued[token_id(token)] = payload
    return payload


def tool_permission(payload: dict, tool: str) -> str:
    """Per-tool permission level for ``tool`` under ``payload``.

    The explicit ``tool_permissions`` entry wins; otherwise a tool
    listed in ``tools`` is "allow" and any other tool is "deny".
    Unknown tools are always "deny" (fail-closed). Raises TokenError
    for a payload that is not a dict.
    """
    if not isinstance(payload, dict):
        raise TokenError("payload token harus dict")
    explicit = payload.get("tool_permissions") or {}
    level = explicit.get(tool)
    if level is not None:
        return level
    if tool in (payload.get("tools") or []):
        return "allow"
    return "deny"


def _effective_permissions(payload: dict) -> dict[str, str]:
    """Effective level per tool: explicit map plus the plain tools
    list (listed tools default to "allow")."""
    effective = {tool: "allow" for tool in (payload.get("tools") or [])}
    for tool, level in (payload.get("tool_permissions") or {}).items():
        effective[tool] = level
    return effective


def is_subset(child: dict, parent: dict) -> bool:
    """Benar kalau izin child seluruhnya di dalam izin parent.

    tools ⊆, domains ⊆, tiap scope konektor ⊆, tiap anggaran ≤,
    exp ≤, user_id sama, agen_id sama; sub_agen_id hanya boleh
    terisi kalau parent adalah token agen (sub_agen_id null).
    Per-tool: the child effective level for every tool must be no
    wider than the parent effective level (deny < ask < allow), so a
    child can never upgrade "ask" to "allow" or "deny" to "ask".
    """
    try:
        if child["user_id"] != parent["user_id"]:
            return False
        if child["agent_id"] != parent["agent_id"]:
            return False
        if set(child["tools"]) - set(parent["tools"]):
            return False
        child_levels = _effective_permissions(child)
        parent_levels = _effective_permissions(parent)
        for tool, level in child_levels.items():
            parent_level = parent_levels.get(tool, "deny")
            if TOOL_PERMISSION_RANK[level] > TOOL_PERMISSION_RANK[parent_level]:
                return False
        if set(child["domains"]) - set(parent["domains"]):
            return False
        for name, scopes in child["connectors"].items():
            if set(scopes) - set(parent["connectors"].get(name, [])):
                return False
        for key in BUDGET_FIELDS:
            if child["budget"][key] > parent["budget"][key]:
                return False
        if child["exp"] > parent["exp"]:
            return False
        # Sub-agen tidak bisa membuat sub-agen (satu level saja, 2.2).
        return not (child.get("subagent_id") is not None and parent.get("subagent_id") is not None)
    except (KeyError, TypeError, AttributeError):
        return False


def issue_child(
    parent_token: str, requested_payload: dict, signing_key, public_key, *, revocations=None
) -> str:
    """Terbitkan token anak dari token induk yang sudah terverifikasi.

    Gagal verifikasi induk atau bukan subset -> TokenError dan tidak
    ada token yang keluar. Field ``parent`` anak diisi hash token induk.

    ``revocations`` (E-11) is passed through to the parent
    verification: a child of a revoked parent is never issued, so
    stopping an agent also stops token refresh.
    """
    parent_payload = verify_token(parent_token, public_key, revocations=revocations)
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
