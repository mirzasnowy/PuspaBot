"""Billing webhooks: HMAC verification, idempotent handling, no crashes.

A webhook handler receives ``(payload_bytes, signature, secret)`` and
returns a result dict. This is a plain Python function, not a live
HTTP route: the HTTP surface belongs to another work item (G1/G13),
which calls :func:`handle_webhook` with the raw body it received.

Rules, in order:
1. The signature is verified with HMAC-SHA256 *before* anything is
   processed. A bad or missing signature is rejected outright.
2. A duplicate delivery (same idempotency key) returns the previous
   result without processing the event twice.
3. An unknown event type is recorded and ignored, never a crash.
4. A malformed payload is recorded and rejected, never a crash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .providers import verify_hmac_signature

#: Event types this handler knows how to process. Anything else is
#: recorded as unknown and ignored.
KNOWN_EVENTS = frozenset({
    "checkout.completed",
    "checkout.failed",
    "invoice.paid",
    "invoice.failed",
    "subscription.created",
    "subscription.updated",
    "subscription.cancelled",
    "credits.granted",
    "credits.revoked",
    "refund.issued",
})


@dataclass
class WebhookResult:
    """What happened to one webhook delivery."""

    status: str  # "processed" | "duplicate" | "ignored" | "rejected"
    event_type: str | None = None
    event_id: str | None = None
    action: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass
class MemoryIdempotencyStore:
    """In-memory record of processed webhook deliveries.

    Maps idempotency key -> the result that was returned. A second
    delivery with the same key returns the stored result. This is the
    store used in tests; a Postgres-backed store can replace it with
    the same two methods.
    """

    processed: dict[str, WebhookResult] = field(default_factory=dict)
    unknown_events: list[dict] = field(default_factory=list)

    def already_processed(self, key: str) -> WebhookResult | None:
        return self.processed.get(key)

    def record(self, key: str, result: WebhookResult) -> None:
        self.processed[key] = result


def _event_idempotency_key(event: dict) -> str:
    """Stable idempotency key for one event.

    Prefer the provider's event id; fall back to a hash of the
    canonical event body so the same payload always maps to the same
    key even without an id.
    """
    event_id = event.get("event_id") or event.get("id")
    if event_id:
        return f"evt:{event_id}"
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
    return "evt:hash:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def handle_webhook(payload: bytes, *, signature: str, secret: str,
                   store: MemoryIdempotencyStore | None = None,
                   on_event=None) -> WebhookResult:
    """Handle one webhook delivery.

    ``on_event`` is an optional callback ``(event_type, event) -> action``
    invoked for known events after verification and dedup; it returns a
    short string describing what the application did (for example
    ``"credits_granted:20"``). When ``on_event`` is None, known events
    are acknowledged without side effects.
    """
    store = store or MemoryIdempotencyStore()
    if not verify_hmac_signature(payload=payload, signature=signature,
                                 secret=secret):
        return WebhookResult(status="rejected", reason="bad_signature")
    try:
        event = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return WebhookResult(status="rejected", reason="malformed_payload")
    if not isinstance(event, dict):
        return WebhookResult(status="rejected", reason="malformed_payload")

    event_type = event.get("type") or event.get("event_type")
    key = _event_idempotency_key(event)
    previous = store.already_processed(key)
    if previous is not None:
        return WebhookResult(
            status="duplicate", event_type=previous.event_type,
            event_id=previous.event_id, action=previous.action,
            reason="already_processed",
        )
    if event_type not in KNOWN_EVENTS:
        store.unknown_events.append(
            {"event_type": event_type, "event_id": event.get("event_id")})
        result = WebhookResult(
            status="ignored", event_type=event_type,
            event_id=event.get("event_id"), reason="unknown_event")
        store.record(key, result)
        return result

    action = None
    if on_event is not None:
        action = on_event(event_type, event)
    result = WebhookResult(
        status="processed", event_type=event_type,
        event_id=event.get("event_id"), action=action)
    store.record(key, result)
    return result
