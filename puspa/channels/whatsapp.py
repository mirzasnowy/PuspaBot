"""WhatsApp adapter (G14).

Raw WhatsApp Cloud API over HTTPS with the already-pinned
``httpx==0.28.1`` — no new dependency. One inbound mode:

- **Webhook** — Meta calls the webhook with a JSON body. The
  GET verification handshake (``hub.mode=subscribe`` +
  ``hub.verify_token`` + ``hub.challenge``) is answered by
  :meth:`WhatsAppAdapter.verify_webhook_handshake`; POST bodies
  are authenticated with HMAC-SHA256 (``X-Hub-Signature-256``,
  app secret) by :meth:`WhatsAppAdapter.verify_signature`, and
  :meth:`WhatsAppAdapter.parse_update` turns the
  ``entry[].changes[].value.messages[]`` array into events.

Sending uses ``POST /{phone_number_id}/messages`` with the same
bounded retry + backoff as Telegram. The HTTP client is
injectable: tests pass an ``httpx.Client`` built on
``MockTransport`` and never touch ``graph.facebook.com``.

Bot-loop guard: WhatsApp has no ``is_bot`` flag on inbound
messages, so the guard here is the sender id — a message whose
``from`` equals the business phone number id (or the configured
bot number) is normalised with ``is_bot=True``. Group messages do
not exist on the Cloud API, so ``is_group`` is always False
(WhatsApp groups are a postponed surface, plan section 12.4);
``mentions_bot`` is still set from the text, so the same routing
rule applies if a group-capable WhatsApp transport ever arrives.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from .base import ChannelAdapter
from .errors import DeliveryError, WebhookVerificationError
from .events import MessageEvent, mentions_puspa

__all__ = ["WHATSAPP_API_BASE", "WhatsAppAdapter"]

WHATSAPP_API_BASE = "https://graph.facebook.com"
WHATSAPP_API_VERSION = "v21.0"

#: Bounded retries for message sends: 3 attempts, 1s / 2s backoff.
MAX_SEND_ATTEMPTS = 3
SEND_BACKOFF_SECONDS = (1.0, 2.0)


def _parse_timestamp(epoch: str | int | None) -> datetime:
    if epoch is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(float(epoch), tz=UTC)


class WhatsAppAdapter(ChannelAdapter):
    """WhatsApp Cloud API adapter (raw HTTPS, httpx only)."""

    platform = "whatsapp"

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        *,
        verify_token: str | None = None,
        app_secret: str | None = None,
        bot_phone_number: str | None = None,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not access_token or not phone_number_id:
            raise ValueError("WhatsAppAdapter requires an access token and a phone number id")
        self._token = access_token
        self._phone_number_id = phone_number_id
        self._verify_token = verify_token
        self._app_secret = app_secret
        self._bot_phone_number = bot_phone_number
        self._client = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._sleep = sleep

    # -- helpers ----------------------------------------------------------

    def _url(self) -> str:
        return f"{WHATSAPP_API_BASE}/{WHATSAPP_API_VERSION}/{self._phone_number_id}/messages"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    # -- inbound ----------------------------------------------------------

    def verify_webhook_handshake(self, query: dict) -> str | None:
        """Answer the Meta webhook verification GET.

        Returns the ``hub.challenge`` string when ``hub.mode`` is
        ``subscribe`` and ``hub.verify_token`` matches the configured
        token; otherwise returns None (the caller answers 403).
        """
        if query.get("hub.mode") != "subscribe":
            return None
        if self._verify_token is None:
            return None
        if query.get("hub.verify_token") != self._verify_token:
            return None
        challenge = query.get("hub.challenge")
        return str(challenge) if challenge is not None else None

    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        """Check the ``X-Hub-Signature-256`` HMAC-SHA256 of a POST body.

        When no app secret is configured, verification is skipped
        and any body passes (configure the secret in production).
        """
        if not self._app_secret:
            return True
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(self._app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header[len("sha256=") :])

    def verify_webhook(self, request) -> bool:
        """Authenticate an inbound webhook POST (signature check).

        ``request`` is a dict with ``body`` (raw bytes) and
        ``headers``. Raises :class:`WebhookVerificationError` when
        the signature is present but wrong; returns False when the
        request carries no signature at all and a secret is set.
        """
        if not isinstance(request, dict):
            return False
        if not self._app_secret:
            return True
        headers = request.get("headers", {})
        signature = headers.get("X-Hub-Signature-256") or headers.get("x-hub-signature-256")
        if signature is None:
            return False
        if not self.verify_signature(request.get("body", b""), signature):
            raise WebhookVerificationError("whatsapp webhook signature mismatch")
        return True

    def parse_update(self, payload: dict) -> list[MessageEvent]:
        """Translate one webhook body into events (one per message).

        Status callbacks (``statuses``) and non-text messages
        produce no event; text messages produce one event each.
        A message from the business number itself is marked as a
        bot message so the loop guard drops it.
        """
        events: list[MessageEvent] = []
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value", {}) or {}
                for message in value.get("messages", []) or []:
                    if message.get("type") != "text":
                        continue
                    sender = str(message.get("from", ""))
                    text = ((message.get("text") or {}).get("body")) or ""
                    events.append(
                        MessageEvent(
                            platform="whatsapp",
                            chat_id=sender,
                            sender_id=sender,
                            text=str(text),
                            message_id=str(message.get("id", "")),
                            timestamp=_parse_timestamp(message.get("timestamp")),
                            is_bot=(
                                self._bot_phone_number is not None
                                and sender == self._bot_phone_number
                            ),
                            is_group=False,
                            mentions_bot=mentions_puspa(text),
                            raw={"entry": entry, "message": message},
                        )
                    )
        return events

    # -- outbound ---------------------------------------------------------

    def send_text(self, chat_id: str, text: str) -> dict:
        """Send one text message (bounded retry + backoff).

        Returns the Cloud API response dict (``messages[0].id`` is
        the platform message id). Raises :class:`DeliveryError`
        after all attempts fail.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": chat_id,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        last_error: Exception | None = None
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            try:
                resp = self._client.post(self._url(), json=payload, headers=self._headers())
                data = resp.json()
                if resp.status_code not in (200, 201) or "messages" not in data:
                    raise DeliveryError(f"whatsapp send failed: HTTP {resp.status_code}: {data}")
                return data
            except (DeliveryError, httpx.HTTPError) as exc:
                last_error = exc
                if attempt < MAX_SEND_ATTEMPTS:
                    self._sleep(SEND_BACKOFF_SECONDS[attempt - 1])
        raise DeliveryError(
            f"whatsapp send to {chat_id} failed after {MAX_SEND_ATTEMPTS} attempts: {last_error}"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
