"""Signed skill revocation list (plan 6.7).

The problem this closes: once a skill issuer key leaks, every
signature made with it stays valid forever, because nothing in the
old check can say "this key is dead" or "this skill is withdrawn".
The transparency log is single-writer and gives no signal either.
The smallest fix is a signed revocation list, fetched on a schedule
and checked every time a skill loads:

- The list is a JSON document ``{version, issuer, issued_at,
  expires_at, revoked_keys, revoked_skills, signature}`` signed
  with the issuer's Ed25519 key over the canonical JSON of the body
  (the same canonical form the manifest signature uses). A list
  with a bad signature, a wrong version, or an ``expires_at`` in
  the past is rejected, never trusted.
- ``revoked_keys`` holds keyids (base64url of the raw 32-byte
  Ed25519 public key, the same keyid convention as the DSSE
  envelopes in ``puspa.policy.signing``). A manifest signed by a
  revoked key is rejected even though its signature is
  cryptographically valid.
- ``revoked_skills`` holds sha256 hex digests of canonical unsigned
  manifests, so one bad skill version can be withdrawn without
  killing the issuer key that signed it.
- The list itself is short-lived (default TTL 7 days): an old list
  stops being trusted, so a stale cache cannot hide a fresh
  revocation forever. Fetching is periodic, never on the verify
  path: ``fetch_revocation_list`` downloads, verifies against the
  issuer public key, and installs atomically (a bad download never
  overwrites the good cache), mirroring
  ``puspa.policy.trust_root.refresh_trust_root``. Verification
  itself stays offline and takes an already-verified
  ``SkillRevocationList`` object, exactly like ``verify_skill`` takes
  an already-loaded transparency log.

Short-lived issuer keys and signature timestamps live in
``puspa.policy.skill_signing``: ``sign_skill`` stamps every
manifest with ``signed_at`` and ``key_expires_at`` (default 90
days) and records the signing ``keyid``; ``verify_skill`` rejects
a manifest whose key has expired or whose timestamp is in the
future. Revocation is the emergency brake for a key that leaks
before its expiry; expiry is the normal brake for a key that
simply gets old.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .tokens import _b64url_decode, _b64url_encode, canonical_json

__all__ = [
    "CLOCK_SKEW_TOLERANCE_SECONDS",
    "DEFAULT_LIST_TTL_SECONDS",
    "DEFAULT_REFRESH_INTERVAL_SECONDS",
    "REVOCATION_LIST_CACHE_ENV_VAR",
    "REVOCATION_LIST_URL_ENV_VAR",
    "SkillRevocationError",
    "SkillRevocationList",
    "default_cache_path",
    "fetch_revocation_list",
    "issuer_public_key_from_env",
    "keyid_for_public_key",
    "load_cached_revocation_list",
    "run_revocation_refresh_job",
    "verify_revocation_list",
]

#: Current revocation-list document version. A list with any other
#: version is rejected so the format can evolve without silently
#: misreading an old document.
LIST_VERSION = 1

#: How long a signed list stays trusted after ``issued_at``.
#: Past ``expires_at`` the list is rejected outright, so a stale
#: cache cannot hide a fresh revocation forever.
DEFAULT_LIST_TTL_SECONDS = 7 * 24 * 3600

#: How often the cached list is refreshed. A cache younger than
#: this is returned as-is with no network access.
DEFAULT_REFRESH_INTERVAL_SECONDS = 24 * 3600

#: Clock-skew allowance for ``issued_at`` / ``expires_at`` checks.
CLOCK_SKEW_TOLERANCE_SECONDS = 300

#: Env var with the URL the list is fetched from. Unset means no
#: remote list is configured and fetching reports ``unconfigured``.
REVOCATION_LIST_URL_ENV_VAR = "PUSPA_REVOCATION_LIST_URL"

#: Env var with the issuer public key that signs the list
#: (base64url of the raw 32-byte Ed25519 public key, the keyid
#: convention). Unset means a fetched list cannot be verified and
#: fetching reports ``unavailable``.
REVOCATION_ISSUER_KEY_ENV_VAR = "PUSPA_REVOCATION_ISSUER_KEY"

#: Env var overriding the cache file path.
REVOCATION_LIST_CACHE_ENV_VAR = "PUSPA_REVOCATION_LIST_CACHE"

#: Largest revocation list accepted. A real list is a few KiB; the
#: cap only stops abuse through huge downloads.
MAX_LIST_BYTES = 1024 * 1024


class SkillRevocationError(Exception):
    """A revocation list is malformed, unsigned, expired, or unusable."""


def keyid_for_public_key(public_key) -> str:
    """Keyid for an Ed25519 public key: base64url of its raw bytes.

    The same keyid convention as the DSSE envelopes in
    ``puspa.policy.signing`` (``keyid`` there is the base64url
    public key). A revoked key is named by this string.
    """
    return _b64url_encode(public_key.public_bytes_raw())


def _now_unix(now: float | None = None) -> int:
    return int(now if now is not None else time.time())


def _unsigned_body(data: dict) -> dict:
    """Copy of a list document without its ``signature`` field."""
    copy = dict(data)
    copy.pop("signature", None)
    return copy


def verify_revocation_list(
    data, issuer_public_key, *, now: float | None = None
) -> tuple[bool, str]:
    """Check a revocation-list document. Returns ``(ok, reason)``.

    Checks, in order: the document is a JSON object with version 1,
    a non-empty issuer string, integer ``issued_at`` /
    ``expires_at`` with ``issued_at`` not in the future (beyond
    clock skew) and ``expires_at`` still in the future, a
    ``signature`` that verifies over the canonical JSON of the body
    under ``issuer_public_key``, and ``revoked_keys`` /
    ``revoked_skills`` as lists of strings. Never raises for a bad
    document: it returns ``(False, reason)`` instead.
    """
    now = _now_unix(now)
    if not isinstance(data, dict):
        return False, "revocation list must be a JSON object"
    if data.get("version") != LIST_VERSION:
        return False, f"unsupported revocation list version: {data.get('version')!r}"
    if not isinstance(data.get("issuer"), str) or not data["issuer"]:
        return False, "revocation list issuer missing"
    issued_at = data.get("issued_at")
    expires_at = data.get("expires_at")
    if not isinstance(issued_at, int) or isinstance(issued_at, bool):
        return False, "revocation list issued_at must be an integer"
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        return False, "revocation list expires_at must be an integer"
    if issued_at > now + CLOCK_SKEW_TOLERANCE_SECONDS:
        return False, "revocation list issued_at is in the future"
    if expires_at <= now - CLOCK_SKEW_TOLERANCE_SECONDS:
        return False, "revocation list is expired"
    if expires_at <= issued_at:
        return False, "revocation list expires_at is not after issued_at"
    for field in ("revoked_keys", "revoked_skills"):
        values = data.get(field)
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            return False, f"revocation list {field} must be a list of strings"
    signature_b64 = data.get("signature")
    if not isinstance(signature_b64, str) or not signature_b64:
        return False, "revocation list has no signature"
    try:
        signature = _b64url_decode(signature_b64)
        issuer_public_key.verify(signature, canonical_json(_unsigned_body(data)))
    except (InvalidSignature, ValueError, TypeError):
        return False, "revocation list signature invalid"
    return True, "ok"


class SkillRevocationList:
    """A verified revocation list: the emergency brake for skills.

    Built only through :meth:`sign` (issuer side) or
    :meth:`from_dict` (verifier side, which checks the signature,
    the version, and the expiry before trusting anything). The
    object is immutable after construction; a new revocation means
    a newly signed list, never an edit in place.
    """

    def __init__(self, body: dict):
        self._body = dict(body)
        self.issuer: str = body["issuer"]
        self.issued_at: int = body["issued_at"]
        self.expires_at: int = body["expires_at"]
        self._revoked_keys = frozenset(body.get("revoked_keys", []))
        self._revoked_skills = frozenset(body.get("revoked_skills", []))

    @classmethod
    def sign(
        cls,
        issuer: str,
        signing_key,
        *,
        revoked_keys=(),
        revoked_skills=(),
        ttl_seconds: int = DEFAULT_LIST_TTL_SECONDS,
        now: float | None = None,
    ) -> SkillRevocationList:
        """Build and sign a new list with the issuer key.

        ``revoked_keys`` are keyids (see :func:`keyid_for_public_key`);
        ``revoked_skills`` are sha256 hex digests of canonical
        unsigned manifests. Raises ``SkillRevocationError`` for a bad
        issuer string or bad entry types.
        """
        if not isinstance(issuer, str) or not issuer:
            raise SkillRevocationError("revocation list needs a non-empty issuer string")
        revoked_keys = list(revoked_keys)
        revoked_skills = list(revoked_skills)
        if not all(isinstance(v, str) for v in revoked_keys):
            raise SkillRevocationError("revoked_keys must be strings")
        if not all(isinstance(v, str) for v in revoked_skills):
            raise SkillRevocationError("revoked_skills must be strings")
        issued = _now_unix(now)
        body = {
            "expires_at": issued + ttl_seconds,
            "issued_at": issued,
            "issuer": issuer,
            "revoked_keys": sorted(set(revoked_keys)),
            "revoked_skills": sorted(set(revoked_skills)),
            "version": LIST_VERSION,
        }
        signature = signing_key.sign(canonical_json(body))
        body["signature"] = _b64url_encode(signature)
        return cls(body)

    @classmethod
    def from_dict(cls, data, issuer_public_key, *, now: float | None = None) -> SkillRevocationList:
        """Verify a raw list document and return a SkillRevocationList.

        Raises ``SkillRevocationError`` when the document is malformed,
        unsigned, expired, or signed by the wrong key. Callers that
        must not raise use :func:`verify_revocation_list` first.
        """
        ok, reason = verify_revocation_list(data, issuer_public_key, now=now)
        if not ok:
            raise SkillRevocationError(f"revocation list rejected: {reason}")
        return cls(data)

    def to_dict(self) -> dict:
        """The signed document, as a plain dict (safe to store)."""
        return dict(self._body)

    def is_key_revoked(self, keyid: str) -> bool:
        """True when ``keyid`` is on this list."""
        return keyid in self._revoked_keys

    def is_skill_revoked(self, skill_sha256: str) -> bool:
        """True when a skill manifest digest is on this list."""
        return skill_sha256 in self._revoked_skills

    def expired(self, *, now: float | None = None) -> bool:
        """True when the list is past ``expires_at`` (clock-skew aware)."""
        return self.expires_at <= _now_unix(now) - CLOCK_SKEW_TOLERANCE_SECONDS

    def __len__(self) -> int:
        return len(self._revoked_keys) + len(self._revoked_skills)


def issuer_public_key_from_env():
    """Issuer public key from ``PUSPA_REVOCATION_ISSUER_KEY``.

    Returns the Ed25519 public key, or None when the variable is
    unset. Raises ``SkillRevocationError`` when it is set but not a
    base64url 32-byte key. This is the scheduled job's key source:
    the operator puts the issuer's public key in the environment
    once, and the daily refresh verifies every download against it.
    """
    raw = os.environ.get(REVOCATION_ISSUER_KEY_ENV_VAR)
    if not raw:
        return None
    try:
        key_bytes = _b64url_decode(raw.strip())
        return Ed25519PublicKey.from_public_bytes(key_bytes)
    except (ValueError, TypeError, Exception) as exc:
        raise SkillRevocationError(
            f"{REVOCATION_ISSUER_KEY_ENV_VAR} is not a base64url 32-byte Ed25519 public key"
        ) from exc


def default_cache_path() -> Path:
    """Default cache file for the fetched revocation list.

    ``PUSPA_REVOCATION_LIST_CACHE`` wins when set; otherwise
    ``~/.local/share/puspa/revocation/revocation-list.json``.
    """
    env = os.environ.get(REVOCATION_LIST_CACHE_ENV_VAR)
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "puspa" / "revocation" / "revocation-list.json"


def _resolve_cache_path(cache_path=None) -> Path:
    if cache_path is None:
        return default_cache_path()
    return Path(cache_path)


def _emit(sink, event_type, content) -> None:
    """Record an event through ``sink``; a broken sink never fails a refresh."""
    if sink is None:
        return
    with contextlib.suppress(Exception):
        sink(event_type, content)


def _cache_age_seconds(cache_path: Path, *, now: float | None = None) -> float | None:
    """Age of the cached list in seconds, or None when there is no cache file.

    ``now`` is a float Unix time (sub-second precision matters here:
    an int-truncated clock can make a just-written cache look
    younger than zero seconds old, which would defeat a
    ``max_age_seconds=0`` "always refresh" call).
    """
    try:
        mtime = cache_path.stat().st_mtime
    except OSError:
        return None
    return (now if now is not None else time.time()) - mtime


def load_cached_revocation_list(
    cache_path=None, issuer_public_key=None, *, now: float | None = None
) -> tuple[SkillRevocationList | None, str]:
    """Load and verify the cached list. Returns ``(list_or_None, reason)``.

    Never raises for a missing or damaged cache: it returns
    ``(None, reason)`` instead. Without ``issuer_public_key`` the
    cached signature cannot be checked, so nothing is trusted and
    ``(None, ...)`` is returned.
    """
    path = _resolve_cache_path(cache_path)
    if issuer_public_key is None:
        return None, "no issuer public key: cached revocation list cannot be verified"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"no cached revocation list at {path}: {exc}"
    if len(raw) > MAX_LIST_BYTES:
        return None, "cached revocation list exceeds the size cap"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"cached revocation list is not JSON: {exc}"
    try:
        return SkillRevocationList.from_dict(data, issuer_public_key, now=now), "ok"
    except SkillRevocationError as exc:
        return None, str(exc)


def _install_cached_list(data: bytes, path: Path) -> None:
    """Atomically install ``data`` as the cached list at ``path``.

    Writes to a temp file in the same directory and renames over
    the old cache, so a crash mid-write never leaves a half-written
    list in place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".revocation-list-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _default_http_get(url: str, *, timeout: float = 30.0) -> httpx.Response:
    """One GET with a timeout. Split out so tests can inject a fake client."""
    with httpx.Client(timeout=timeout) as client:
        return client.get(url)


def fetch_revocation_list(
    url: str | None = None,
    *,
    cache_path=None,
    max_age_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
    issuer_public_key=None,
    http_client=None,
    sink=None,
    now: float | None = None,
) -> dict:
    """Fetch the signed revocation list, verifying before installing.

    Returns an outcome dict ``{"status": ..., "path": str | None,
    "reason": str, "revocation_list": SkillRevocationList | None}`` and
    never raises for a failed fetch. Statuses:

    - ``unconfigured``: no URL given and
      ``PUSPA_REVOCATION_LIST_URL`` is unset — nothing to fetch.
    - ``unavailable``: no ``issuer_public_key`` given and
      ``PUSPA_REVOCATION_ISSUER_KEY`` unset — a downloaded list
      could not be verified, so no network is attempted.
    - ``fresh``: the cache is younger than ``max_age_seconds`` and
      verifies — no network.
    - ``refreshed``: a new list was downloaded, verified, and
      installed (a ``revocation_list_refreshed`` event is recorded).
    - ``unchanged``: the downloaded bytes are identical to the
      cached ones — the cache file is left untouched.
    - ``failed``: download, verification, or install failed; the
      old cache is untouched, and when it still verifies it is
      attached as ``revocation_list`` (stale fallback, recorded
      through ``sink``) instead of leaving the caller with nothing.

    ``http_client`` is an injectable ``(url, timeout) -> response``
    callable (tests pass a fake; the default does one
    ``httpx.Client`` GET). ``now`` is an injectable Unix clock for
    tests.
    """
    path = _resolve_cache_path(cache_path)
    now_unix = _now_unix(now)
    now_float = now if now is not None else time.time()
    if url is None:
        url = os.environ.get(REVOCATION_LIST_URL_ENV_VAR) or None
    if url is None:
        return {
            "status": "unconfigured",
            "path": None,
            "reason": "no revocation list URL configured (PUSPA_REVOCATION_LIST_URL unset)",
            "revocation_list": None,
        }
    if issuer_public_key is None:
        try:
            issuer_public_key = issuer_public_key_from_env()
        except SkillRevocationError as exc:
            return {
                "status": "unavailable",
                "path": str(path) if path.exists() else None,
                "reason": str(exc),
                "revocation_list": None,
            }
    if issuer_public_key is None:
        return {
            "status": "unavailable",
            "path": str(path) if path.exists() else None,
            "reason": "no issuer public key: a fetched revocation list could not be verified",
            "revocation_list": None,
        }
    age = _cache_age_seconds(path, now=now_float)
    if age is not None and age < max_age_seconds:
        cached, reason = load_cached_revocation_list(path, issuer_public_key, now=now_unix)
        if cached is not None:
            return {
                "status": "fresh",
                "path": str(path),
                "reason": f"cached revocation list is {age:.0f}s old (refresh after {max_age_seconds:.0f}s)",
                "revocation_list": cached,
            }
        # A young but unverifiable cache is treated as no cache: fall
        # through and try to fetch a good one.
        _emit(sink, "revocation_list_cache_invalid", {"path": str(path), "reason": reason})
    fetcher = http_client or _default_http_get
    try:
        response = fetcher(url, timeout=30.0)
        if response.status_code != 200:
            raise SkillRevocationError(
                f"revocation list fetch returned HTTP {response.status_code}"
            )
        data = response.content
    except SkillRevocationError as exc:
        return _fetch_failed(path, issuer_public_key, sink, now_unix, str(exc))
    except Exception as exc:
        return _fetch_failed(
            path, issuer_public_key, sink, now_unix, f"revocation list fetch failed: {exc}"
        )
    if len(data) > MAX_LIST_BYTES:
        return _fetch_failed(
            path,
            issuer_public_key,
            sink,
            now_unix,
            "downloaded revocation list exceeds the size cap",
        )
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _fetch_failed(
            path,
            issuer_public_key,
            sink,
            now_unix,
            f"downloaded revocation list is not JSON: {exc}",
        )
    try:
        fetched = SkillRevocationList.from_dict(parsed, issuer_public_key, now=now_unix)
    except SkillRevocationError as exc:
        return _fetch_failed(
            path, issuer_public_key, sink, now_unix, f"downloaded revocation list rejected: {exc}"
        )
    try:
        if path.is_file() and path.read_bytes() == data:
            return {
                "status": "unchanged",
                "path": str(path),
                "reason": "cached revocation list is already current",
                "revocation_list": fetched,
            }
        _install_cached_list(data, path)
    except Exception as exc:
        return _fetch_failed(
            path,
            issuer_public_key,
            sink,
            now_unix,
            f"could not install refreshed revocation list: {exc}",
        )
    _emit(
        sink,
        "revocation_list_refreshed",
        {"path": str(path), "issuer": fetched.issuer, "entries": len(fetched)},
    )
    return {
        "status": "refreshed",
        "path": str(path),
        "reason": "revocation list refreshed from the configured URL",
        "revocation_list": fetched,
    }


def _fetch_failed(path, issuer_public_key, sink, now_unix, reason) -> dict:
    """Shared failure outcome: record it, fall back to a cached list when it still verifies."""
    _emit(sink, "revocation_list_refresh_failed", {"path": str(path), "reason": reason})
    cached, _cache_reason = load_cached_revocation_list(path, issuer_public_key, now=now_unix)
    outcome: dict = {
        "status": "failed",
        "path": str(path) if path.exists() else None,
        "reason": reason,
        "revocation_list": cached,
    }
    if cached is not None:
        outcome["reason"] = reason + " (falling back to the cached list)"
        _emit(
            sink,
            "revocation_list_stale_fallback",
            {"path": str(path), "issuer": cached.issuer, "entries": len(cached)},
        )
    return outcome


def run_revocation_refresh_job(
    *,
    url: str | None = None,
    cache_path=None,
    max_age_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
    issuer_public_key=None,
    http_client=None,
    sink=None,
    now: float | None = None,
) -> dict:
    """One run of the scheduled revocation-list refresh job.

    Thin wrapper over :func:`fetch_revocation_list` for a scheduled
    trigger (daily): the outcome dict is returned unchanged and
    never raises. Wire it to a scheduler trigger the same way
    ``run_trust_root_refresh_job`` is wired to
    ``trust_root_refresh``; this module never runs on the verify
    path.
    """
    return fetch_revocation_list(
        url,
        cache_path=cache_path,
        max_age_seconds=max_age_seconds,
        issuer_public_key=issuer_public_key,
        http_client=http_client,
        sink=sink,
        now=now,
    )
