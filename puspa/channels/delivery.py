"""Outbound delivery through an adapter, recorded in the ledger (G14).

:class:`DeliveryService` is the only way the gateway sends: it
calls ``adapter.send_text``, records the attempt in the delivery
ledger (``pending`` -> ``sent`` / ``failed`` / ``retrying``), and
raises :class:`DeliveryError` when the adapter gives up. The
adapter itself owns the bounded retry + backoff; this service
owns the record.

A ledger failure must never lose a send: when the ledger write
fails, the send still happens and the error is attached to the
returned record as best-effort (the caller can log it). The
ledger interface is the union of :class:`InMemoryDeliveryLedger`
and :class:`PostgresDeliveryLedger`.
"""

from __future__ import annotations

import contextlib

from .base import ChannelAdapter
from .errors import DeliveryError
from .ledger import DeliveryRecord, new_delivery_id

__all__ = ["DeliveryService"]


class DeliveryService:
    """Send through an adapter and record the outcome in the ledger."""

    def __init__(self, ledger) -> None:
        self._ledger = ledger

    def send(
        self,
        adapter: ChannelAdapter,
        *,
        chat_id: str,
        text: str,
        reply_to_dedup_key: str | None = None,
    ) -> DeliveryRecord:
        """Send one reply and record it.

        ``reply_to_dedup_key`` is the inbound message's dedup key
        when this send answers one; the outbound record's own
        dedup key is ``platform:chat:outbound:<platform message id>``
        once the platform answers, and the pending record uses the
        reply target as its key so a duplicate send is visible.
        Returns the final ledger record.
        """
        dedup_key = reply_to_dedup_key or f"{adapter.platform}:{chat_id}:outbound"
        record = self._safe_record(
            platform=adapter.platform,
            chat_id=chat_id,
            direction="outbound",
            status="pending",
            dedup_key=dedup_key,
        )
        try:
            response = adapter.send_text(chat_id, text)
        except DeliveryError as exc:
            self._safe_update(record, status="failed", error=str(exc))
            raise
        platform_message_id = ""
        if isinstance(response, dict):
            result = response.get("result") or {}
            platform_message_id = str(result.get("message_id", ""))
            messages = response.get("messages") or []
            if messages and isinstance(messages[0], dict):
                platform_message_id = str(messages[0].get("id", ""))
        if record is not None and platform_message_id:
            # Re-key the record to the platform's own message id so
            # a later dedup lookup finds this exact send.
            record.dedup_key = f"{adapter.platform}:{chat_id}:{platform_message_id}"
        self._safe_update(record, status="sent")
        return record

    # -- ledger helpers (best-effort: never lose a send) ------------------

    def _safe_record(self, **kwargs) -> DeliveryRecord:
        """Record in the ledger; fall back to an in-memory record.

        A ledger failure must never lose a send, so when the ledger
        write raises, the caller still gets a usable record (marked
        with the failure in its error field).
        """
        try:
            return self._ledger.record(**kwargs)
        except Exception as exc:  # ledger must never drop a send
            return DeliveryRecord(
                id=new_delivery_id(),
                platform=kwargs.get("platform", ""),
                chat_id=kwargs.get("chat_id", ""),
                direction=kwargs.get("direction", "outbound"),
                status=kwargs.get("status", "pending"),
                dedup_key=kwargs.get("dedup_key", ""),
                attempts=kwargs.get("attempts", 1),
                error=f"ledger unavailable: {exc}",
            )

    def _safe_update(self, record, **kwargs) -> None:
        if record is None:
            return
        with contextlib.suppress(Exception):
            self._ledger.update(record.id, **kwargs)
