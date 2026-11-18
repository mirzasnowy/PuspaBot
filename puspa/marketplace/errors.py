"""Errors for the Puspa skill/module marketplace (G3)."""

from __future__ import annotations

__all__ = [
    "ApprovalRequiredError",
    "DiscoveryError",
    "EntitlementError",
    "HashMismatchError",
    "LicenseNotAllowedError",
    "ManifestError",
    "MarketplaceError",
    "ShareError",
    "ToolLimitError",
    "TrustError",
]


class MarketplaceError(Exception):
    """Base error for marketplace operations."""


class DiscoveryError(MarketplaceError):
    """Skill discovery failed (registry unreachable, bad response,
    or a discovered skill cannot be fetched). Discovery never
    raises for a bad registry response at search time — the
    client returns no results instead — so this error is for
    the fetch/install half of discovery."""


class ApprovalRequiredError(MarketplaceError):
    """A skill install was refused because no explicit user
    approval for this skill (and content hash) exists. Installs
    never proceed without approval (G4)."""


class HashMismatchError(MarketplaceError):
    """Downloaded skill text does not match the expected sha256.
    The install is refused; the content may have changed between
    approval and download."""


class LicenseNotAllowedError(MarketplaceError):
    """A discovered skill's licence is outside the install
    allowlist (MIT / Apache-2.0 / BSD) or could not be
    determined. The install is refused."""


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
