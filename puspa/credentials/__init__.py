"""Credential package (G22): resolver, pool, scope, redaction, auth.

One resolver used by every surface (PWA, gateway, cron,
harness); a credential pool with per-model cooldown rotation;
secret scope per profile; per-type secret redaction; OAuth
(PKCE, device flow, anonymous) login flows. See each module's
docstring for the pattern source and the call sites.
"""

from . import auth, pool, redact, resolver, scope

__all__ = ["auth", "pool", "redact", "resolver", "scope"]
