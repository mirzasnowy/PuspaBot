"""Discord adapter (AST-04).

Raw Discord Bot API over HTTPS with the already-pinned
``httpx==0.28.1`` — no new dependency (discord.py is not used;
the Bot API is plain HTTPS and needs no library). Two inbound
modes, one processing path:

- **REST polling** — :meth:`DiscordAdapter.poll_once` calls
  ``GET /channels/{channel_id}/messages`` with ``after`` set to
  the last message id seen in that channel, so a restart resumes
  where polling stopped instead of reprocessing the backlog.
  Discord has no long-poll HTTP endpoint for messages; the
  gateway websocket is deliberately NOT used here, because this
  is a local transport that must run on plain HTTPS with no extra
  dependency. Polling is the local-transport inbound mode.
- **Webhook / interactions** — :meth:`DiscordAdapter.parse_update`
  accepts one gateway ``MESSAGE_CREATE`` dispatch (or a bare
  message object, the shape a gateway forwarder hands over);
  :meth:`DiscordAdapter.verify_webhook` checks the Ed25519
  interaction signature (``X-Signature-Ed25519`` +
  ``X-Signature-Timestamp``) when a public key is configured,
  using the already-pinned ``cryptography`` package.

Sending uses ``POST /channels/{channel_id}/messages`` with the
same bounded retry + backoff as Telegram. The HTTP client is
injectable: tests pass an ``httpx.Client`` built on
``MockTransport`` and never touch ``discord.com``.

Bot-loop guard: a message whose ``author.bot`` is true, or whose
author id equals the bot's own id (when known), is normalised
with ``is_bot=True`` and the pipeline refuses it. Server
channels (``guild_id`` present) are normalised with
``is_group=True``; they are answered only when a room is linked
to the channel (B6, ``puspa/rooms/routing.py``) — unlinked
servers are still dropped by the pipeline.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .base import ChannelAdapter
from .errors import DeliveryError
from .events import MessageEvent, mentions_puspa

__all__ = ["DISCORD_API_BASE", "DiscordAdapter"]

DISCORD_API_BASE = "https://discord.com/api/v10"

#: Bounded retries for message sends: 3 attempts, 1s / 2s backoff.
MAX_SEND_ATTEMPTS = 3
SEND_BACKOFF_SECONDS = (1.0, 2.0)

#: Messages fetched per channel per poll batch.
POLL_LIMIT = 50


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


class DiscordAdapter(ChannelAdapter):
    """Discord Bot API adapter (raw HTTPS, httpx only)."""

    platform = "discord"

    def __init__(
        self,
        bot_token: str,
        *,
        channel_ids: list[str] | tuple[str, ...] | None = None,
        public_key: str | None = None,
        bot_user_id: str | None = None,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bot_token:
            raise ValueError("DiscordAdapter requires a bot token")
        self._token = bot_token
        self._channel_ids = list(channel_ids or [])
        self._public_key = public_key
        self._bot_user_id = bot_user_id
        self._client = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._sleep = sleep
        self._last_message_ids: dict[str, str] = {}

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bot {self._token}", "Content-Type": "application/json"}

    def _check_response(self, resp: httpx.Response, action: str) -> Any:
        """Raise a clear DeliveryError for a failed Discord call."""
        if resp.status_code in (200, 201):
            return resp.json()
        try:
            detail = resp.json().get("message", "")
        except ValueError:
            detail = resp.text[:200]
        if resp.status_code == 401:
            raise DeliveryError(
                f"discord {action} failed: HTTP 401 — the bot token is invalid "
                f"or revoked; set a valid DISCORD_BOT_TOKEN ({detail})"
            )
        if resp.status_code == 403:
            raise DeliveryError(
                f"discord {action} failed: HTTP 403 — the bot lacks access "
                f"to this channel ({detail})"
            )
        raise DeliveryError(f"discord {action} failed: HTTP {resp.status_code}: {detail}")

    def check_token(self) -> dict:
        """Prove the bot token works (``GET /users/@me``).

        Returns the bot's user object. Raises :class:`DeliveryError`
        with a clear message when the token is missing or invalid.
        Call this once at startup; the adapter never calls the
        network at construction.
        """
        resp = self._client.get(f"{DISCORD_API_BASE}/users/@me", headers=self._headers())
        return self._check_response(resp, "check token")

    # -- inbound ----------------------------------------------------------

    def parse_update(self, payload: dict) -> list[MessageEvent]:
        """Translate one Discord message into zero or one event.

        Accepts a gateway ``MESSAGE_CREATE`` dispatch
        (``{"t": "MESSAGE_CREATE", "d": {...}}``) or a bare message
        object. Anything else (other dispatch types, partial
        payloads) produces no event. Text-less messages are
        normalised with an empty text so the pipeline can still
        record and ignore them deterministically.
        """
        message = payload.get("d") if payload.get("t") == "MESSAGE_CREATE" else payload
        if not isinstance(message, dict) or "id" not in message or "channel_id" not in message:
            return []
        author = message.get("author") or {}
        author_id = str(author.get("id", ""))
        is_bot = bool(author.get("bot"))
        if self._bot_user_id is not None and author_id == self._bot_user_id:
            is_bot = True
        mentions = message.get("mentions") or []
        mentions_bot = any(
            isinstance(m, dict) and str(m.get("id", "")) == self._bot_user_id
            for m in mentions
            if self._bot_user_id is not None
        ) or mentions_puspa(str(message.get("content") or ""))
        return [
            MessageEvent(
                platform="discord",
                chat_id=str(message.get("channel_id", "")),
                sender_id=author_id,
                text=str(message.get("content") or ""),
                message_id=str(message.get("id", "")),
                timestamp=_parse_timestamp(message.get("timestamp")),
                is_bot=is_bot,
                is_group=bool(message.get("guild_id")),
                mentions_bot=mentions_bot,
                raw=payload,
            )
        ]

    def poll_once(
        self,
        *,
        channel_ids: list[str] | tuple[str, ...] | None = None,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[MessageEvent]:
        """One polling batch over the configured channels.

        Fetches ``GET /channels/{id}/messages`` per channel with
        ``after`` set to the last message id seen there (Discord
        returns newest first; events come back oldest first).
        ``channel_ids`` overrides the ids given at construction.
        ``offset`` and ``timeout`` are accepted for the shared
        polling shape and ignored: Discord paging is by message
        id, not by offset, and REST polling has no long-poll wait.
        A failed poll raises :class:`DeliveryError`.
        """
        ids = list(channel_ids if channel_ids is not None else self._channel_ids)
        if not ids:
            raise DeliveryError(
                "discord poll needs at least one channel id: set DISCORD_CHANNEL_IDS "
                "or pass channel_ids"
            )
        events: list[MessageEvent] = []
        for channel_id in ids:
            params: dict = {"limit": POLL_LIMIT}
            last_id = self._last_message_ids.get(channel_id)
            if last_id is not None:
                params["after"] = last_id
            resp = self._client.get(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                params=params,
                headers=self._headers(),
            )
            data = self._check_response(resp, f"poll channel {channel_id}")
            messages: list[dict] = list(data) if isinstance(data, list) else []
            for message in reversed(messages):
                events.extend(self.parse_update(message))
            if messages:
                self._last_message_ids[channel_id] = str(messages[0].get("id", ""))
        return events

    def verify_webhook(self, request) -> bool:
        """Check the Ed25519 interaction signature.

        ``request`` is a dict with ``body`` (raw bytes) and
        ``headers`` (``X-Signature-Ed25519`` +
        ``X-Signature-Timestamp``). When no public key is
        configured, any request passes (configure one in
        production). Returns False when the signature is missing
        or does not verify.
        """
        if not self._public_key:
            return True
        if not isinstance(request, dict):
            return False
        headers = request.get("headers", {})
        signature = headers.get("X-Signature-Ed25519") or headers.get("x-signature-ed25519")
        timestamp = headers.get("X-Signature-Timestamp") or headers.get("x-signature-timestamp")
        if not signature or not timestamp:
            return False
        try:
            public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(self._public_key))
            public_key.verify(
                bytes.fromhex(signature), timestamp.encode() + request.get("body", b"")
            )
        except (ValueError, InvalidSignature):
            return False
        return True

    # -- outbound ---------------------------------------------------------

    def send_text(self, chat_id: str, text: str) -> dict:
        """Send one message (bounded retry + backoff).

        Returns the Discord message object. Raises
        :class:`DeliveryError` after all attempts fail.
        """
        payload = {"content": text}
        last_error: Exception | None = None
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            try:
                resp = self._client.post(
                    f"{DISCORD_API_BASE}/channels/{chat_id}/messages",
                    json=payload,
                    headers=self._headers(),
                )
                return self._check_response(resp, f"send to channel {chat_id}")
            except (DeliveryError, httpx.HTTPError) as exc:
                last_error = exc
                if attempt < MAX_SEND_ATTEMPTS:
                    self._sleep(SEND_BACKOFF_SECONDS[attempt - 1])
        raise DeliveryError(
            f"discord send to channel {chat_id} failed after "
            f"{MAX_SEND_ATTEMPTS} attempts: {last_error}"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
