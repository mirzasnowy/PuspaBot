"""Signed skill revocation list, short-lived issuer keys, and signature
timestamps (plan 6.7, E-12).

Covers ``puspa.policy.revocation`` (list signing, verification,
fetch/refresh with a local cache) and the wiring into
``puspa.policy.skill_signing`` (``sign_skill`` stamps ``signed_at`` /
``key_expires_at`` / ``keyid``; ``verify_skill`` / ``load_skill`` /
``check_tool`` enforce expiry, keyid, and revocation) and into
``puspa.chassis.skill_loader`` (the loader refuses a revoked skill).

No test touches the network: fetches go through an injected fake
HTTP client, and every key is generated fresh inside the test.
"""

from __future__ import annotations

import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from puspa.chassis.skill_loader import PuspaSkillsLoader
from puspa.policy.revocation import (
    SkillRevocationError,
    SkillRevocationList,
    fetch_revocation_list,
    keyid_for_public_key,
    load_cached_revocation_list,
    verify_revocation_list,
)
from puspa.policy.skill_signing import (
    SkillRejected,
    check_tool,
    load_skill,
    sign_skill,
    verify_skill,
)
from puspa.policy.tokens import canonical_json

ISSUER = "puspa-issuer-test"


def _make_keypair():
    signing_key = Ed25519PrivateKey.generate()
    return signing_key, signing_key.public_key()


def _make_skill_dir(tmp_path, name="price", tools=("read_price",)):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Skill {name}.\n---\n\nSkill body {name}.\n",
        encoding="utf-8",
    )
    manifest = {
        "author": "puspa",
        "connectors": {},
        "domains": ["tokopedia.com"],
        "name": name,
        "tools": list(tools),
        "version": "0.1.0",
    }
    (skill_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return skill_dir


def _read_manifest(skill_dir):
    return json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))


def _skill_sha256(skill_dir):
    manifest = _read_manifest(skill_dir)
    unsigned = {k: v for k, v in manifest.items() if k != "signature"}
    return hashlib.sha256(canonical_json(unsigned)).hexdigest()


class _FakeResponse:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


def _fake_client(mapping):
    """A fake ``(url, timeout) -> response`` client from a URL map."""

    def fetch(url, timeout=30.0):
        status, body = mapping[url]
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        return _FakeResponse(status, body)

    return fetch


# --- List signing and verification ---------------------------------------


def test_list_sign_verify_round_trip():
    signing_key, public_key = _make_keypair()
    revoked = SkillRevocationList.sign(
        ISSUER, signing_key, revoked_keys=["dead-key"], revoked_skills=["abcd"]
    )
    assert revoked.issuer == ISSUER
    assert revoked.is_key_revoked("dead-key")
    assert revoked.is_skill_revoked("abcd")
    assert not revoked.is_key_revoked("live-key")
    ok, reason = verify_revocation_list(revoked.to_dict(), public_key)
    assert ok, reason


def test_list_tampered_signature_rejected():
    signing_key, public_key = _make_keypair()
    revoked = SkillRevocationList.sign(ISSUER, signing_key, revoked_keys=["dead-key"])
    doc = revoked.to_dict()
    doc["revoked_keys"] = ["another-key"]
    ok, _reason = verify_revocation_list(doc, public_key)
    assert not ok


def test_list_wrong_issuer_key_rejected():
    signing_key, _public_key = _make_keypair()
    _other_key, other_public = _make_keypair()
    revoked = SkillRevocationList.sign(ISSUER, signing_key)
    ok, _reason = verify_revocation_list(revoked.to_dict(), other_public)
    assert not ok


def test_list_expired_rejected():
    signing_key, public_key = _make_keypair()
    now = int(time.time())
    revoked = SkillRevocationList.sign(ISSUER, signing_key, ttl_seconds=60, now=now)
    assert verify_revocation_list(revoked.to_dict(), public_key, now=now + 30)[0]
    assert not verify_revocation_list(revoked.to_dict(), public_key, now=now + 3600)[0]
    with pytest.raises(SkillRevocationError):
        SkillRevocationList.from_dict(revoked.to_dict(), public_key, now=now + 3600)


def test_list_future_issued_at_rejected():
    signing_key, public_key = _make_keypair()
    now = int(time.time())
    revoked = SkillRevocationList.sign(ISSUER, signing_key, now=now + 3600)
    ok, _reason = verify_revocation_list(revoked.to_dict(), public_key, now=now)
    assert not ok


def test_list_wrong_version_rejected():
    signing_key, public_key = _make_keypair()
    revoked = SkillRevocationList.sign(ISSUER, signing_key)
    doc = revoked.to_dict()
    doc["version"] = 2
    ok, _reason = verify_revocation_list(doc, public_key)
    assert not ok


def test_list_from_dict_rejects_malformed():
    _signing_key, public_key = _make_keypair()
    with pytest.raises(SkillRevocationError):
        SkillRevocationList.from_dict({"version": 1}, public_key)


def test_keyid_is_stable_and_matches_public_key():
    _signing_key, public_key = _make_keypair()
    keyid = keyid_for_public_key(public_key)
    assert keyid == keyid_for_public_key(public_key)
    assert len(keyid) == 43  # base64url of 32 bytes, no padding


# --- Fetch / refresh ------------------------------------------------------


def test_fetch_unconfigured_without_url(monkeypatch):
    monkeypatch.delenv("PUSPA_REVOCATION_LIST_URL", raising=False)
    outcome = fetch_revocation_list(issuer_public_key=_make_keypair()[1])
    assert outcome["status"] == "unconfigured"
    assert outcome["revocation_list"] is None


def test_fetch_unavailable_without_issuer_key(tmp_path, monkeypatch):
    monkeypatch.delenv("PUSPA_REVOCATION_ISSUER_KEY", raising=False)
    outcome = fetch_revocation_list(
        "https://example.invalid/revocation-list.json",
        cache_path=tmp_path / "revocation-list.json",
        issuer_public_key=None,
    )
    assert outcome["status"] == "unavailable"
    assert outcome["revocation_list"] is None


def test_issuer_public_key_from_env_round_trip(monkeypatch):
    from puspa.policy.revocation import (
        REVOCATION_ISSUER_KEY_ENV_VAR,
        issuer_public_key_from_env,
    )
    from puspa.policy.tokens import _b64url_encode

    _signing_key, public_key = _make_keypair()
    monkeypatch.setenv(
        REVOCATION_ISSUER_KEY_ENV_VAR,
        _b64url_encode(public_key.public_bytes_raw()),
    )
    loaded = issuer_public_key_from_env()
    assert loaded.public_bytes_raw() == public_key.public_bytes_raw()
    monkeypatch.delenv(REVOCATION_ISSUER_KEY_ENV_VAR, raising=False)
    assert issuer_public_key_from_env() is None
    monkeypatch.setenv(REVOCATION_ISSUER_KEY_ENV_VAR, "not-a-key")
    with pytest.raises(SkillRevocationError):
        issuer_public_key_from_env()


def test_fetch_reads_url_and_key_from_env(tmp_path, monkeypatch):
    from puspa.policy.revocation import (
        REVOCATION_ISSUER_KEY_ENV_VAR,
        REVOCATION_LIST_URL_ENV_VAR,
    )
    from puspa.policy.tokens import _b64url_encode

    signing_key, public_key = _make_keypair()
    revoked = SkillRevocationList.sign(ISSUER, signing_key, revoked_keys=["dead-key"])
    monkeypatch.setenv(REVOCATION_LIST_URL_ENV_VAR, "https://example.invalid/list.json")
    monkeypatch.setenv(
        REVOCATION_ISSUER_KEY_ENV_VAR,
        _b64url_encode(public_key.public_bytes_raw()),
    )
    client = _fake_client({"https://example.invalid/list.json": (200, revoked.to_dict())})
    outcome = fetch_revocation_list(
        cache_path=tmp_path / "revocation-list.json", http_client=client
    )
    assert outcome["status"] == "refreshed"
    assert outcome["revocation_list"].is_key_revoked("dead-key")


def test_fetch_refreshes_and_caches(tmp_path):
    signing_key, public_key = _make_keypair()
    revoked = SkillRevocationList.sign(ISSUER, signing_key, revoked_keys=["dead-key"])
    client = _fake_client({"https://example.invalid/list.json": (200, revoked.to_dict())})
    cache = tmp_path / "revocation-list.json"
    outcome = fetch_revocation_list(
        "https://example.invalid/list.json",
        cache_path=cache,
        issuer_public_key=public_key,
        http_client=client,
    )
    assert outcome["status"] == "refreshed"
    assert outcome["revocation_list"] is not None
    assert outcome["revocation_list"].is_key_revoked("dead-key")
    assert cache.is_file()
    loaded, reason = load_cached_revocation_list(cache, public_key)
    assert loaded is not None, reason
    assert loaded.is_key_revoked("dead-key")


def test_fetch_fresh_cache_skips_network(tmp_path):
    signing_key, public_key = _make_keypair()
    revoked = SkillRevocationList.sign(ISSUER, signing_key, revoked_keys=["dead-key"])
    cache = tmp_path / "revocation-list.json"
    cache.write_text(json.dumps(revoked.to_dict()), encoding="utf-8")

    def _boom(url, timeout=30.0):
        raise AssertionError("network must not be touched while the cache is fresh")

    outcome = fetch_revocation_list(
        "https://example.invalid/list.json",
        cache_path=cache,
        issuer_public_key=public_key,
        http_client=_boom,
    )
    assert outcome["status"] == "fresh"
    assert outcome["revocation_list"].is_key_revoked("dead-key")


def test_fetch_unchanged_leaves_cache_alone(tmp_path):
    signing_key, public_key = _make_keypair()
    revoked = SkillRevocationList.sign(ISSUER, signing_key, revoked_keys=["dead-key"])
    cache = tmp_path / "revocation-list.json"
    cache.write_text(json.dumps(revoked.to_dict()), encoding="utf-8")
    old_mtime = cache.stat().st_mtime
    client = _fake_client({"https://example.invalid/list.json": (200, revoked.to_dict())})
    outcome = fetch_revocation_list(
        "https://example.invalid/list.json",
        cache_path=cache,
        max_age_seconds=0,
        issuer_public_key=public_key,
        http_client=client,
    )
    assert outcome["status"] == "unchanged"
    assert cache.stat().st_mtime == old_mtime


def test_fetch_failed_download_falls_back_to_cached_list(tmp_path):
    signing_key, public_key = _make_keypair()
    cached = SkillRevocationList.sign(ISSUER, signing_key, revoked_keys=["old-dead-key"])
    cache = tmp_path / "revocation-list.json"
    cache.write_text(json.dumps(cached.to_dict()), encoding="utf-8")
    client = _fake_client({"https://example.invalid/list.json": (500, "boom")})
    outcome = fetch_revocation_list(
        "https://example.invalid/list.json",
        cache_path=cache,
        max_age_seconds=0,
        issuer_public_key=public_key,
        http_client=client,
    )
    assert outcome["status"] == "failed"
    assert outcome["revocation_list"] is not None
    assert outcome["revocation_list"].is_key_revoked("old-dead-key")
    assert "falling back" in outcome["reason"]


def test_fetch_bad_signature_never_overwrites_cache(tmp_path):
    signing_key, public_key = _make_keypair()
    cached = SkillRevocationList.sign(ISSUER, signing_key, revoked_keys=["old-dead-key"])
    cache = tmp_path / "revocation-list.json"
    cache.write_text(json.dumps(cached.to_dict()), encoding="utf-8")
    bad = SkillRevocationList.sign(ISSUER, signing_key, revoked_keys=["new-dead-key"])
    bad_doc = bad.to_dict()
    bad_doc["revoked_keys"] = ["tampered-key"]
    client = _fake_client({"https://example.invalid/list.json": (200, bad_doc)})
    outcome = fetch_revocation_list(
        "https://example.invalid/list.json",
        cache_path=cache,
        max_age_seconds=0,
        issuer_public_key=public_key,
        http_client=client,
    )
    assert outcome["status"] == "failed"
    loaded, _reason = load_cached_revocation_list(cache, public_key)
    assert loaded.is_key_revoked("old-dead-key")
    assert not loaded.is_key_revoked("tampered-key")


def test_load_cached_missing_file_returns_none(tmp_path):
    loaded, reason = load_cached_revocation_list(tmp_path / "nope.json", _make_keypair()[1])
    assert loaded is None
    assert "no cached" in reason


# --- Wiring into skill signing -------------------------------------------


def test_sign_skill_stamps_timestamp_keyid_and_expiry(tmp_path):
    signing_key, public_key = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    now = int(time.time())
    manifest = sign_skill(skill_dir, signing_key, now=now)
    assert manifest["signed_at"] == now
    assert manifest["key_expires_at"] == now + 90 * 24 * 3600
    assert manifest["keyid"] == keyid_for_public_key(public_key)
    assert verify_skill(skill_dir, public_key, now=now)


def test_verify_skill_rejects_expired_issuer_key(tmp_path):
    signing_key, public_key = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    now = int(time.time())
    sign_skill(skill_dir, signing_key, key_ttl_seconds=60, now=now)
    assert verify_skill(skill_dir, public_key, now=now + 30)
    assert not verify_skill(skill_dir, public_key, now=now + 3600)


def test_verify_skill_rejects_future_signed_at(tmp_path):
    signing_key, public_key = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    now = int(time.time())
    sign_skill(skill_dir, signing_key, now=now + 3600)
    assert not verify_skill(skill_dir, public_key, now=now)


def test_verify_skill_rejects_keyid_mismatch(tmp_path):
    signing_key, public_key = _make_keypair()
    _other_key, other_public = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    manifest = sign_skill(skill_dir, signing_key)
    # Re-sign the manifest with the real key but a foreign keyid: the
    # signature itself stays valid, so only the keyid check can catch it.
    manifest["keyid"] = keyid_for_public_key(other_public)
    unsigned = {k: v for k, v in manifest.items() if k != "signature"}
    signature = signing_key.sign(canonical_json(unsigned))
    from puspa.policy.tokens import _b64url_encode

    manifest["signature"] = _b64url_encode(signature)
    (skill_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert not verify_skill(skill_dir, public_key)


def test_verify_skill_rejects_revoked_key(tmp_path):
    signing_key, public_key = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    sign_skill(skill_dir, signing_key)
    assert verify_skill(skill_dir, public_key)
    revoked = SkillRevocationList.sign(
        ISSUER, signing_key, revoked_keys=[keyid_for_public_key(public_key)]
    )
    assert not verify_skill(skill_dir, public_key, revocation_list=revoked)


def test_verify_skill_rejects_revoked_skill_not_key(tmp_path):
    signing_key, public_key = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    sign_skill(skill_dir, signing_key)
    digest = _skill_sha256(skill_dir)
    revoked = SkillRevocationList.sign(ISSUER, signing_key, revoked_skills=[digest])
    assert not verify_skill(skill_dir, public_key, revocation_list=revoked)
    other_dir = _make_skill_dir(tmp_path, name="other")
    sign_skill(other_dir, signing_key)
    assert verify_skill(other_dir, public_key, revocation_list=revoked)


def test_load_skill_rejects_revoked_skill(tmp_path):
    signing_key, public_key = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    sign_skill(skill_dir, signing_key)
    revoked = SkillRevocationList.sign(
        ISSUER, signing_key, revoked_keys=[keyid_for_public_key(public_key)]
    )
    with pytest.raises(SkillRejected):
        load_skill(skill_dir, public_key, revocation_list=revoked)


def test_check_tool_rejects_revoked_skill(tmp_path):
    signing_key, public_key = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    manifest = sign_skill(skill_dir, signing_key)
    assert check_tool(manifest, "read_price", public_key)
    revoked = SkillRevocationList.sign(
        ISSUER, signing_key, revoked_skills=[_skill_sha256(skill_dir)]
    )
    with pytest.raises(SkillRejected):
        check_tool(manifest, "read_price", public_key, revocation_list=revoked)


def test_check_tool_rejects_expired_key(tmp_path):
    signing_key, public_key = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    now = int(time.time())
    manifest = sign_skill(skill_dir, signing_key, key_ttl_seconds=60, now=now)
    assert check_tool(manifest, "read_price", public_key, now=now + 30)
    with pytest.raises(SkillRejected):
        check_tool(manifest, "read_price", public_key, now=now + 3600)


def test_loader_refuses_revoked_workspace_skill(tmp_path):
    signing_key, public_key = _make_keypair()
    workspace = tmp_path / "ws"
    skill_dir = workspace / "skills" / "price"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: price\ndescription: Skill price.\n---\n\nSkill body price.\n",
        encoding="utf-8",
    )
    manifest = {
        "author": "puspa",
        "connectors": {},
        "domains": ["tokopedia.com"],
        "name": "price",
        "tools": ["read_price"],
        "version": "0.1.0",
    }
    (skill_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sign_skill(skill_dir, signing_key)
    loader = PuspaSkillsLoader(workspace, public_key=public_key)
    assert loader.load_skill("price") is not None
    revoked = SkillRevocationList.sign(
        ISSUER, signing_key, revoked_keys=[keyid_for_public_key(public_key)]
    )
    revoked_loader = PuspaSkillsLoader(workspace, public_key=public_key, revocation_list=revoked)
    assert revoked_loader.load_skill("price") is None


def test_legacy_manifest_without_timestamps_still_verifies(tmp_path):
    signing_key, public_key = _make_keypair()
    skill_dir = _make_skill_dir(tmp_path)
    manifest = _read_manifest(skill_dir)
    unsigned = {k: v for k, v in manifest.items() if k != "signature"}
    signature = signing_key.sign(canonical_json(unsigned))
    from puspa.policy.tokens import _b64url_encode

    manifest["signature"] = _b64url_encode(signature)
    (skill_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert verify_skill(skill_dir, public_key)


# --- Scheduler wiring -----------------------------------------------------


def test_scheduler_registers_daily_revocation_refresh_trigger(monkeypatch):
    from puspa.scheduler import scheduler

    seen = {}

    def _fake_create_trigger(
        user_id, event_type, next_trigger_at, schedule="", workflow="", dsn=None
    ):
        seen.update(
            {
                "user_id": user_id,
                "event_type": event_type,
                "next_trigger_at": next_trigger_at,
                "schedule": schedule,
            }
        )
        return scheduler.Trigger(
            id="trigger-test",
            user_id=user_id,
            event_type=event_type,
            schedule=schedule,
            next_trigger_at=next_trigger_at,
        )

    monkeypatch.setattr(scheduler, "create_trigger", _fake_create_trigger)
    trigger = scheduler.schedule_revocation_list_refresh("user-1")
    assert trigger.event_type == "revocation_list_refresh"
    assert seen["event_type"] == "revocation_list_refresh"
    assert seen["schedule"] == "daily 03:00"
    assert trigger.event_type in scheduler.TRIGGER_TYPES


def test_scheduler_dispatches_revocation_refresh_job(monkeypatch):
    from puspa.policy import revocation
    from puspa.scheduler import scheduler

    class _FakeCursor:
        def __init__(self, row):
            self._row = row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return self._row

    class _FakeConn:
        def __init__(self, row):
            self._row = row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self, *args, **kwargs):
            return _FakeCursor(self._row)

    monkeypatch.setattr(
        scheduler, "_connect", lambda dsn=None: _FakeConn(("revocation_list_refresh", "user-1"))
    )

    def _fake_job(**kwargs):
        return {
            "status": "fresh",
            "path": "/cache/revocation-list.json",
            "reason": "fresh",
            "revocation_list": None,
        }

    monkeypatch.setattr(revocation, "run_revocation_refresh_job", _fake_job)
    result = scheduler.run_scheduled_job({"trigger_id": "trigger-test", "task_id": "task-test"})
    assert result["job"] == "revocation_list_refresh"
    assert result["result"]["status"] == "fresh"
