"""The normalised channel event (G14).

Every platform adapter translates its own payload into a
:class:`MessageEvent` and nothing else flows into the shared
pipeline: authorisation, session resolution, the agent call, and
the reply all see the same shape, whatever the platform. This is
the hermes-agent gateway pattern (``gateway/platforms/event``),
rewritten as Puspa code: one core, many adapters.

``dedup_key`` is the platform's own message identity
(``update_id`` on Telegram, ``messages[].id`` on WhatsApp) and is
what the delivery ledger uses to process each inbound message
exactly once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

#: A group message counts as addressed to Puspa when its text
#: contains "@puspa" (case-insensitive). The adapters set
#: ``mentions_bot`` from the platform payload when they can (a
#: Telegram mention entity); the text scan is the fallback, and
#: ``puspa/rooms/routing.py`` uses the same rule.
_MENTION_RE = re.compile(r"@puspa\b", re.IGNORECASE)


def mentions_puspa(text: str) -> bool:
    """True when the text mentions @puspa (case-insensitive)."""
    return bool(_MENTION_RE.search(text or ""))


@dataclass(frozen=True)
class MessageEvent:
    """One inbound user message, in platform-neutral form."""

    platform: str  # e.g. "telegram", "whatsapp"
    chat_id: str  # platform chat/thread id, as a string
    sender_id: str  # platform sender id, as a string
    text: str
    message_id: str  # platform message id (dedup key part)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_bot: bool = False  # sender is a bot (never answered)
    is_group: bool = False  # group/channel message (answered only via a room link, B6)
    mentions_bot: bool = False  # message mentions the bot (group routing, B6)
    raw: dict = field(default_factory=dict, repr=False)  # original payload

    @property
    def dedup_key(self) -> str:
        """Stable identity of this message for exactly-once handling."""
        return f"{self.platform}:{self.chat_id}:{self.message_id}"

    @property
    def session_key(self) -> str:
        """Canonical session key for this chat (G26 single writer)."""
        return f"{self.platform}:{self.chat_id}"
