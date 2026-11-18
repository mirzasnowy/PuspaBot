"""Per-profile availability checks (``check_fn``) with a TTL cache (G23).

A tool may need a credential, a permission, or a live service before
it is offered to a profile. This module answers "is this module
available for this profile?" and caches the answer per profile with
a TTL, so a hot turn does not re-check credentials on every call:

- a fresh cache entry is returned as-is;
- a stale entry is **re-checked, not failed** -- the check runs
  again and the cache is refreshed with the new answer;
- a check that raises is fail-soft: the tool is reported
  unavailable, with the reason recorded, and nothing raises.

Credential checks are the default. The credential scope function
``puspa.credentials.scope.check_credential(profile_id, provider,
model=None) -> bool`` is built by the G22 worker in parallel, so
this module imports it **lazily, inside the call**, and treats a
missing module (or a missing function) as "credential not
verifiable" -- unavailable, not a crash. Tests inject a stub
checker instead of relying on the real module existing.

A caller can also register a custom check per module with
:meth:`AvailabilityChecker.register_check`; a registered check wins
over the credential default for that module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_TTL_S",
    "AvailabilityChecker",
    "CheckResult",
    "check_credential_available",
    "default_checker",
]

#: Default cache lifetime for one availability answer, in seconds.
DEFAULT_TTL_S = 300.0

#: Dotted path of the credential-scope function built by G22.
CREDENTIAL_SCOPE_PATH = "puspa.credentials.scope"


@dataclass(frozen=True)
class CheckResult:
    """One availability answer."""

    available: bool
    #: Why the tool is (un)available: ``"ok"``, ``"no_check"``,
    #: ``"credential_missing"``, ``"credential_module_missing"``,
    #: ``"check_failed"``, ...
    reason: str
    #: True when this answer came from the cache.
    cached: bool = False


#: A check function: ``fn(profile_id, provider=None, model=None) -> bool``.
CheckFn = Callable[..., bool]


def check_credential_available(
    profile_id: str | None,
    provider: str | None,
    model: str | None = None,
) -> CheckResult:
    """Lazy credential-scope check (the G22 seam).

    Imports ``puspa.credentials.scope.check_credential`` only when
    called. A missing module/function means the credential cannot be
    verified yet, so the tool is unavailable -- never a crash.
    """
    try:
        from puspa.credentials.scope import check_credential
    except (ImportError, AttributeError):
        return CheckResult(False, "credential_module_missing")
    try:
        ok = check_credential(profile_id, provider, model=model)
    except TypeError:
        # Older signature without ``model``: retry positionally.
        try:
            ok = check_credential(profile_id, provider)
        except Exception:
            return CheckResult(False, "check_failed")
    except Exception:
        return CheckResult(False, "check_failed")
    return CheckResult(bool(ok), "ok" if ok else "credential_missing")


class AvailabilityChecker:
    """TTL-cached per-profile availability checks."""

    def __init__(
        self,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Callable[[], float] | None = None,
        credential_checker: CheckFn | None = None,
    ) -> None:
        self.ttl_s = float(ttl_s)
        self._clock = clock or time.monotonic
        #: Custom per-module checks: module -> fn(profile_id, provider, model).
        self._checks: dict[str, CheckFn] = {}
        #: Cache: (module, profile_id, provider, model) -> (expires_at, result).
        self._cache: dict[tuple[Any, ...], tuple[float, CheckResult]] = {}
        self._credential_checker = credential_checker

    # -- registration ---------------------------------------------------

    def register_check(self, module: str, fn: CheckFn) -> None:
        """Register a custom availability check for one module."""
        self._checks[module] = fn
        self.invalidate(module=module)

    def unregister_check(self, module: str) -> bool:
        """Remove a custom check. Returns True when one existed."""
        removed = self._checks.pop(module, None) is not None
        if removed:
            self.invalidate(module=module)
        return removed

    # -- checking -------------------------------------------------------

    def check(
        self,
        module: str,
        profile_id: str | None,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> CheckResult:
        """Is ``module`` available for ``profile_id`` (cached, TTL)?"""
        key = (module, profile_id, provider, model)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None:
            expires_at, result = cached
            if now < expires_at:
                return CheckResult(result.available, result.reason, cached=True)
        result = self._check_uncached(module, profile_id, provider=provider, model=model)
        self._cache[key] = (now + self.ttl_s, result)
        return result

    def _check_uncached(
        self,
        module: str,
        profile_id: str | None,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> CheckResult:
        fn = self._checks.get(module)
        if fn is None:
            if provider is None:
                return CheckResult(True, "no_check")
            checker = self._credential_checker
            if checker is not None:
                try:
                    ok = checker(profile_id, provider, model=model)
                except TypeError:
                    try:
                        ok = checker(profile_id, provider)
                    except Exception:
                        return CheckResult(False, "check_failed")
                except Exception:
                    return CheckResult(False, "check_failed")
                return CheckResult(bool(ok), "ok" if ok else "credential_missing")
            return check_credential_available(profile_id, provider, model=model)
        try:
            ok = fn(profile_id, provider, model=model)
        except TypeError:
            try:
                ok = fn(profile_id)
            except Exception:
                return CheckResult(False, "check_failed")
        except Exception:
            return CheckResult(False, "check_failed")
        return CheckResult(bool(ok), "ok" if ok else "check_failed")

    def check_tool(
        self,
        definition: Any,
        profile_id: str | None,
        *,
        model: str | None = None,
    ) -> CheckResult:
        """Availability of one registered tool definition for a profile.

        Uses the tool's own ``check`` when it has one, the credential
        check when it names a ``provider``, and "available" otherwise.
        The tool's own check is wrapped in the same TTL cache.
        """
        check = getattr(definition, "check", None)
        provider = getattr(definition, "provider", None)
        name = getattr(definition, "name", "tool")
        if check is not None:
            key = (f"tool:{name}", profile_id, provider, model)
            now = self._clock()
            cached = self._cache.get(key)
            if cached is not None and now < cached[0]:
                cached_result = cached[1]
                return CheckResult(cached_result.available, cached_result.reason, cached=True)
            try:
                ok = check(profile_id)
            except Exception:
                result = CheckResult(False, "check_failed")
            else:
                result = CheckResult(bool(ok), "ok" if ok else "check_failed")
            self._cache[key] = (now + self.ttl_s, result)
            return result
        return self.check(name, profile_id, provider=provider, model=model)

    # -- cache management -----------------------------------------------

    def invalidate(
        self,
        *,
        profile_id: str | None = None,
        module: str | None = None,
    ) -> int:
        """Drop cache entries. No filter drops everything.

        Returns the number of entries dropped.
        """
        if profile_id is None and module is None:
            dropped = len(self._cache)
            self._cache.clear()
            return dropped
        doomed = [
            key
            for key in self._cache
            if (profile_id is None or key[1] == profile_id) and (module is None or key[0] == module)
        ]
        for key in doomed:
            del self._cache[key]
        return len(doomed)

    def cache_size(self) -> int:
        return len(self._cache)


#: Process-wide default checker (300 s TTL, real credential seam).
_DEFAULT_CHECKER = AvailabilityChecker()


def default_checker() -> AvailabilityChecker:
    """The process-wide default :class:`AvailabilityChecker`."""
    return _DEFAULT_CHECKER
