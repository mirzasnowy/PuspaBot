"""Public pricing configuration.

The prices themselves are public data: the Plus/Pro/Team tiers and
the credit packs. Everything that records or settles money (the
contract, the usage ledger, signed entitlements, payment providers,
webhooks) lives in the hosted service.
"""

from __future__ import annotations

from .config import BillingConfig, default_config

__all__ = [
    "BillingConfig",
    "default_config",
]
