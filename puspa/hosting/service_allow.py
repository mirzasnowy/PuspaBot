"""Explicit service allowlist for shared-server mode.

Pattern (DuskBot ``server/request-auth.ts``, rewritten as Puspa code):
in ``service`` mode a loopback request WITHOUT a session may only
reach an explicit ``SERVICE_ALLOW`` list of (method, path regex)
entries. Everything else needs a session. There is no implicit
allowance: the default list is three read-only health/version
endpoints, and the operator extends it deliberately.

Matching is ``re.fullmatch`` on the path, so an entry matches exactly
what the operator wrote — ``/health`` does not also match
``/health/secret``. Patterns are compiled once at construction; an
invalid regex fails loudly at construction time, never at request
time. The method ``"*"`` matches any method.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "DEFAULT_SERVICE_ALLOW",
    "ServiceAllowlist",
]

#: Default allowlist: read-only endpoints that are safe to expose to
#: any loopback client without a session. Deliberately minimal; the
#: operator adds entries for anything else.
DEFAULT_SERVICE_ALLOW: tuple[tuple[str, str], ...] = (
    ("GET", r"/health"),
    ("GET", r"/healthz"),
    ("GET", r"/version"),
)


@dataclass(frozen=True)
class _Entry:
    method: str
    pattern: re.Pattern

    def matches(self, method: str, path: str) -> bool:
        if self.method != "*" and self.method != method.upper():
            return False
        return self.pattern.fullmatch(path) is not None


@dataclass
class ServiceAllowlist:
    """An explicit (method, path-regex) allowlist for sessionless access."""

    entries: tuple[tuple[str, str], ...] = DEFAULT_SERVICE_ALLOW
    _compiled: tuple[_Entry, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        compiled = []
        for method, pattern in self.entries:
            if not isinstance(method, str) or not method:
                raise ValueError(f"service allowlist method must be a non-empty string: {method!r}")
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(
                    f"service allowlist pattern must be a non-empty string: {pattern!r}"
                )
            try:
                compiled.append(_Entry(method.upper(), re.compile(pattern)))
            except re.error as exc:
                raise ValueError(f"invalid service allowlist pattern {pattern!r}: {exc}") from exc
        object.__setattr__(self, "_compiled", tuple(compiled))

    def allowed(self, method: str, path: str) -> bool:
        """True when (method, path) matches at least one entry."""
        if not isinstance(method, str) or not isinstance(path, str):
            return False
        return any(entry.matches(method, path) for entry in self._compiled)

    def __len__(self) -> int:
        return len(self._compiled)
