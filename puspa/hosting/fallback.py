"""Connection fallback: direct, then Cloudflare tunnel, then relay.

Pattern (DuskBot link/relay fallback order, rewritten as Puspa
code): the app reaches the server by the first route that works, in
the fixed order ``direct -> tunnel -> relay``. Direct is cheapest
and fastest; the tunnel crosses NAT without a public port; the relay
is the last resort and only ever carries sealed frames (see
``relay.py``).

This module owns the ORDER and the attempt accounting — which route
was tried, in which order, and why each failed. It does NOT own the
transports: each route is an injected probe callable supplied by the
caller (a real direct HTTPS probe, a real tunnel client check, a real
relay reachability check). A probe returns a truthy value on success
(the connection handle, or anything truthy) and either returns a
falsy value or raises on failure; either way the next route is tried.
That seam is deliberate: the transports need a live HTTP route and a
Cloudflare tunnel client, neither of which exists in this repo yet
(see ``docs/hosting-auth.md`` follow-ups). The ordering, the
recording, and the all-failed error are real here and fully tested.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import AllRoutesFailedError

__all__ = [
    "FALLBACK_ORDER",
    "ConnectionResult",
    "RouteAttempt",
    "connect_with_fallback",
    "pick_route",
]

#: The fixed fallback order. Never reordered per call: the order is
#: the contract (cheapest/fastest first, relay last).
FALLBACK_ORDER: tuple[str, str, str] = ("direct", "tunnel", "relay")


@dataclass(frozen=True)
class RouteAttempt:
    """One tried route: name, whether it worked, and the failure."""

    route: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class ConnectionResult:
    """The winning route, its probe result, and every attempt made."""

    route: str
    result: object
    attempts: tuple[RouteAttempt, ...] = ()


def pick_route(available) -> str | None:
    """First available route in fallback order, or None.

    ``available`` maps route name -> bool. Unknown route names are
    ignored; a missing route counts as unavailable.
    """
    for route in FALLBACK_ORDER:
        if available.get(route):
            return route
    return None


def connect_with_fallback(probes, *, on_attempt=None) -> ConnectionResult:
    """Try each route in fallback order; return the first that works.

    ``probes`` maps route name -> zero-argument callable. A probe
    that returns a truthy value wins; a falsy return or a raised
    exception records a failed attempt and moves to the next route.
    ``on_attempt`` (optional) is called with each ``RouteAttempt``
    as it happens, for live logging. Raises ``AllRoutesFailedError``
    (carrying every attempt) when no route works.
    """
    attempts: list[RouteAttempt] = []
    for route in FALLBACK_ORDER:
        probe = probes.get(route) if hasattr(probes, "get") else None
        if probe is None:
            attempt = RouteAttempt(route=route, ok=False, error="no probe for route")
            attempts.append(attempt)
            if on_attempt is not None:
                on_attempt(attempt)
            continue
        try:
            result = probe()
        except Exception as exc:  # probe failure is data, not a crash
            attempt = RouteAttempt(route=route, ok=False, error=f"{type(exc).__name__}: {exc}")
            attempts.append(attempt)
            if on_attempt is not None:
                on_attempt(attempt)
            continue
        if result:
            attempt = RouteAttempt(route=route, ok=True)
            attempts.append(attempt)
            if on_attempt is not None:
                on_attempt(attempt)
            return ConnectionResult(route=route, result=result, attempts=tuple(attempts))
        attempt = RouteAttempt(route=route, ok=False, error="probe returned a falsy value")
        attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(attempt)
    raise AllRoutesFailedError("no connection route worked (direct, tunnel, relay)", attempts)
