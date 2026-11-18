"""Sharing skills between users (G3).

Modes (DuskBot parity, rewritten): ``off`` | ``ask`` | ``auto``,
default ``ask``. Sharing is a pure decision function: it never
mutates its inputs.

Invariants (both tested):

1. Sharing never changes the running conversation's index: the
   caller passes the conversation index in and gets a grant list
   back; the index object is left untouched.
2. A skill only reaches peers no more numerous than where it was
   learned: grants are capped at the number of peers the skill
   was learned from (``learned_from_peers``). A skill learned
   alone (0 peers) cannot be shared to anyone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ShareError

__all__ = [
    "DEFAULT_SHARE_MODE",
    "SHARE_MODES",
    "ShareDecision",
    "share_skill",
]

SHARE_MODES = frozenset({"off", "ask", "auto"})

#: Default share mode.
DEFAULT_SHARE_MODE = "ask"


@dataclass
class ShareDecision:
    """Outcome of a share request."""

    shared: bool
    granted_peers: list = field(default_factory=list)
    reason: str = ""
    mode: str = DEFAULT_SHARE_MODE

    def to_dict(self) -> dict:
        return {
            "shared": self.shared,
            "granted_peers": list(self.granted_peers),
            "reason": self.reason,
            "mode": self.mode,
        }


def share_skill(
    skill_id: str,
    *,
    mode: str = DEFAULT_SHARE_MODE,
    peers: list | None = None,
    learned_from_peers: int = 0,
    conversation_index: dict | None = None,
    ask_user=None,
) -> ShareDecision:
    """Decide whether ``skill_id`` may be shared with ``peers``.

    ``peers`` is the requested peer list (user ids). ``ask_user``
    is an optional callable ``ask_user(skill_id, peers) -> bool``
    used in ``ask`` mode; without it, ``ask`` mode declines.
    ``conversation_index`` is accepted only so the caller can
    prove the invariant: it is read, never written.

    Grants are capped at ``learned_from_peers`` (invariant 2).
    """
    if mode not in SHARE_MODES:
        raise ShareError(f"unknown share mode {mode!r}")
    peers = list(peers or [])
    if mode == "off":
        return ShareDecision(shared=False, mode=mode, reason="sharing is off")
    if learned_from_peers <= 0:
        return ShareDecision(
            shared=False,
            mode=mode,
            reason="skill was learned alone; it reaches no peers",
        )
    if mode == "ask":
        approved = ask_user(skill_id, peers) if ask_user is not None else False
        if not approved:
            return ShareDecision(shared=False, mode=mode, reason="user declined the share")
    # Cap: no more peers than the skill was learned from.
    granted = peers[:learned_from_peers]
    if not granted:
        return ShareDecision(shared=False, mode=mode, reason="no peers requested")
    if len(peers) > len(granted):
        reason = (
            f"shared with {len(granted)} of {len(peers)} requested peers: "
            "a skill only reaches as many peers as it was learned from"
        )
    else:
        reason = f"shared with {len(granted)} peer(s)"
    # Invariant 1: the conversation index is never touched here.
    # (The caller keeps it; this function returns grants only.)
    _ = conversation_index
    return ShareDecision(shared=True, granted_peers=granted, reason=reason, mode=mode)
