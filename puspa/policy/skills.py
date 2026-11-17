"""Skill bertanda tangan Puspa (definisi 2.6, CONTRACTS.md).

Skill = direktori berisi ``SKILL.md`` + ``manifest.json``::

    {name, version, tools[], domains[], konektor{}, author, signature}

``signature`` adalah Ed25519 atas JSON kanonis manifes TANPA field
``signature``. Skill tanpa tanda tangan atau dengan signature salah
ditolak. Tool di luar manifes ditolak saat dipakai (``check_tool``).

Kunci Puspa untuk skill bawaan dibuat saat runtime; kunci komunitas
terdaftar terpisah (fase 6).
"""

import base64
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature

from .tokens import TokenError, _b64url_decode, _b64url_encode, canonical_json

__all__ = ["SkillRejected", "check_tool", "load_skill", "sign_skill", "verify_skill"]


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
    salinan = dict(manifest)
    salinan.pop("signature", None)
    return salinan


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


def verify_skill(dirpath, public_key) -> bool:
    """Benar kalau manifes ada, punya signature, dan signature valid.

    Tidak pernah raise untuk skill yang rusak: kembalikan False.
    """
    try:
        manifest = _read_manifest(dirpath)
    except SkillRejected:
        return False
    return _verify_manifest_dict(manifest, public_key)


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
