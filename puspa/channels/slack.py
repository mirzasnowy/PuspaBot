"""Slack adapter (AST-04).

Raw Slack Web API over HTTPS with the already-pinned
``httpx==0.28.1`` — no new dependency (slack-sdk is not used;
the Web API is plain HTTPS and needs no library). Two inbound
modes, one processing path:

- **REST polling** — :meth:`SlackAdapter.poll_once` calls
  ``conversations.history`` per configured channel with
  ``oldest`` set past the last timestamp seen there, so a
  restart resumes where polling stopped instead of reprocessing
  the backlog. Slack has no long-poll HTTP endpoint for
  messages; polling is the local-transport inbound mode.
- **Events API** — :meth:`SlackAdapter.parse_update` accepts one
  Events API envelope (``{"event": {...}}``) or a bare event
  object; :meth:`SlackAdapter.verify_webhook` checks the
  HMAC-SHA256 signing-secret signature (``X-Slack-Signature`` +
  ``X-Slack-Request-Timestamp``) when a signing secret is
  configured. The URL-verification handshake is answered by
  :meth:`SlackAdapter.verify_url_challenge`.

Sending uses ``chat.postMessage`` with the same bounded retry +
backoff as Telegram. The HTTP client is injectable: tests pass
an ``httpx.Client`` built on ``MockTransport`` and never touch
``slack.com``.

Bot-loop guard: an event whose ``bot_id`` is set, whose
``subtype`` is ``bot_message``, or whose user id equals the
bot's own id (when known) is normalised with ``is_bot=True``
and the pipeline refuses it. Public and private channels
(channel ids starting with ``C`` or ``G``) are normalised with
``is_group=True``; they are answered only when a room is linked
to the channel (B6, ``puspa/rooms/routing.py``) — unlinked
channels are still dropped by the pipeline. Direct messages
(channel ids starting with ``D``) are private.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from .base import ChannelAdapter
from .errors import DeliveryError
from .events import MessageEvent, mentions_puspa

__all__ = ["SLACK_API_BASE", "SlackAdapter"]

SLACK_API_BASE = "https://slack.com/api"

#: Bounded retries for message sends: 3 attempts, 1s / 2s backoff.
MAX_SEND_ATTEMPTS = 3
SEND_BACKOFF_SECONDS = (1.0, 2.0)

#: Messages fetched per channel per poll batch.
POLL_LIMIT = 50

#: A Slack request older than this is rejected as a replay (seconds).
SIGNATURE_MAX_AGE_SECONDS = 300


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


class SlackAdapter(ChannelAdapter):
    """Slack Web API adapter (raw HTTPS, httpx only)."""

    platform = "slack"

    def __init__(
        self,
        bot_token: str,
        *,
        channel_ids: list[str] | tuple[str, ...] | None = None,
        signing_secret: str | None = None,
        bot_user_id: str | None = None,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bot_token:
            raise ValueError("SlackAdapter requires a bot token")
        self._token = bot_token
        self._channel_ids = list(channel_ids or [])
        self._signing_secret = signing_secret
        self._bot_user_id = bot_user_id
        self._client = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._sleep = sleep
        self._last_ts: dict[str, str] = {}

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _check_response(self, resp: httpx.Response, action: str) -> dict:
        """Raise a clear DeliveryError for a failed Slack call."""
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 200 and data.get("ok"):
            return data
        error = str(data.get("error", f"HTTP {resp.status_code}"))
        if error == "invalid_auth":
            raise DeliveryError(
                f"slack {action} failed: invalid_auth — the bot token is invalid "
                "or revoked; set a valid SLACK_BOT_TOKEN"
            )
        raise DeliveryError(f"slack {action} failed: {error}")

    def check_token(self) -> dict:
        """Prove the bot token works (``auth.test``).

        Returns the ``auth.test`` response (bot id, team, user).
        Raises :class:`DeliveryError` with a clear message when
        the token is missing or invalid. Call this once at
        startup; the adapter never calls the network at
        construction.
        """
        resp = self._client.post(f"{SLACK_API_BASE}/auth.test", headers=self._headers())
        return self._check_response(resp, "check token")

    # -- inbound ----------------------------------------------------------

    def verify_url_challenge(self, payload: dict) -> str | None:
        """Answer the Events API URL-verification handshake.

        Returns the ``challenge`` string when the payload is a
        ``url_verification`` event; otherwise returns None.
        """
        if payload.get("type") != "url_verification":
            return None
        challenge = payload.get("challenge")
        return str(challenge) if challenge is not None else None

    def verify_signature(
        self, raw_body: bytes, signature_header: str | None, timestamp: str | None
    ) -> bool:
        """Check the ``X-Slack-Signature`` HMAC-SHA256.

        When no signing secret is configured, verification is
        skipped and any body passes (configure the secret in
        production). Requests older than five minutes are
        rejected as replays.
        """
        if not self._signing_secret:
            return True
        if not signature_header or not timestamp:
            return False
        try:
            age = abs(time.time() - float(timestamp))
        except ValueError:
            return False
        if age > SIGNATURE_MAX_AGE_SECONDS:
            return False
        base = f"v0:{timestamp}:".encode() + raw_body
        expected = "v0=" + hmac.new(self._signing_secret.encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    def verify_webhook(self, request) -> bool:
        """Authenticate an inbound Events API POST (signature check).

        ``request`` is a dict with ``body`` (raw bytes) and
        ``headers``. Returns False when the request carries no
        signature or the signature does not verify.
        """
        if not isinstance(request, dict):
            return False
        headers = request.get("headers", {})
        signature = headers.get("X-Slack-Signature") or headers.get("x-slack-signature")
        timestamp = headers.get("X-Slack-Request-Timestamp") or headers.get(
            "x-slack-request-timestamp"
        )
        return self.verify_signature(request.get("body", b""), signature, timestamp)

    def parse_update(self, payload: dict) -> list[MessageEvent]:
        """Translate one Events API envelope into zero or one event.

        Accepts an envelope (``{"event": {...}}``) or a bare event
        object. Only ``type == "message"`` events with text
        produce an event; URL verification, message edits, and
        other subtypes produce none. A message sent by a bot is
        normalised with ``is_bot=True``.
        """
        event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
        if not isinstance(event, dict) or event.get("type") != "message":
            return []
        if event.get("subtype") == "message_changed":
            return []
        channel = str(event.get("channel", ""))
        user_id = str(event.get("user", ""))
        is_bot = bool(event.get("bot_id")) or event.get("subtype") == "bot_message"
        if self._bot_user_id is not None and user_id == self._bot_user_id:
            is_bot = True
        text = str(event.get("text") or "")
        mentions_bot = mentions_puspa(text)
        if self._bot_user_id is not None and f"<@{self._bot_user_id}>" in text:
            mentions_bot = True
        return [
            MessageEvent(
                platform="slack",
                chat_id=channel,
                sender_id=user_id,
                text=text,
                message_id=str(event.get("ts", "")),
                timestamp=_parse_timestamp(event.get("ts")),
                is_bot=is_bot,
                is_group=channel.startswith(("C", "G")),
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

        Fetches ``conversations.history`` per channel with
        ``oldest`` past the last timestamp seen there (Slack
        returns newest first; events come back oldest first).
        ``channel_ids`` overrides the ids given at construction.
        ``offset`` and ``timeout`` are accepted for the shared
        polling shape and ignored: Slack paging is by timestamp,
        not by offset, and REST polling has no long-poll wait.
        A failed poll raises :class:`DeliveryError`.
        """
        ids = list(channel_ids if channel_ids is not None else self._channel_ids)
        if not ids:
            raise DeliveryError(
                "slack poll needs at least one channel id: set SLACK_CHANNEL_IDS "
                "or pass channel_ids"
            )
        events: list[MessageEvent] = []
        for channel_id in ids:
            params: dict = {"channel": channel_id, "limit": POLL_LIMIT}
            last_ts = self._last_ts.get(channel_id)
            if last_ts is not None:
                params["oldest"] = last_ts
            resp = self._client.get(
                f"{SLACK_API_BASE}/conversations.history",
                params=params,
                headers=self._headers(),
            )
            data = self._check_response(resp, f"poll channel {channel_id}")
            messages = list(data.get("messages", []) or [])
            for message in reversed(messages):
                if last_ts is not None and str(message.get("ts", "")) <= last_ts:
                    continue
                event = dict(message)
                event["channel"] = channel_id
                events.extend(self.parse_update(event))
            if messages:
                newest = max(str(m.get("ts", "")) for m in messages)
                if newest > self._last_ts.get(channel_id, ""):
                    self._last_ts[channel_id] = newest
        return events

    # -- outbound ---------------------------------------------------------

    def send_text(self, chat_id: str, text: str) -> dict:
        """Send one message via ``chat.postMessage`` (bounded retry).

        Returns the Slack response dict (``ts`` is the platform
        message id). Raises :class:`DeliveryError` after all
        attempts fail.
        """
        payload = {"channel": chat_id, "text": text}
        last_error: Exception | None = None
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            try:
                resp = self._client.post(
                    f"{SLACK_API_BASE}/chat.postMessage",
                    json=payload,
                    headers=self._headers(),
                )
                return self._check_response(resp, f"send to channel {chat_id}")
            except (DeliveryError, httpx.HTTPError) as exc:
                last_error = exc
                if attempt < MAX_SEND_ATTEMPTS:
                    self._sleep(SEND_BACKOFF_SECONDS[attempt - 1])
        raise DeliveryError(
            f"slack send to channel {chat_id} failed after "
            f"{MAX_SEND_ATTEMPTS} attempts: {last_error}"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
