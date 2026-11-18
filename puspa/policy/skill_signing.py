"""Skill bertanda tangan Puspa (definisi 2.6, CONTRACTS.md).

Skill = direktori berisi ``SKILL.md`` + ``manifest.json``::

    {name, version, tools[], domains[], konektor{}, author, signature}

``signature`` adalah Ed25519 atas JSON kanonis manifes TANPA field
``signature``. Skill tanpa tanda tangan atau dengan signature salah
ditolak. Tool di luar manifes ditolak saat dipakai (``check_tool``).

Kunci Puspa untuk skill bawaan dibuat saat runtime; kunci komunitas
terdaftar terpisah (fase 6).

Provenance (``puspa.policy.signing``): when a transparency log is
passed to ``verify_skill``, a skill is trusted only if it also carries
a valid DSSE envelope (``envelope.json`` in the skill directory) whose
payload binds the manifest bytes and the expected identity, and that
envelope is recorded in the log with a verifiable Merkle inclusion
proof. Without a log argument, ``verify_skill`` keeps its old meaning
(manifest signature only) so existing callers stay valid.
"""

import base64
import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature

from .signing import (
    SigningError,
    decode_envelope_payload,
    sign_skill as sign_skill_envelope,
)
from .signing import verify_skill_envelope
from .tokens import TokenError, _b64url_decode, _b64url_encode, canonical_json

__all__ = [
    "SkillRejected",
    "check_tool",
    "load_skill",
    "sign_skill",
    "sign_skill_provenance",
    "verify_skill",
]

#: DSSE envelope file next to manifest.json in a skill directory.
ENVELOPE_FILENAME = "envelope.json"


class SkillRejected(Exception):
    """Skill ditolak: tidak ada signature, signature salah, manifes
    rusak, atau tool di luar manifes."""


def _manifest_path(dirpath) -> Path:
    return Path(dirpath) / "manifest.json"


def _read_manifest(dirpath) -> dict:
    path = _manifest_path(dirpath)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except FileNotFoundError as exc:
        raise SkillRejected(f"manifest tidak ditemukan di {path}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise SkillRejected(f"manifest tidak bisa dibaca: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SkillRejected("manifest harus objek JSON")
    return manifest


def _unsigned_manifest(manifest: dict) -> dict:
    """Salinan manifes tanpa field signature (yang ditandatangani)."""
    copy = dict(manifest)
    copy.pop("signature", None)
    return copy


def _verify_manifest_dict(manifest: dict, public_key) -> bool:
    """Benar kalau ``manifest`` dict punya signature Ed25519 yang valid
    atas manifes tanpa field ``signature``. Tidak pernah raise."""
    signature_b64 = manifest.get("signature")
    if not isinstance(signature_b64, str) or not signature_b64:
        return False
    try:
        signature = _b64url_decode(signature_b64)
        public_key.verify(signature, canonical_json(_unsigned_manifest(manifest)))
    except (InvalidSignature, ValueError, TypeError, Exception):
        return False
    return True


def sign_skill(dirpath, signing_key) -> dict:
    """Tandatangani manifes skill di ``dirpath``.

    Menulis ``signature`` (base64url) ke manifest.json dan
    mengembalikan manifes yang sudah ditandatangani.
    """
    manifest = _read_manifest(dirpath)
    if "name" not in manifest or "tools" not in manifest:
        raise SkillRejected("manifest butuh field name dan tools")
    unsigned = _unsigned_manifest(manifest)
    signature = signing_key.sign(canonical_json(unsigned))
    manifest["signature"] = _b64url_encode(signature)
    path = _manifest_path(dirpath)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, sort_keys=True, indent=2, ensure_ascii=False)
        fh.write("\n")
    return manifest


def verify_skill(dirpath, public_key, transparency_log=None, expected_identity=None) -> bool:
    """Benar kalau manifes ada, punya signature, dan signature valid.

    Tidak pernah raise untuk skill yang rusak: kembalikan False.
    When ``transparency_log`` is given, the skill must ALSO carry a
    valid DSSE envelope (``envelope.json``) bound to these manifest
    bytes and to ``expected_identity``, recorded in the log with a
    verifiable inclusion proof; otherwise the skill is not trusted,
    even with a valid manifest signature. Without a log, behavior is
    exactly the old manifest-signature check.
    """
    try:
        manifest = _read_manifest(dirpath)
    except SkillRejected:
        return False
    if not _verify_manifest_dict(manifest, public_key):
        return False
    if transparency_log is None:
        return True
    return _verify_provenance(dirpath, manifest, transparency_log, expected_identity)


def _verify_provenance(dirpath, manifest, transparency_log, expected_identity) -> bool:
    """Provenance check: envelope valid + bound to this manifest +
    identity matches + recorded in the transparency log. Never raises."""
    if not isinstance(expected_identity, str) or not expected_identity:
        return False
    try:
        with open(Path(dirpath) / ENVELOPE_FILENAME, "r", encoding="utf-8") as fh:
            envelope = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    try:
        payload = decode_envelope_payload(envelope)
    except SigningError:
        return False
    skill_sha256 = hashlib.sha256(
        canonical_json(_unsigned_manifest(manifest))
    ).hexdigest()
    if payload.get("skill_sha256") != skill_sha256:
        return False
    ok, _reason = verify_skill_envelope(envelope, expected_identity, transparency_log)
    return ok


def sign_skill_provenance(dirpath, identity, transparency_log) -> dict:
    """Sign a skill directory's provenance and record it in the log.

    Builds a DSSE envelope over the canonical unsigned manifest bytes
    (the same bytes the manifest signature covers), writes it to
    ``envelope.json`` in the skill directory, and appends it to
    ``transparency_log``. Returns the envelope dict. This does not
    replace the manifest signature (``sign_skill``); call both, in
    either order, before the skill is trusted with a log. Raises
    SkillRejected if the manifest cannot be read and SigningError if
    the envelope cannot be built or recorded.
    """
    manifest = _read_manifest(dirpath)
    skill_bytes = canonical_json(_unsigned_manifest(manifest))
    envelope = sign_skill_envelope(skill_bytes, identity)
    path = Path(dirpath) / ENVELOPE_FILENAME
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(envelope, fh, sort_keys=True, indent=2, ensure_ascii=False)
        fh.write("\n")
    transparency_log.append(envelope)
    return envelope


def load_skill(dirpath, public_key) -> dict:
    """Muat skill yang sudah terverifikasi; raise SkillRejected kalau
    tidak ada signature, signature salah, atau manifes rusak.

    Juga menolak skill tanpa SKILL.md (skill = SKILL.md + manifest).
    """
    if not (Path(dirpath) / "SKILL.md").is_file():
        raise SkillRejected(f"SKILL.md tidak ditemukan di {dirpath}")
    manifest = _read_manifest(dirpath)
    if not manifest.get("signature"):
        raise SkillRejected("skill tanpa tanda tangan ditolak")
    if not verify_skill(dirpath, public_key):
        raise SkillRejected("signature skill tidak valid")
    return manifest


def check_tool(skill, tool: str, public_key=None) -> bool:
    """Benar kalau ``tool`` ada di manifes skill yang SUDAH TERVERIFIKASI
    tanda tangannya; kalau tidak, raise SkillRejected.

    ``skill`` boleh berupa manifes dict atau path direktori skill.
    ``public_key`` WAJIB (fail-closed): tanpa kunci, signature tidak bisa
    diverifikasi dan pemakaian ditolak — tidak ada jalur yang memakai
    manifes mentah tanpa verifikasi. Manifes dict tanpa signature,
    dengan signature salah, atau yang diubah setelah ditandatangani
    (mis. tool ditambahkan diam-diam) selalu ditolak, walau tool yang
    diminta ada di daftar tools-nya.

    Ini menutup celah lama: dulu ``check_tool`` hanya memeriksa
    keanggotaan manifes tanpa memverifikasi signature, sehingga kode
    yang memanggilnya langsung dengan manifes dict mentah dari disk
    bisa memakai skill unsigned. Sekarang verifikasi signature adalah
    bagian dari ``check_tool`` sendiri, bukan aturan pemanggil.
    """
    if public_key is None:
        raise SkillRejected(
            "check_tool butuh public_key untuk memverifikasi signature skill"
        )
    if isinstance(skill, (str, Path)):
        manifest = _read_manifest(skill)
    elif isinstance(skill, dict):
        manifest = skill
    else:
        raise SkillRejected("skill harus manifes dict atau path direktori")
    if not _verify_manifest_dict(manifest, public_key):
        raise SkillRejected(
            f"signature skill {manifest.get('name')!r} tidak valid atau tidak ada"
        )
    tools = manifest.get("tools", [])
    if tool not in tools:
        raise SkillRejected(f"tool {tool!r} di luar manifes skill {manifest.get('name')!r}")
    return True
