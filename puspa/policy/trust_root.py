"""Scheduled Sigstore trust-root refresh (TUF), outside the verify path.

The verifier in ``puspa.policy.sigstore_verify`` is deliberately
offline: it uses the trust root shipped inside the installed
``sigstore`` package (or a ``trusted_root.json`` file path) and never
touches the network. That shipped root is pinned to the installed
``sigstore`` version, so it goes stale when Fulcio or Rekor keys
rotate. This module is the scheduled refresh path that keeps a
fresher copy on disk without ever putting network into verification.

How it works:

- The refresh uses the ``sigstore`` package's own TUF client,
  ``sigstore._internal.tuf.TrustUpdater`` (sigstore 4.5.0, Apache-2.0;
  private API, read from the installed package 18 Nov 2026 — there is
  no public TUF refresh API in sigstore-python). ``TrustUpdater``
  refreshes the signed TUF metadata from the Sigstore TUF repository
  and downloads ``trusted_root.json`` as a TUF target, so the bytes
  are verified against the signed metadata before we ever see them.
  A downloaded root is still validated here before it is installed:
  it must parse as a ``TrustedRoot`` (``TrustedRoot.from_file``
  rejects bad JSON, a wrong media type, and missing fields) and fit
  under the size cap. Only then is it installed atomically
  (write to a temp file in the cache directory, then ``os.replace``).
  A bad download never overwrites the good cache: any failure leaves
  the previous cached root untouched, and the failure is reported
  (and recorded through ``sink``) instead of silently kept.
- The cached root lives at ``~/.local/share/puspa/trust_root/
  trusted_root.json`` by default (``PUSPA_TRUST_ROOT_CACHE`` env var
  or an explicit ``cache_path`` overrides it). It is a plain file,
  so the verifier can be pointed at it: pass the path returned by
  ``resolve_trust_root_for_verify`` as ``trust_root=`` to
  ``verify_sigstore_bundle``.
- Staleness is recorded, never silent. ``check_staleness`` reports
  (and emits a ``trust_root_stale`` event through ``sink``) when the
  cached root is older than ``max_age_days`` — or missing entirely.
  A stale cached root is still the freshest TUF-verified copy we
  have, so ``resolve_trust_root_for_verify`` keeps returning it, but
  only after recording that it is stale; callers that would rather
  fall back to the bundled root can check the returned status dict.
- The refresh is a scheduled job, not a startup hook and not part of
  verification. A daily trigger (``trust_root_refresh`` in
  ``puspa.scheduler.scheduler``) runs ``run_trust_root_refresh_job``:
  the trigger gives idempotent runs, missed runs are reported by the
  scheduler, and a refresh failure is retried the next day instead
  of delaying boot or breaking verification. When the cached root is
  still fresh (younger than ``max_age_days``), the job does no
  network at all and reports ``fresh``.

No function in this module is called from the verify path, and the
verify path never imports this module: verification stays offline.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "DEFAULT_MAX_AGE_DAYS",
    "DEFAULT_STALE_AFTER_DAYS",
    "DEFAULT_TUF_URL",
    "MAX_TRUSTED_ROOT_BYTES",
    "STAGING_TUF_URL",
    "TrustRootError",
    "cached_root_age_days",
    "check_staleness",
    "default_cache_path",
    "refresh_trust_root",
    "resolve_trust_root_for_verify",
    "run_trust_root_refresh_job",
]

#: Production Sigstore TUF repository (same URL sigstore-python uses).
DEFAULT_TUF_URL = "https://tuf-repo-cdn.sigstore.dev"

#: Staging Sigstore TUF repository (tests only; never the default).
STAGING_TUF_URL = "https://tuf-repo-cdn.sigstage.dev"

#: Refresh when the cached root is older than this (days). The daily
#: scheduled job checks this first and skips the network while fresh.
DEFAULT_MAX_AGE_DAYS = 7

#: Record staleness when the cached root is older than this (days).
#: A root between the refresh age and this age is simply due for its
#: next scheduled refresh; past this age it is stale and must be
#: recorded as such.
DEFAULT_STALE_AFTER_DAYS = 30

#: Largest trusted_root.json accepted. The real root is ~7 KiB; the
#: cap only stops abuse through huge downloads.
MAX_TRUSTED_ROOT_BYTES = 1024 * 1024

#: Cache file name inside the cache directory.
CACHE_FILENAME = "trusted_root.json"

#: Env var overriding the cache file path.
CACHE_ENV_VAR = "PUSPA_TRUST_ROOT_CACHE"

#: Seconds in a day, for age arithmetic.
_SECONDS_PER_DAY = 86400


class TrustRootError(Exception):
    """Trust-root refresh or validation failed."""


def _now() -> datetime:
    return datetime.now(UTC)


def default_cache_path() -> Path:
    """Default cache file for the refreshed trust root.

    ``PUSPA_TRUST_ROOT_CACHE`` wins when set; otherwise
    ``~/.local/share/puspa/trust_root/trusted_root.json``.
    """
    env = os.environ.get(CACHE_ENV_VAR)
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "puspa" / "trust_root" / CACHE_FILENAME


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


def cached_root_age_days(cache_path=None, *, now: datetime | None = None) -> float | None:
    """Age of the cached root in days, or None when there is no cache file."""
    path = _resolve_cache_path(cache_path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    now = now or _now()
    return (now.timestamp() - mtime) / _SECONDS_PER_DAY


def check_staleness(
    cache_path=None,
    *,
    max_age_days: float = DEFAULT_STALE_AFTER_DAYS,
    sink=None,
    now: datetime | None = None,
) -> dict:
    """Check whether the cached trust root is stale.

    Returns ``{"stale": bool, "age_days": float | None, "path": str,
    "reason": str}``. When the root is stale (older than
    ``max_age_days``) or missing, a ``trust_root_stale`` event is
    recorded through ``sink`` — a stale root is never used silently.
    This function performs no network access and never raises for a
    missing or unreadable cache file.
    """
    path = _resolve_cache_path(cache_path)
    age = cached_root_age_days(path, now=now)
    result: dict[str, str | bool | float | None]
    if age is None:
        result = {
            "stale": True,
            "age_days": None,
            "path": str(path),
            "reason": "no cached trust root",
        }
    elif age > max_age_days:
        result = {
            "stale": True,
            "age_days": age,
            "path": str(path),
            "reason": f"cached trust root is {age:.1f} days old (stale after {max_age_days} days)",
        }
    else:
        result = {
            "stale": False,
            "age_days": age,
            "path": str(path),
            "reason": "cached trust root is fresh",
        }
    if result["stale"]:
        _emit(
            sink,
            "trust_root_stale",
            {
                "path": str(path),
                "age_days": age,
                "max_age_days": max_age_days,
                "reason": result["reason"],
            },
        )
    return result


def _validate_trusted_root_bytes(data: bytes) -> None:
    """Reject bytes that are not a real Sigstore trust root.

    Raises ``TrustRootError``. Checks: non-empty, under the size cap,
    valid JSON, and parses as a ``sigstore.models.TrustedRoot``
    (which validates the media type and required fields). A bad root
    is rejected here, before it can reach the cache.
    """
    if not data:
        raise TrustRootError("downloaded trust root is empty")
    if len(data) > MAX_TRUSTED_ROOT_BYTES:
        raise TrustRootError(
            f"downloaded trust root is {len(data)} bytes, over the"
            f" {MAX_TRUSTED_ROOT_BYTES} byte cap"
        )
    try:
        parsed = json.loads(data)
    except Exception as exc:
        raise TrustRootError(f"downloaded trust root is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise TrustRootError("downloaded trust root is not a JSON object")
    try:
        from sigstore.models import TrustedRoot
    except Exception as exc:
        raise TrustRootError(f"sigstore library is not installed: {exc}") from exc
    try:
        with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            TrustedRoot.from_file(tmp_path)
        finally:
            os.unlink(tmp_path)
    except TrustRootError:
        raise
    except Exception as exc:
        raise TrustRootError(f"downloaded trust root does not parse: {exc}") from exc


def _default_updater_factory(tuf_url: str):
    """Build the sigstore package's own TUF updater for ``tuf_url``.

    ``TrustUpdater.__init__`` refreshes the signed TUF metadata over
    the network and ``get_trusted_root_path()`` downloads the
    ``trusted_root.json`` TUF target (hash-checked against the signed
    metadata). This is the only place in Puspa that talks to the TUF
    repository; the verify path never calls it. Raises
    ``TrustRootError`` when the sigstore library (or its TUF client)
    is unavailable.
    """
    try:
        from sigstore._internal.tuf import TrustUpdater
    except Exception as exc:
        raise TrustRootError(f"sigstore TUF client is not available: {exc}") from exc
    return TrustUpdater(tuf_url)


def _install_cached_root(data: bytes, path: Path, now: datetime | None = None) -> None:
    """Atomically install ``data`` as the cached root at ``path``.

    Writes to a temp file in the same directory and renames over the
    old cache, so a crash mid-write never leaves a half-written root
    in place. The parent directory is created when missing. The
    installed file is stamped with ``now`` (the refresh time) so its
    age is measured on the same clock the staleness check uses.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".trusted_root-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        if now is not None:
            stamp = now.timestamp()
            os.utime(path, (stamp, stamp))
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def refresh_trust_root(
    cache_path=None,
    *,
    tuf_url: str = DEFAULT_TUF_URL,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    force: bool = False,
    sink=None,
    now: datetime | None = None,
    updater_factory=None,
) -> dict:
    """Refresh the cached trust root from the Sigstore TUF repository.

    Returns an outcome dict ``{"status": ..., "path": str | None,
    "age_days": float | None, "reason": str}`` and never raises for a
    failed refresh. Statuses:

    - ``fresh``: the cache is younger than ``max_age_days`` (or
      ``force`` is False and it exists and is fresh) — no network.
    - ``refreshed``: a new root was downloaded, validated, and
      installed (a ``trust_root_refreshed`` event is recorded).
    - ``unchanged``: the TUF repository's root is byte-identical to
      the cached one — the cache file is left untouched.
    - ``failed``: download or validation failed; the old cache is
      untouched and a ``trust_root_refresh_failed`` event is recorded.
    - ``unavailable``: the sigstore library or its TUF client is not
      installed; nothing was attempted over the network.

    ``updater_factory`` is an injectable ``(tuf_url) -> updater``
    whose ``get_trusted_root_path()`` returns a local path to the
    downloaded root; tests pass a fake. ``now`` is an injectable
    clock (UTC datetime) for tests.
    """
    path = _resolve_cache_path(cache_path)
    now = now or _now()
    age = cached_root_age_days(path, now=now)
    if not force and age is not None and age <= max_age_days:
        return {
            "status": "fresh",
            "path": str(path),
            "age_days": age,
            "reason": f"cached trust root is {age:.1f} days old"
            f" (refresh after {max_age_days} days)",
        }
    factory = updater_factory or _default_updater_factory
    try:
        updater = factory(tuf_url)
        downloaded_path = updater.get_trusted_root_path()
        data = Path(downloaded_path).read_bytes()
    except TrustRootError as exc:
        _emit(sink, "trust_root_refresh_failed", {"path": str(path), "reason": str(exc)})
        return {
            "status": "unavailable" if "not available" in str(exc) else "failed",
            "path": str(path) if path.exists() else None,
            "age_days": age,
            "reason": str(exc),
        }
    except Exception as exc:
        reason = f"trust root download failed: {exc}"
        _emit(sink, "trust_root_refresh_failed", {"path": str(path), "reason": reason})
        return {
            "status": "failed",
            "path": str(path) if path.exists() else None,
            "age_days": age,
            "reason": reason,
        }
    try:
        _validate_trusted_root_bytes(data)
    except TrustRootError as exc:
        reason = f"downloaded trust root rejected: {exc}"
        _emit(sink, "trust_root_refresh_failed", {"path": str(path), "reason": reason})
        return {
            "status": "failed",
            "path": str(path) if path.exists() else None,
            "age_days": age,
            "reason": reason,
        }
    try:
        if path.is_file() and path.read_bytes() == data:
            return {
                "status": "unchanged",
                "path": str(path),
                "age_days": age,
                "reason": "cached trust root is already current",
            }
        _install_cached_root(data, path, now=now)
    except Exception as exc:
        reason = f"could not install refreshed trust root: {exc}"
        _emit(sink, "trust_root_refresh_failed", {"path": str(path), "reason": reason})
        return {
            "status": "failed",
            "path": str(path) if path.exists() else None,
            "age_days": age,
            "reason": reason,
        }
    new_age = cached_root_age_days(path, now=now)
    _emit(
        sink,
        "trust_root_refreshed",
        {"path": str(path), "previous_age_days": age, "age_days": new_age},
    )
    return {
        "status": "refreshed",
        "path": str(path),
        "age_days": new_age,
        "reason": "cached trust root refreshed from the TUF repository",
    }


def resolve_trust_root_for_verify(
    cache_path=None,
    *,
    max_age_days: float = DEFAULT_STALE_AFTER_DAYS,
    sink=None,
    now: datetime | None = None,
) -> tuple[str | None, dict]:
    """Pick the trust root a verification should use.

    Returns ``(path_or_None, status)`` where ``status`` is the
    ``check_staleness`` result. When a cached root exists it is
    returned — even when stale, because it is still the freshest
    TUF-verified copy we have — but staleness is recorded through
    ``sink`` first, so it is never used silently. When no cached root
    exists, returns ``(None, status)``: the caller falls back to the
    bundled ``"production"`` root in ``verify_sigstore_bundle``.
    Performs no network access.
    """
    status = check_staleness(cache_path, max_age_days=max_age_days, sink=sink, now=now)
    if status["age_days"] is None:
        return None, status
    return status["path"], status


def run_trust_root_refresh_job(
    *,
    sink=None,
    cache_path=None,
    tuf_url: str = DEFAULT_TUF_URL,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    stale_after_days: float = DEFAULT_STALE_AFTER_DAYS,
    force: bool = False,
    now: datetime | None = None,
    updater_factory=None,
) -> dict:
    """One run of the scheduled trust-root refresh job.

    Checks staleness first (recorded through ``sink``), then
    refreshes when due. Returns the refresh outcome dict with the
    staleness result attached as ``"staleness"``. Never raises: a
    failed job reports ``failed``/``unavailable`` and is retried by
    the next daily trigger. This is what the scheduler's
    ``trust_root_refresh`` trigger executes; it never runs on the
    verify path.
    """
    now = now or _now()
    staleness = check_staleness(cache_path, max_age_days=stale_after_days, sink=sink, now=now)
    outcome = refresh_trust_root(
        cache_path,
        tuf_url=tuf_url,
        max_age_days=max_age_days,
        force=force,
        sink=sink,
        now=now,
        updater_factory=updater_factory,
    )
    outcome["staleness"] = staleness
    return outcome
