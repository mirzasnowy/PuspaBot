"""Channel adapter interface (G14).

Every platform adapter implements the same small surface, so the
inbound pipeline, the delivery service, and the gateway never see
platform specifics:

- ``parse_update(payload)`` — turn one raw platform payload into
  zero or more :class:`MessageEvent` objects (a webhook batch can
  carry several messages).
- ``send_text(chat_id, text)`` — deliver one reply through the
  platform. Adapters retry transient failures themselves, with a
  bounded attempt count and backoff; the delivery service records
  the outcome in the ledger.
- ``verify_webhook(request)`` — authenticate an inbound webhook
  request (token or signature, per platform). Polling adapters
  raise ``NotImplementedError`` for the modes they do not offer.

Adapters never decide authorisation and never call the agent:
they translate and transport only. The pipeline in
``puspa.channels.inbound`` owns the rest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .events import MessageEvent


class ChannelAdapter(ABC):
    """One platform's transport: parse inbound, send outbound."""

    platform: str = ""

    @abstractmethod
    def parse_update(self, payload: dict) -> list[MessageEvent]:
        """Translate one raw platform payload into normalised events."""

    @abstractmethod
    def send_text(self, chat_id: str, text: str) -> dict:
        """Send one text reply. Returns the platform's response dict.

        Raises :class:`puspa.channels.errors.DeliveryError` when
        every retry is exhausted.
        """

    def verify_webhook(self, request: Any) -> bool:
        """Authenticate an inbound webhook request.

        ``request`` is whatever the HTTP layer hands over (a dict
        with ``headers`` / ``query`` / ``body`` keys is enough for
        the adapters in this package). Returns True when the
        request is authentic. Adapters without webhook support
        return False.
        """
        return False

    def poll_once(self, *, offset: int | None = None, timeout: int = 30) -> list[MessageEvent]:
        """Fetch one batch of inbound events (polling adapters only)."""
        raise NotImplementedError(f"{self.platform} does not poll")
