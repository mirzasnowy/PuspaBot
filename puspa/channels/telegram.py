"""Telegram adapter (G14).

Raw Telegram Bot API over HTTPS with the already-pinned
``httpx==0.28.1`` — no new dependency (python-telegram-bot is
LGPL and is not used; the Bot API is plain HTTPS and needs no
library). Two inbound modes, one processing path:

- **Long polling** — :meth:`TelegramAdapter.poll_once` calls
  ``getUpdates`` with the stored offset and a long-poll timeout.
  The gateway stores the next offset in the heartbeat state, so a
  restart resumes where polling stopped instead of reprocessing
  the backlog or dropping messages.
- **Webhook** — :meth:`TelegramAdapter.parse_update` accepts one
  ``Update`` object (the webhook body); :meth:`verify_webhook`
  checks the ``X-Telegram-Bot-Api-Secret-Token`` header when a
  secret is configured.

Sending uses ``sendMessage`` with a bounded retry (default 3
attempts) and exponential backoff; the sleep is injectable so
tests never wait. The HTTP client is injectable too: tests pass
an ``httpx.Client`` built on ``MockTransport`` and never touch
``api.telegram.org``.

Bot-loop guard: an update whose ``from.is_bot`` is true, or whose
sender id equals the bot's own id (when known), is normalised
with ``is_bot=True`` and the pipeline refuses it. Group messages
are normalised with ``is_group=True``; they are answered only when
a room is linked to the group chat (B6, ``puspa/rooms/routing.py``)
— unlinked groups are still dropped by the pipeline.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from .base import ChannelAdapter
from .errors import DeliveryError
from .events import MessageEvent, mentions_puspa

__all__ = ["TELEGRAM_API_BASE", "TelegramAdapter"]

TELEGRAM_API_BASE = "https://api.telegram.org"

#: Bounded retries for sendMessage: 3 attempts, 1s / 2s backoff.
MAX_SEND_ATTEMPTS = 3
SEND_BACKOFF_SECONDS = (1.0, 2.0)


def _parse_timestamp(epoch: int | float | None) -> datetime:
    if epoch is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(float(epoch), tz=UTC)


class TelegramAdapter(ChannelAdapter):
    """Telegram Bot API adapter (raw HTTPS, httpx only)."""

    platform = "telegram"

    def __init__(
        self,
        bot_token: str,
        *,
        http_client: httpx.Client | None = None,
        webhook_secret: str | None = None,
        bot_user_id: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bot_token:
            raise ValueError("TelegramAdapter requires a bot token")
        self._token = bot_token
        self._client = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._webhook_secret = webhook_secret
        self._bot_user_id = bot_user_id
        self._sleep = sleep
        self._last_update_ids: list = []

    # -- helpers ----------------------------------------------------------

    def _url(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self._token}/{method}"

    def _post(
        self, method: str, payload: dict | None = None, *, params: dict | None = None
    ) -> dict:
        """POST one Bot API call.

        ``payload`` goes in the JSON body; ``params`` goes in the
        query string (``getUpdates`` takes its offset/timeout as
        query parameters, the conventional call shape).
        """
        kwargs: dict = {}
        if payload is not None:
            kwargs["json"] = payload
        if params is not None:
            kwargs["params"] = params
        resp = self._client.post(self._url(method), **kwargs)
        data = resp.json()
        if resp.status_code != 200 or not data.get("ok"):
            raise DeliveryError(
                f"telegram {method} failed: HTTP {resp.status_code}: "
                f"{data.get('description', 'unknown error')}"
            )
        return data["result"]

    # -- inbound ----------------------------------------------------------

    def parse_update(self, payload: dict) -> list[MessageEvent]:
        """Translate one Telegram ``Update`` into zero or one event.

        Non-message updates (edited messages, callbacks, channel
        posts) produce no event. Text-less messages (photos, stickers)
        are normalised with an empty text so the pipeline can still
        record and ignore them deterministically.
        """
        message = payload.get("message") or payload.get("edited_message")
        if not isinstance(message, dict):
            return []
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        sender_id = str(sender.get("id", ""))
        is_bot = bool(sender.get("is_bot"))
        if self._bot_user_id is not None and sender.get("id") == self._bot_user_id:
            is_bot = True
        text = str(message.get("text") or message.get("caption") or "")
        # A mention entity naming the bot, or "@puspa" in the text,
        # marks the message as addressed to Puspa (group routing, B6).
        mentions_bot = mentions_puspa(text)
        if not mentions_bot:
            for entity in message.get("entities") or []:
                if not isinstance(entity, dict) or entity.get("type") != "mention":
                    continue
                start, length = entity.get("offset", 0), entity.get("length", 0)
                if text[start : start + length].lower() == "@puspa":
                    mentions_bot = True
                    break
        return [
            MessageEvent(
                platform="telegram",
                chat_id=str(chat.get("id", "")),
                sender_id=sender_id,
                text=text,
                message_id=str(message.get("message_id", payload.get("update_id", ""))),
                timestamp=_parse_timestamp(message.get("date")),
                is_bot=is_bot,
                is_group=str(chat.get("type", "private")) != "private",
                mentions_bot=mentions_bot,
                raw=payload,
            )
        ]

    def poll_once(self, *, offset: int | None = None, timeout: int = 30) -> list[MessageEvent]:
        """One ``getUpdates`` long-poll batch.

        Returns the normalised events; the caller advances the
        offset past the highest ``update_id`` seen (see
        :meth:`next_offset`). A failed poll raises
        :class:`DeliveryError` — the gateway logs it and polls again.
        """
        params: dict = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        result = self._post("getUpdates", params=params)
        events: list[MessageEvent] = []
        for update in result:
            events.extend(self.parse_update(update))
        self._last_update_ids = [u.get("update_id") for u in result if "update_id" in u]
        return events

    @staticmethod
    def next_offset(update_ids: list[int | None], current: int | None) -> int | None:
        """The offset to store after a poll batch (max update_id + 1)."""
        known = [u for u in update_ids if isinstance(u, int)]
        if not known:
            return current
        candidate = max(known) + 1
        return candidate if current is None or candidate > current else current

    def verify_webhook(self, request) -> bool:
        """Check the ``X-Telegram-Bot-Api-Secret-Token`` header.

        When no secret is configured, any request passes (the
        webhook URL itself is the secret then — configure one in
        production). ``request`` is a dict with a ``headers`` mapping.
        """
        if not self._webhook_secret:
            return True
        headers = request.get("headers", {}) if isinstance(request, dict) else {}
        token = headers.get("X-Telegram-Bot-Api-Secret-Token") or headers.get(
            "x-telegram-bot-api-secret-token"
        )
        return token == self._webhook_secret

    # -- outbound ---------------------------------------------------------

    def send_text(self, chat_id: str, text: str) -> dict:
        """Send one message via ``sendMessage`` (bounded retry + backoff).

        Returns the Telegram ``result`` object (the sent message).
        Raises :class:`DeliveryError` after all attempts fail.
        """
        last_error: Exception | None = None
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            try:
                return self._post(
                    "sendMessage",
                    {"chat_id": chat_id, "text": text},
                )
            except (DeliveryError, httpx.HTTPError) as exc:
                last_error = exc
                if attempt < MAX_SEND_ATTEMPTS:
                    self._sleep(SEND_BACKOFF_SECONDS[attempt - 1])
        raise DeliveryError(
            f"telegram sendMessage to {chat_id} failed after "
            f"{MAX_SEND_ATTEMPTS} attempts: {last_error}"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
