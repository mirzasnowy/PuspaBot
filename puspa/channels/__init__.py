"""Channels: Telegram, WhatsApp, Discord, Slack and LINE access to Puspa (G14).

The channels are access channels, NOT the home: the home stays
the PWA. One core, many adapters — every platform event is
normalised to a :class:`MessageEvent` and runs the same pipeline
(authorise -> resolve session -> agent -> reply through the
origin adapter).

Public surface:

- :class:`GatewayRunner` — the facade (``run.py``); one live
  runner per profile (G26 single writer).
- :class:`MessageEvent` — the normalised event (``events.py``).
- :class:`TelegramAdapter` / :class:`WhatsAppAdapter` /
  :class:`DiscordAdapter` / :class:`SlackAdapter` /
  :class:`LineAdapter` — raw HTTPS adapters over the pinned
  ``httpx`` (no new dependency). Discord and Slack poll over
  REST; LINE is webhook-only.
- :class:`ChannelPairing` / :class:`Allowlist` — authorisation
  (``authz.py``).
- :class:`InMemoryDeliveryLedger` /
  :class:`PostgresDeliveryLedger` — delivery + idempotency
  (``ledger.py``).
- :class:`TaskRunnerAgent` — the agent seam over the existing
  ``TaskRunner`` (``agent.py``).
- :class:`HeartbeatStore` — heartbeat + polling-offset restore
  (``heartbeat.py``).

See ``docs/channels.md`` for the architecture, the pairing flow,
and the honest limits (no live bot is verified in this iteration).
"""

from __future__ import annotations

from .agent import ChannelAgent, TaskRunnerAgent
from .authz import Allowlist, ChannelPairing, resolve_session_key
from .base import ChannelAdapter
from .config import ChannelConfig, ChannelConfigError, load_config
from .delivery import DeliveryService
from .discord import DiscordAdapter
from .errors import (
    BotLoopError,
    ChannelError,
    DeliveryError,
    PairingError,
    SingleWriterError,
    UnauthorizedChatError,
    WebhookVerificationError,
)
from .events import MessageEvent, mentions_puspa
from .heartbeat import Heartbeat, HeartbeatStore
from .inbound import InboundOutcome, handle_inbound
from .ledger import (
    DeliveryRecord,
    InMemoryDeliveryLedger,
    PostgresDeliveryLedger,
)
from .line import LineAdapter
from .notify import notification_text, notify
from .run import GatewayRunner
from .slack import SlackAdapter
from .telegram import TelegramAdapter
from .whatsapp import WhatsAppAdapter

__all__ = [
    "Allowlist",
    "BotLoopError",
    "ChannelAdapter",
    "ChannelAgent",
    "ChannelConfig",
    "ChannelConfigError",
    "ChannelError",
    "ChannelPairing",
    "DeliveryError",
    "DeliveryRecord",
    "DeliveryService",
    "DiscordAdapter",
    "GatewayRunner",
    "Heartbeat",
    "HeartbeatStore",
    "InMemoryDeliveryLedger",
    "InboundOutcome",
    "LineAdapter",
    "MessageEvent",
    "PairingError",
    "PostgresDeliveryLedger",
    "SingleWriterError",
    "SlackAdapter",
    "TaskRunnerAgent",
    "TelegramAdapter",
    "UnauthorizedChatError",
    "WebhookVerificationError",
    "WhatsAppAdapter",
    "handle_inbound",
    "load_config",
    "mentions_puspa",
    "notification_text",
    "notify",
    "resolve_session_key",
]
