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
- ``DISCORD_BOT_TOKEN`` — Discord bot token.
- ``DISCORD_CHANNEL_IDS`` — comma-separated Discord channel ids
  the gateway polls for inbound messages.
- ``DISCORD_PUBLIC_KEY`` — optional Ed25519 public key (hex) for
  the interaction signature check.
- ``DISCORD_BOT_USER_ID`` — optional bot user id (bot-loop guard).
- ``SLACK_BOT_TOKEN`` — Slack bot token (``xoxb-...``).
- ``SLACK_CHANNEL_IDS`` — comma-separated Slack channel ids the
  gateway polls for inbound messages.
- ``SLACK_SIGNING_SECRET`` — optional signing secret for the
  ``X-Slack-Signature`` check.
- ``SLACK_BOT_USER_ID`` — optional bot user id (bot-loop guard).
- ``LINE_CHANNEL_ACCESS_TOKEN`` — LINE channel access token.
- ``LINE_CHANNEL_SECRET`` — optional channel secret for the
  ``X-Line-Signature`` check.
- ``LINE_BOT_USER_ID`` — optional bot user id (bot-loop guard).
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
    discord_bot_token: str | None = None
    discord_channel_ids: tuple[str, ...] = ()
    discord_public_key: str | None = None
    discord_bot_user_id: str | None = None
    slack_bot_token: str | None = None
    slack_channel_ids: tuple[str, ...] = ()
    slack_signing_secret: str | None = None
    slack_bot_user_id: str | None = None
    line_channel_access_token: str | None = None
    line_channel_secret: str | None = None
    line_bot_user_id: str | None = None
    allowlist: Allowlist = field(default_factory=Allowlist)
    state_dir: str = ".puspa-channels"
    bot_name: str = "Puspa"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_access_token and self.whatsapp_phone_number_id)

    @property
    def discord_enabled(self) -> bool:
        return bool(self.discord_bot_token)

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_bot_token)

    @property
    def line_enabled(self) -> bool:
        return bool(self.line_channel_access_token)

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

    def require_discord(self) -> str:
        if not self.discord_bot_token:
            raise ChannelConfigError("Discord is not configured: set DISCORD_BOT_TOKEN")
        return self.discord_bot_token

    def require_slack(self) -> str:
        if not self.slack_bot_token:
            raise ChannelConfigError("Slack is not configured: set SLACK_BOT_TOKEN")
        return self.slack_bot_token

    def require_line(self) -> str:
        if not self.line_channel_access_token:
            raise ChannelConfigError("LINE is not configured: set LINE_CHANNEL_ACCESS_TOKEN")
        return self.line_channel_access_token


def _parse_ids(raw: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (raw or "").split(",") if part.strip())


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
        discord_bot_token=source.get("DISCORD_BOT_TOKEN") or None,
        discord_channel_ids=_parse_ids(source.get("DISCORD_CHANNEL_IDS")),
        discord_public_key=source.get("DISCORD_PUBLIC_KEY") or None,
        discord_bot_user_id=source.get("DISCORD_BOT_USER_ID") or None,
        slack_bot_token=source.get("SLACK_BOT_TOKEN") or None,
        slack_channel_ids=_parse_ids(source.get("SLACK_CHANNEL_IDS")),
        slack_signing_secret=source.get("SLACK_SIGNING_SECRET") or None,
        slack_bot_user_id=source.get("SLACK_BOT_USER_ID") or None,
        line_channel_access_token=source.get("LINE_CHANNEL_ACCESS_TOKEN") or None,
        line_channel_secret=source.get("LINE_CHANNEL_SECRET") or None,
        line_bot_user_id=source.get("LINE_BOT_USER_ID") or None,
        allowlist=Allowlist.parse(source.get("PUSPA_CHANNEL_ALLOWLIST")),
        state_dir=source.get("PUSPA_CHANNEL_STATE_DIR", ".puspa-channels"),
        bot_name=source.get("PUSPA_BOT_NAME", "Puspa"),
    )
