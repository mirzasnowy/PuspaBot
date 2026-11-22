"""LINE adapter (AST-04).

Raw LINE Messaging API over HTTPS with the already-pinned
``httpx==0.28.1`` — no new dependency (line-bot-sdk is not
used; the Messaging API is plain HTTPS and needs no library).
One inbound mode:

- **Webhook** — LINE calls the webhook with a JSON body. POST
  bodies are authenticated with HMAC-SHA256
  (``X-Line-Signature``, channel secret) by
  :meth:`LineAdapter.verify_webhook`, and
  :meth:`LineAdapter.parse_update` turns the ``events`` array
  into one event per text message.

LINE has no polling endpoint for messages, so this adapter is
webhook-only: :meth:`poll_once` is not implemented and the base
class raises ``NotImplementedError`` for it, exactly like the
webhook-only WhatsApp adapter.

Sending has two shapes, both with the same bounded retry +
backoff as Telegram:

- :meth:`LineAdapter.send_text` — push message
  (``POST /v2/bot/message/push``) to a user, group, or room id.
  This is the uniform shape the pipeline uses for replies.
- :meth:`LineAdapter.reply_text` — reply message
  (``POST /v2/bot/message/reply``) with the one-use reply token
  from the inbound event, the free reply inside the reply
  window. Webhook handlers that still hold the token should
  prefer this; the token is stashed in the event's ``raw``
  payload.

The HTTP client is injectable: tests pass an ``httpx.Client``
built on ``MockTransport`` and never touch ``api.line.me``.

Bot-loop guard: LINE has no ``is_bot`` flag on inbound
messages, so the guard here is the sender id — a message whose
``source.userId`` equals the configured bot user id is
normalised with ``is_bot=True``. Group and room messages are
normalised with ``is_group=True``; they are answered only when
a room is linked to the group (B6, ``puspa/rooms/routing.py``)
— unlinked groups are still dropped by the pipeline.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from .base import ChannelAdapter
from .errors import DeliveryError
from .events import MessageEvent, mentions_puspa

__all__ = ["LINE_API_BASE", "LineAdapter"]

LINE_API_BASE = "https://api.line.me"

#: Bounded retries for message sends: 3 attempts, 1s / 2s backoff.
MAX_SEND_ATTEMPTS = 3
SEND_BACKOFF_SECONDS = (1.0, 2.0)


def _parse_timestamp(epoch_ms: int | float | None) -> datetime:
    if epoch_ms is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(float(epoch_ms) / 1000, tz=UTC)


class LineAdapter(ChannelAdapter):
    """LINE Messaging API adapter (raw HTTPS, httpx only)."""

    platform = "line"

    def __init__(
        self,
        channel_access_token: str,
        *,
        channel_secret: str | None = None,
        bot_user_id: str | None = None,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not channel_access_token:
            raise ValueError("LineAdapter requires a channel access token")
        self._token = channel_access_token
        self._channel_secret = channel_secret
        self._bot_user_id = bot_user_id
        self._client = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._sleep = sleep

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _check_response(self, resp: httpx.Response, action: str) -> dict:
        """Raise a clear DeliveryError for a failed LINE call."""
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return {}
        try:
            detail = resp.json().get("message", "")
        except ValueError:
            detail = resp.text[:200]
        if resp.status_code == 401:
            raise DeliveryError(
                f"line {action} failed: HTTP 401 — the channel access token is "
                "invalid or expired; set a valid LINE_CHANNEL_ACCESS_TOKEN "
                f"({detail})"
            )
        raise DeliveryError(f"line {action} failed: HTTP {resp.status_code}: {detail}")

    def check_token(self) -> dict:
        """Prove the channel access token works (message quota).

        Returns the quota response. Raises
        :class:`DeliveryError` with a clear message when the token
        is missing or invalid. Call this once at startup; the
        adapter never calls the network at construction.
        """
        resp = self._client.get(f"{LINE_API_BASE}/v2/bot/message/quota", headers=self._headers())
        return self._check_response(resp, "check token")

    # -- inbound ----------------------------------------------------------

    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        """Check the ``X-Line-Signature`` HMAC-SHA256 (base64).

        When no channel secret is configured, verification is
        skipped and any body passes (configure the secret in
        production).
        """
        if not self._channel_secret:
            return True
        if not signature_header:
            return False
        expected = base64.b64encode(
            hmac.new(self._channel_secret.encode(), raw_body, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(expected, signature_header)

    def verify_webhook(self, request) -> bool:
        """Authenticate an inbound webhook POST (signature check).

        ``request`` is a dict with ``body`` (raw bytes) and
        ``headers``. Returns False when the request carries no
        signature or the signature does not verify.
        """
        if not isinstance(request, dict):
            return False
        headers = request.get("headers", {})
        signature = headers.get("X-Line-Signature") or headers.get("x-line-signature")
        return self.verify_signature(request.get("body", b""), signature)

    def parse_update(self, payload: dict) -> list[MessageEvent]:
        """Translate one webhook body into events (one per message).

        Only ``type == "message"`` events with a text message
        produce an event; follow, unfollow, join, and non-text
        messages produce none. The chat id is the group or room
        id for group sources, else the sender's user id. The
        event's ``raw`` payload carries the reply token, so a
        webhook handler can answer with
        :meth:`reply_text` instead of a push.
        """
        events: list[MessageEvent] = []
        for event in payload.get("events", []) or []:
            if event.get("type") != "message":
                continue
            message = event.get("message", {}) or {}
            if message.get("type") != "text":
                continue
            source = event.get("source", {}) or {}
            source_type = str(source.get("type", "user"))
            sender_id = str(source.get("userId", ""))
            if source_type == "group":
                chat_id = str(source.get("groupId", sender_id))
            elif source_type == "room":
                chat_id = str(source.get("roomId", sender_id))
            else:
                chat_id = sender_id
            events.append(
                MessageEvent(
                    platform="line",
                    chat_id=chat_id,
                    sender_id=sender_id,
                    text=str(message.get("text") or ""),
                    message_id=str(event.get("webhookEventId") or message.get("id", "")),
                    timestamp=_parse_timestamp(event.get("timestamp")),
                    is_bot=(self._bot_user_id is not None and sender_id == self._bot_user_id),
                    is_group=source_type in ("group", "room"),
                    mentions_bot=mentions_puspa(str(message.get("text") or "")),
                    raw=event,
                )
            )
        return events

    # -- outbound ---------------------------------------------------------

    def _send_with_retry(self, url: str, payload: dict, action: str) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            try:
                resp = self._client.post(url, json=payload, headers=self._headers())
                return self._check_response(resp, action)
            except (DeliveryError, httpx.HTTPError) as exc:
                last_error = exc
                if attempt < MAX_SEND_ATTEMPTS:
                    self._sleep(SEND_BACKOFF_SECONDS[attempt - 1])
        raise DeliveryError(
            f"line {action} failed after {MAX_SEND_ATTEMPTS} attempts: {last_error}"
        )

    def send_text(self, chat_id: str, text: str) -> dict:
        """Send one push message (bounded retry + backoff).

        Returns the (usually empty) LINE response dict. Raises
        :class:`DeliveryError` after all attempts fail.
        """
        return self._send_with_retry(
            f"{LINE_API_BASE}/v2/bot/message/push",
            {"to": chat_id, "messages": [{"type": "text", "text": text}]},
            f"push to {chat_id}",
        )

    def reply_text(self, reply_token: str, text: str) -> dict:
        """Answer one inbound event with its reply token.

        The reply token is one-use and expires quickly; the
        caller takes it from the event's ``raw`` payload
        (``raw["replyToken"]``). Raises :class:`DeliveryError`
        after all attempts fail.
        """
        if not reply_token:
            raise ValueError("LineAdapter.reply_text requires a reply token")
        return self._send_with_retry(
            f"{LINE_API_BASE}/v2/bot/message/reply",
            {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
            "reply",
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
