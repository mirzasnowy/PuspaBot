"""Tes token izin + skill bertanda tangan — definisi 2.6, CONTRACTS.md
bagian "Token izin" dan "Skill bertanda tangan".

Memetakan ke gerbang: token menyempit ke bawah (anak ⊆ induk),
signature Ed25519, pencabutan, dan skill tanpa tanda tangan ditolak.
"""

import copy
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from puspa.policy import (
    SkillRejected,
    TokenError,
    check_tool,
    issue_child,
    issue_token,
    is_revoked,
    is_subset,
    load_skill,
    revoke,
    revoke_all_for_task,
    sign_skill,
    token_id,
    verify_skill,
    verify_token,
)


def _payload(**overrides):
    now = int(time.time())
    payload = {
        "user_id": "user-1",
        "agent_id": "agen-puspa",
        "subagent_id": None,
        "tools": ["gmail.kirim", "read_price", "calendar.create_event"],
        "domains": ["tokopedia.com", "shopee.co.id"],
        "connectors": {"gmail": ["read", "send"], "calendar": ["read"]},
        "anggaran": {"step_max": 25, "token_max": 100000, "rupiah_max": 2000},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }
    payload.update(overrides)
    return payload


# --- Token: terbit + verifikasi -------------------------------------------

def test_issue_verify_roundtrip(ephemeral_keys):
    sk, pk = ephemeral_keys
    token = issue_token(_payload(), sk)
    assert token.count(".") == 1
    out = verify_token(token, pk)
    assert out["user_id"] == "user-1"
    assert out["agent_id"] == "agen-puspa"
    assert out["tools"] == ["gmail.kirim", "read_price", "calendar.create_event"]


def test_verify_wrong_key_raises(ephemeral_keys):
    sk, _pk = ephemeral_keys
    token = issue_token(_payload(), sk)
    other_pk = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(TokenError):
        verify_token(token, other_pk)


def test_verify_tampered_payload_raises(ephemeral_keys):
    sk, pk = ephemeral_keys
    token = issue_token(_payload(), sk)
    raw_b64, sig_b64 = token.split(".")
    import base64

    raw = json.loads(base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4)))
    raw["tools"] = ["delete_all"]
    raw_b64_new = base64.urlsafe_b64encode(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(TokenError):
        verify_token(f"{raw_b64_new}.{sig_b64}", pk)


def test_verify_expired_raises(ephemeral_keys):
    sk, pk = ephemeral_keys
    token = issue_token(_payload(exp=int(time.time()) - 10), sk)
    with pytest.raises(TokenError):
        verify_token(token, pk)


def test_verify_missing_field_raises(ephemeral_keys):
    sk, pk = ephemeral_keys
    payload = _payload()
    del payload["anggaran"]
    with pytest.raises(TokenError):
        issue_token(payload, sk)
    # Token yang payloadnya kehilangan field (dibuat manual) juga ditolak.
    payload2 = _payload()
    del payload2["tools"]
    raw = json.dumps(payload2, sort_keys=True, separators=(",", ":")).encode()
    import base64

    sig = sk.sign(raw)
    token = (
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    )
    with pytest.raises(TokenError):
        verify_token(token, pk)


def test_verify_malformed_token_raises(ephemeral_keys):
    _sk, pk = ephemeral_keys
    for buruk in ["", "abc", "a.b.c", "!!!.???"]:
        with pytest.raises(TokenError):
            verify_token(buruk, pk)


# --- Subset ---------------------------------------------------------------

def test_is_subset_ok():
    parent = _payload()
    child = _payload(
        subagent_id="sub-1",
        tools=["read_price"],
        domains=["tokopedia.com"],
        connectors={"gmail": ["read"]},
        anggaran={"step_max": 15, "token_max": 50000, "rupiah_max": 1000},
        exp=parent["exp"] - 60,
    )
    assert is_subset(child, parent) is True


def test_is_subset_rejects_bigger_tool():
    parent = _payload()
    child = _payload(tools=["read_price", "delete_all"])
    assert is_subset(child, parent) is False


def test_is_subset_rejects_bigger_anggaran():
    parent = _payload()
    child = _payload(anggaran={"step_max": 26, "token_max": 100000, "rupiah_max": 2000})
    assert is_subset(child, parent) is False


def test_is_subset_rejects_later_exp_and_other_user():
    parent = _payload()
    assert is_subset(_payload(exp=parent["exp"] + 60), parent) is False
    assert is_subset(_payload(user_id="user-2"), parent) is False
    assert is_subset(_payload(agent_id="agen-lain"), parent) is False


def test_is_subset_subagen_cannot_spawn_subagen():
    parent = _payload(subagent_id="sub-1")
    child = _payload(subagent_id="sub-2")
    assert is_subset(child, parent) is False


# --- Token anak -----------------------------------------------------------

def test_issue_child_ok(ephemeral_keys):
    sk, pk = ephemeral_keys
    parent_token = issue_token(_payload(), sk)
    child = issue_child(
        parent_token,
        _payload(subagent_id="sub-1", tools=["read_price"]),
        sk,
        pk,
    )
    out = verify_token(child, pk)
    assert out["subagent_id"] == "sub-1"
    assert out["parent"] == token_id(parent_token)


def test_issue_child_superset_raises(ephemeral_keys):
    sk, pk = ephemeral_keys
    parent_token = issue_token(_payload(), sk)
    with pytest.raises(TokenError):
        issue_child(
            parent_token,
            _payload(tools=["read_price", "tool_rahasia"]),
            sk,
            pk,
        )


def test_issue_child_bad_parent_raises(ephemeral_keys):
    sk, pk = ephemeral_keys
    with pytest.raises(TokenError):
        issue_child("bukan.token", _payload(), sk, pk)


# --- Pencabutan -----------------------------------------------------------

def test_revoke_and_is_revoked(ephemeral_keys):
    sk, pk = ephemeral_keys
    token = issue_token(_payload(), sk)
    assert is_revoked(token_id(token)) is False
    revoke(token_id(token))
    assert is_revoked(token_id(token)) is True
    with pytest.raises(TokenError):
        verify_token(token, pk)


def test_revoke_accepts_full_token(ephemeral_keys):
    sk, _pk = ephemeral_keys
    token = issue_token(_payload(), sk)
    revoke(token)
    assert is_revoked(token) is True


def test_revoke_all_for_task(ephemeral_keys):
    sk, pk = ephemeral_keys
    now = int(time.time())
    # iat dibedakan: payload identik menghasilkan token identik.
    t1 = issue_token(_payload(task_id="tugas-A", iat=now), sk)
    t2 = issue_token(_payload(task_id="tugas-A", iat=now + 1), sk)
    t3 = issue_token(_payload(task_id="tugas-B", iat=now + 2), sk)
    revoked = revoke_all_for_task("tugas-A")
    assert len(revoked) == 2
    with pytest.raises(TokenError):
        verify_token(t1, pk)
    with pytest.raises(TokenError):
        verify_token(t2, pk)
    assert verify_token(t3, pk)["task_id"] == "tugas-B"


# --- Skill bertanda tangan ------------------------------------------------

def test_skill_sign_verify_load(ephemeral_keys, tmp_skill_dir):
    sk, pk = ephemeral_keys
    assert verify_skill(tmp_skill_dir, pk) is False  # belum ditandatangani
    manifest = sign_skill(tmp_skill_dir, sk)
    assert manifest["signature"]
    assert verify_skill(tmp_skill_dir, pk) is True
    loaded = load_skill(tmp_skill_dir, pk)
    assert loaded["name"] == "example"
    assert loaded["tools"] == ["read_price", "search_listings"]


def test_skill_unsigned_rejected(ephemeral_keys, tmp_skill_dir):
    _sk, pk = ephemeral_keys
    with pytest.raises(SkillRejected):
        load_skill(tmp_skill_dir, pk)


def test_skill_tampered_manifest_rejected(ephemeral_keys, tmp_skill_dir):
    sk, pk = ephemeral_keys
    sign_skill(tmp_skill_dir, sk)
    manifest_path = tmp_skill_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tools"].append("delete_all")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert verify_skill(tmp_skill_dir, pk) is False
    with pytest.raises(SkillRejected):
        load_skill(tmp_skill_dir, pk)


def test_skill_wrong_key_rejected(ephemeral_keys, tmp_skill_dir):
    sk, _pk = ephemeral_keys
    sign_skill(tmp_skill_dir, sk)
    other_pk = Ed25519PrivateKey.generate().public_key()
    assert verify_skill(tmp_skill_dir, other_pk) is False
    with pytest.raises(SkillRejected):
        load_skill(tmp_skill_dir, other_pk)


def test_check_tool(ephemeral_keys, tmp_skill_dir):
    sk, pk = ephemeral_keys
    manifest = sign_skill(tmp_skill_dir, sk)
    assert check_tool(manifest, "read_price", pk) is True
    assert check_tool(load_skill(tmp_skill_dir, pk), "search_listings", pk) is True
    assert check_tool(tmp_skill_dir, "read_price", pk) is True
    with pytest.raises(SkillRejected):
        check_tool(manifest, "send_email", pk)
    with pytest.raises(SkillRejected):
        check_tool(manifest, "delete_all", pk)


def test_check_tool_without_public_key_rejected(ephemeral_keys, tmp_skill_dir):
    """Tanpa public_key, check_tool menolak walau manifes sudah ditandatangani
    (fail-closed; signature tidak boleh dilewati diam-diam)."""
    sk, _pk = ephemeral_keys
    manifest = sign_skill(tmp_skill_dir, sk)
    with pytest.raises(SkillRejected):
        check_tool(manifest, "read_price")
    with pytest.raises(SkillRejected):
        check_tool(manifest, "read_price", None)


def test_check_tool_manifest_unsigned_rejected(ephemeral_keys, tmp_skill_dir):
    """Manifes dict mentah tanpa signature ditolak walau tool yang diminta
    ada di daftar tools-nya (celah lama: keanggotaan saja tidak cukup)."""
    _sk, pk = ephemeral_keys
    manifest = json.loads((tmp_skill_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "signature" not in manifest
    assert "read_price" in manifest["tools"]
    with pytest.raises(SkillRejected):
        check_tool(manifest, "read_price", pk)


def test_check_tool_manifest_diubah_setelah_tanda_tangan_rejected(
    ephemeral_keys, tmp_skill_dir
):
    """Manifes yang ditandatangani lalu diubah diam-diam (tool berbahaya
    ditambahkan) ditolak — signature tidak lagi cocok dengan isinya."""
    import json as _json

    sk, pk = ephemeral_keys
    manifest = sign_skill(tmp_skill_dir, sk)
    manifest["tools"].append("send_uang")
    assert "send_uang" in manifest["tools"]
    with pytest.raises(SkillRejected):
        check_tool(manifest, "send_uang", pk)
    with pytest.raises(SkillRejected):
        check_tool(manifest, "read_price", pk)


def test_check_tool_key_wrong_rejected(ephemeral_keys, tmp_skill_dir):
    """Signature valid untuk kunci lain tidak diterima."""
    sk, _pk = ephemeral_keys
    sign_skill(tmp_skill_dir, sk)
    other_pk = Ed25519PrivateKey.generate().public_key()
    manifest = json.loads((tmp_skill_dir / "manifest.json").read_text(encoding="utf-8"))
    with pytest.raises(SkillRejected):
        check_tool(manifest, "read_price", other_pk)


def test_check_tool_path_unsigned_rejected(ephemeral_keys, tmp_skill_dir):
    """Path direktori skill unsigned ditolak (verifikasi dari disk)."""
    _sk, pk = ephemeral_keys
    with pytest.raises(SkillRejected):
        check_tool(tmp_skill_dir, "read_price", pk)
