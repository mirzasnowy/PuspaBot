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
- ``PUSPA_BOT_NAME`` - the bot name carried by content-free
  notifications (default ``"Puspa"``).

Engine (AST-05): which agent answers a channel message.

- ``PUSPA_CHANNEL_ENGINE`` - ``builtin`` (PuspaBot's own
  engine, the default), ``dify``, ``coze``, or ``bailian``.
  An unknown value fails at load time.
- ``DIFY_API_KEY`` - the Dify app's API key.
  ``DIFY_BASE_URL`` - Dify API base
  (default ``https://api.dify.ai/v1``; a self-hosted Dify uses
  its own URL).
- ``COZE_API_TOKEN`` - the Coze personal access token.
  ``COZE_BOT_ID`` - the Coze bot id to answer as.
  ``COZE_BASE_URL`` - Coze API base
  (default ``https://api.coze.com``).
- ``BAILIAN_API_KEY`` - the DashScope API key.
  ``BAILIAN_APP_ID`` - the Bailian application id.
  ``BAILIAN_BASE_URL`` - DashScope API base (default
  ``https://dashscope-intl.aliyuncs.com``; China uses
  ``https://dashscope.aliyuncs.com``).

An external engine is built only when selected: asking for
one without its key (or bot/app id) raises
:class:`ChannelConfigError` naming the missing variable, at
runner start - not at first message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from .authz import Allowlist
from .engines import ENGINE_NAMES, ExternalEngine
from .engines_bailian import BAILIAN_DEFAULT_BASE_URL, BailianEngine
from .engines_coze import COZE_DEFAULT_BASE_URL, CozeEngine
from .engines_dify import DIFY_DEFAULT_BASE_URL, DifyEngine
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
    engine: str = "builtin"
    dify_api_key: str | None = None
    dify_base_url: str = DIFY_DEFAULT_BASE_URL
    coze_api_token: str | None = None
    coze_bot_id: str | None = None
    coze_base_url: str = COZE_DEFAULT_BASE_URL
    bailian_api_key: str | None = None
    bailian_app_id: str | None = None
    bailian_base_url: str = BAILIAN_DEFAULT_BASE_URL

    def __post_init__(self) -> None:
        if self.engine not in ENGINE_NAMES:
            raise ChannelConfigError(
                f"unknown channel engine: {self.engine!r} "
                f"(expected one of: {', '.join(ENGINE_NAMES)})"
            )

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

    def build_engine(self, http_client: httpx.Client | None = None) -> ExternalEngine | None:
        """Build the configured external engine, or None for builtin.

        Raises :class:`ChannelConfigError` naming the missing
        variable when the selected engine has no key (Dify,
        Coze, Bailian) or no bot/app id (Coze, Bailian).
        """
        if self.engine == "builtin":
            return None
        if self.engine == "dify":
            if not self.dify_api_key:
                raise ChannelConfigError("Dify engine selected but DIFY_API_KEY is not set")
            return DifyEngine(
                self.dify_api_key, base_url=self.dify_base_url, http_client=http_client
            )
        if self.engine == "coze":
            if not self.coze_api_token:
                raise ChannelConfigError("Coze engine selected but COZE_API_TOKEN is not set")
            if not self.coze_bot_id:
                raise ChannelConfigError("Coze engine selected but COZE_BOT_ID is not set")
            return CozeEngine(
                self.coze_api_token,
                self.coze_bot_id,
                base_url=self.coze_base_url,
                http_client=http_client,
            )
        if self.engine == "bailian":
            if not self.bailian_api_key:
                raise ChannelConfigError("Bailian engine selected but BAILIAN_API_KEY is not set")
            if not self.bailian_app_id:
                raise ChannelConfigError("Bailian engine selected but BAILIAN_APP_ID is not set")
            return BailianEngine(
                self.bailian_api_key,
                self.bailian_app_id,
                base_url=self.bailian_base_url,
                http_client=http_client,
            )
        raise ChannelConfigError(
            f"unknown channel engine: {self.engine!r} (expected one of: {', '.join(ENGINE_NAMES)})"
        )


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
        engine=source.get("PUSPA_CHANNEL_ENGINE", "builtin"),
        dify_api_key=source.get("DIFY_API_KEY") or None,
        dify_base_url=source.get("DIFY_BASE_URL", DIFY_DEFAULT_BASE_URL),
        coze_api_token=source.get("COZE_API_TOKEN") or None,
        coze_bot_id=source.get("COZE_BOT_ID") or None,
        coze_base_url=source.get("COZE_BASE_URL", COZE_DEFAULT_BASE_URL),
        bailian_api_key=source.get("BAILIAN_API_KEY") or None,
        bailian_app_id=source.get("BAILIAN_APP_ID") or None,
        bailian_base_url=source.get("BAILIAN_BASE_URL", BAILIAN_DEFAULT_BASE_URL),
    )
