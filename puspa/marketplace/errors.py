"""Errors for the Puspa skill/module marketplace (G3)."""

from __future__ import annotations

__all__ = [
    "EntitlementError",
    "ManifestError",
    "MarketplaceError",
    "ShareError",
    "ToolLimitError",
    "TrustError",
]


class MarketplaceError(Exception):
    """Base error for marketplace operations."""


class ManifestError(MarketplaceError):
    """A skill folder or manifest failed strict validation."""


class TrustError(MarketplaceError):
    """A module was refused because its trust score is too low and
    no explicit user approval was given."""


class EntitlementError(MarketplaceError):
    """A paid module was refused because no entitlement record
    exists for the user (real charging lives in G2 billing)."""


class ToolLimitError(MarketplaceError):
    """A run was refused because the tool count exceeds the hard
    limit (128). The audit event is ``agent.tool_limit_refused``."""


class ShareError(MarketplaceError):
    """A share request violated the share policy."""
