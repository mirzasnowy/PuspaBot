"""Signed Puspa skills (definition 2.6, CONTRACTS.md).

A skill is a directory holding ``SKILL.md`` + ``manifest.json``::

    {name, version, tools[], domains[], konektor{}, author, signature}

``signature`` is an Ed25519 signature over the canonical JSON of the
manifest WITHOUT the ``signature`` field. A skill with no signature
or a bad signature is rejected. Tools outside the manifest are
rejected when used (``check_tool``).

Puspa's key for built-in skills is created at runtime; community
keys are registered separately (phase 6).

Provenance (``puspa.policy.signing``): when a transparency log is
passed to ``verify_skill``, a skill is trusted only if it also carries
a valid DSSE envelope (``envelope.json`` in the skill directory) whose
payload binds the manifest bytes and the expected identity, and that
envelope is recorded in the log with a verifiable Merkle inclusion
proof. Without a log argument, ``verify_skill`` keeps its old meaning
(manifest signature only) so existing callers stay valid.

Sigstore (``puspa.policy.sigstore_verify``): a community skill may
additionally carry a real Sigstore bundle, declared in the manifest::

    "sigstore": {
      "bundle": "bundle.sigstore",   // file in the skill dir (default)
      "artifact": "manifest.json",   // file the bundle attests (default)
      "identity": "author@example.com",
      "issuer": "https://github.com/login/oauth",
      "trust_root": "production"     // "production" | "staging" | path
    }

When the ``sigstore`` field is present, the bundle must verify (real
Sigstore verification: certificate chain, identity, issuer, artifact
digest, transparency-log inclusion proof) IN ADDITION to everything
else: the local manifest signature is still required first, and the
provenance envelope is still required when a log is passed. A bundle
is never a replacement for the local mechanism. When the field is
absent, behavior is exactly as before. A missing, malformed, or
failing bundle rejects the skill (fail closed); verification never
raises.

The bundle attests the raw bytes of the named artifact file as
stored on disk (by default ``manifest.json`` itself, which is the
natural ``sigstore sign manifest.json`` flow). The author signs the
final manifest, so the bundle binds the exact bytes Puspa reads,
including the ``sigstore`` declaration itself — and the local
signature covers that declaration too, so it cannot be added or
changed after signing without breaking the local check.
"""

import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature

from .signing import (
    SigningError,
    decode_envelope_payload,
    verify_skill_envelope,
)
from .signing import (
    sign_skill as sign_skill_envelope,
)
from .sigstore_verify import verify_sigstore_bundle
from .tokens import _b64url_decode, _b64url_encode, canonical_json

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

#: Default Sigstore bundle file in a skill directory, used when the
#: manifest's ``sigstore`` declaration does not name another file.
SIGSTORE_BUNDLE_FILENAME = "bundle.sigstore"


class SkillRejected(Exception):
    """Skill ditolak: tidak ada signature, signature salah, manifes
    rusak, atau tool di luar manifes."""


def _manifest_path(dirpath) -> Path:
    return Path(dirpath) / "manifest.json"


def _read_manifest(dirpath) -> dict:
    path = _manifest_path(dirpath)
    try:
        with open(path, encoding="utf-8") as fh:
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


def verify_skill(
    dirpath,
    public_key,
    transparency_log=None,
    expected_identity=None,
    *,
    sigstore_identity=None,
    sigstore_issuer=None,
    sigstore_trust_root=None,
) -> bool:
    """True when the manifest exists, is signed, and the signature is valid.

    Never raises for a damaged skill: returns False instead. When
    ``transparency_log`` is given, the skill must ALSO carry a valid
    DSSE envelope (``envelope.json``) bound to these manifest bytes
    and to ``expected_identity``, recorded in the log with a
    verifiable inclusion proof; otherwise the skill is not trusted,
    even with a valid manifest signature. Without a log, behavior is
    exactly the old manifest-signature check. When the manifest
    declares a ``sigstore`` bundle, that bundle must ALSO verify (see
    the module docstring); a failing bundle rejects the skill even
    when the local signature and the provenance envelope are fine.

    ``sigstore_identity``, ``sigstore_issuer``, and
    ``sigstore_trust_root`` override the matching values from the
    manifest's ``sigstore`` declaration when given; otherwise the
    manifest values are used.
    """
    try:
        manifest = _read_manifest(dirpath)
    except SkillRejected:
        return False
    if not _verify_manifest_dict(manifest, public_key):
        return False
    if transparency_log is not None and not _verify_provenance(
        dirpath, manifest, transparency_log, expected_identity
    ):
        return False
    return _verify_sigstore(
        dirpath, manifest, sigstore_identity, sigstore_issuer, sigstore_trust_root
    )


def _skill_file(dirpath, filename):
    """Path to ``filename`` inside the skill directory, or None when
    the name is not a plain file inside it (empty, absolute, or
    escaping via ``..``). Never raises.

    The bundle and artifact names come from the manifest, which the
    local signature covers, but a registered community key must still
    not be able to make verification read files outside the skill
    directory.
    """
    if not isinstance(filename, str) or not filename:
        return None
    base = Path(dirpath).resolve()
    path = (base / filename).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path


def _verify_sigstore(dirpath, manifest, identity, issuer, trust_root) -> bool:
    """Sigstore check: True when the manifest declares no bundle, or
    when the declared bundle verifies against its artifact with the
    expected identity and issuer. Never raises; anything missing,
    malformed, or failing rejects the skill (fail closed)."""
    declaration = manifest.get("sigstore")
    if declaration is None:
        return True
    if not isinstance(declaration, dict):
        return False
    bundle_path = _skill_file(dirpath, declaration.get("bundle", SIGSTORE_BUNDLE_FILENAME))
    artifact_path = _skill_file(dirpath, declaration.get("artifact", "manifest.json"))
    if bundle_path is None or artifact_path is None:
        return False
    if identity is None:
        identity = declaration.get("identity")
    if issuer is None:
        issuer = declaration.get("issuer")
    if trust_root is None:
        trust_root = declaration.get("trust_root", "production")
    if not isinstance(identity, str) or not identity:
        return False
    if not isinstance(issuer, str) or not issuer:
        return False
    if not isinstance(trust_root, str) or not trust_root:
        return False
    try:
        bundle_bytes = bundle_path.read_bytes()
        artifact_bytes = artifact_path.read_bytes()
    except OSError:
        return False
    try:
        ok, _reason = verify_sigstore_bundle(
            bundle_bytes, artifact_bytes, identity, issuer, trust_root=trust_root
        )
    except Exception:
        return False
    return ok


def _verify_provenance(dirpath, manifest, transparency_log, expected_identity) -> bool:
    """Provenance check: envelope valid + bound to this manifest +
    identity matches + recorded in the transparency log. Never raises."""
    if not isinstance(expected_identity, str) or not expected_identity:
        return False
    try:
        with open(Path(dirpath) / ENVELOPE_FILENAME, encoding="utf-8") as fh:
            envelope = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    try:
        payload = decode_envelope_payload(envelope)
    except SigningError:
        return False
    skill_sha256 = hashlib.sha256(canonical_json(_unsigned_manifest(manifest))).hexdigest()
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
        raise SkillRejected("check_tool butuh public_key untuk memverifikasi signature skill")
    if isinstance(skill, (str, Path)):
        manifest = _read_manifest(skill)
    elif isinstance(skill, dict):
        manifest = skill
    else:
        raise SkillRejected("skill harus manifes dict atau path direktori")
    if not _verify_manifest_dict(manifest, public_key):
        raise SkillRejected(f"signature skill {manifest.get('name')!r} tidak valid atau tidak ada")
    tools = manifest.get("tools", [])
    if tool not in tools:
        raise SkillRejected(f"tool {tool!r} di luar manifes skill {manifest.get('name')!r}")
    return True
