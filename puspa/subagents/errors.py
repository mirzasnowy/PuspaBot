"""Errors of the public subagent lifecycle (G25).

Every error carries a stable machine-readable ``code`` so callers can
branch on the failure kind without parsing the message. The two codes
a caller must handle explicitly are ``UNKNOWN_HANDLE`` (a forged or
corrupt handle can never reach a child) and
``RECONNECT_UNAVAILABLE`` (a handle from a previous process
generation can never start a replacement child).
"""

from __future__ import annotations

__all__ = [
    "LifecycleError",
    "LimitExceededError",
    "NotTerminalError",
    "ReconnectUnavailableError",
    "ToolsetRejectedError",
    "UnknownHandleError",
    "UnsupportedOptionError",
]


class LifecycleError(Exception):
    """Base error for the subagent lifecycle."""

    code = "LIFECYCLE_ERROR"


class UnknownHandleError(LifecycleError):
    """The handle is malformed, forged, or names no known child.

    Code ``UNKNOWN_HANDLE``. A forged handle can never access a
    child: the caller learns nothing about which ids exist.
    """

    code = "UNKNOWN_HANDLE"


class ReconnectUnavailableError(LifecycleError):
    """The handle is from a previous process generation.

    Code ``RECONNECT_UNAVAILABLE``. The child no longer exists in
    this process and reconnect never starts a replacement child.
    """

    code = "RECONNECT_UNAVAILABLE"


class ToolsetRejectedError(LifecycleError):
    """The requested child toolset is unknown or widens the parent.

    Code ``TOOLSET_REJECTED``. A child may only run tools the parent
    already has; an unknown toolset name or a tool outside the
    parent toolset is refused before anything is spawned.
    """

    code = "TOOLSET_REJECTED"


class LimitExceededError(LifecycleError):
    """A bounded size (goal, context, metadata, steer text) is too large.

    Code ``LIMIT_EXCEEDED``. The lifecycle is fail-closed: oversized
    input is refused, never silently truncated at launch time.
    """

    code = "LIMIT_EXCEEDED"


class UnsupportedOptionError(LifecycleError):
    """An option that cannot be supported without weakening isolation.

    Code ``UNSUPPORTED_OPTION``. Per-launch working-directory
    overrides and per-tool timeouts are refused explicitly until
    they can be supported without weakening child isolation.
    """

    code = "UNSUPPORTED_OPTION"


class NotTerminalError(LifecycleError):
    """``result`` was asked for a child that has not finished.

    Code ``NOT_TERMINAL``. The result of a running child does not
    exist yet; the caller must ``wait`` for a terminal state first.
    """

    code = "NOT_TERMINAL"
