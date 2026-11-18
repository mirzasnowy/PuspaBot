"""Channel configuration (G14).

Tokens and ids come ONLY from the environment (or an explicit
``ChannelConfig`` passed by the caller). They are NEVER hardcoded
in code or tests, and never logged. A platform is enabled only
when its token is present; asking for an adapter without its
token raises :class:`ChannelConfigError` at construction time,
not at first send.

Environment variables:

- ``TELEGRAM_BOT_TOKEN`` — Telegram Bot API token.
- ``TELEGRAM_WEBHOOK_SECRET`` — optional secret token Telegram
  sends as ``X-Telegram-Bot-Api-Secret-Token`` on webhooks.
- ``WHATSAPP_ACCESS_TOKEN`` — WhatsApp Cloud API access token.
- ``WHATSAPP_PHONE_NUMBER_ID`` — the sender phone number id.
- ``WHATSAPP_VERIFY_TOKEN`` — the webhook verification token.
- ``WHATSAPP_APP_SECRET`` — app secret for the
  ``X-Hub-Signature-256`` check (optional but recommended).
- ``PUSPA_CHANNEL_ALLOWLIST`` — comma-separated
  ``platform:chat_id`` pairs the gateway answers without pairing.
- ``PUSPA_CHANNEL_STATE_DIR`` — where heartbeat/offset state is
  kept (default: the current working directory's ``.puspa-channels``).
- ``PUSPA_BOT_NAME`` — the bot name carried by content-free
  notifications (default ``"Puspa"``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .authz import Allowlist
from .errors import ChannelError

__all__ = ["ChannelConfig", "ChannelConfigError", "load_config"]


class ChannelConfigError(ChannelError):
    """Channel configuration is missing or invalid."""


@dataclass(frozen=True)
class ChannelConfig:
    """All channel settings, from env or from the caller."""

    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None
    allowlist: Allowlist = field(default_factory=Allowlist)
    state_dir: str = ".puspa-channels"
    bot_name: str = "Puspa"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_access_token and self.whatsapp_phone_number_id)

    def require_telegram(self) -> str:
        if not self.telegram_bot_token:
            raise ChannelConfigError("Telegram is not configured: set TELEGRAM_BOT_TOKEN")
        return self.telegram_bot_token

    def require_whatsapp(self) -> tuple[str, str]:
        if not self.whatsapp_access_token or not self.whatsapp_phone_number_id:
            raise ChannelConfigError(
                "WhatsApp is not configured: set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID"
            )
        return self.whatsapp_access_token, self.whatsapp_phone_number_id


def load_config(env: dict | None = None) -> ChannelConfig:
    """Build a config from the environment (``os.environ`` by default)."""
    source = os.environ if env is None else env
    return ChannelConfig(
        telegram_bot_token=source.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_webhook_secret=source.get("TELEGRAM_WEBHOOK_SECRET") or None,
        whatsapp_access_token=source.get("WHATSAPP_ACCESS_TOKEN") or None,
        whatsapp_phone_number_id=source.get("WHATSAPP_PHONE_NUMBER_ID") or None,
        whatsapp_verify_token=source.get("WHATSAPP_VERIFY_TOKEN") or None,
        whatsapp_app_secret=source.get("WHATSAPP_APP_SECRET") or None,
        allowlist=Allowlist.parse(source.get("PUSPA_CHANNEL_ALLOWLIST")),
        state_dir=source.get("PUSPA_CHANNEL_STATE_DIR", ".puspa-channels"),
        bot_name=source.get("PUSPA_BOT_NAME", "Puspa"),
    )
