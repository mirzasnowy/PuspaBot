"""Tests: billing webhooks — HMAC verification before processing,
duplicate delivery via idempotency key, unknown events recorded
without crashing, malformed payloads rejected."""

import json

from puspa.billing.providers import hmac_sha256_hex
from puspa.billing.webhook import (
    MemoryIdempotencyStore,
    handle_webhook,
)

SECRET = "test-webhook-secret"


def _signed(event: dict) -> tuple[bytes, str]:
    payload = json.dumps(event).encode("utf-8")
    return payload, "sha256=" + hmac_sha256_hex(SECRET, payload)


def test_known_event_processed_once():
    store = MemoryIdempotencyStore()
    payload, signature = _signed(
        {"event_id": "evt-1", "type": "invoice.paid", "amount_idr": 99_000})
    result = handle_webhook(payload, signature=signature, secret=SECRET,
                            store=store)
    assert result.status == "processed"
    assert result.event_type == "invoice.paid"
    assert result.event_id == "evt-1"


def test_bad_signature_rejected_before_processing():
    calls = []

    def on_event(event_type, event):
        calls.append(event_type)
        return "did_work"

    store = MemoryIdempotencyStore()
    payload, _ = _signed({"event_id": "evt-2", "type": "invoice.paid"})
    result = handle_webhook(payload, signature="sha256=deadbeef",
                            secret=SECRET, store=store, on_event=on_event)
    assert result.status == "rejected"
    assert result.reason == "bad_signature"
    assert calls == []


def test_duplicate_delivery_returns_previous_result_without_reprocessing():
    calls = []

    def on_event(event_type, event):
        calls.append(event_type)
        return "credits_granted:100"

    store = MemoryIdempotencyStore()
    event = {"event_id": "evt-3", "type": "credits.granted", "credits": 100}
    payload, signature = _signed(event)
    first = handle_webhook(payload, signature=signature, secret=SECRET,
                           store=store, on_event=on_event)
    second = handle_webhook(payload, signature=signature, secret=SECRET,
                            store=store, on_event=on_event)
    assert first.status == "processed"
    assert first.action == "credits_granted:100"
    assert second.status == "duplicate"
    assert second.reason == "already_processed"
    assert second.action == "credits_granted:100"
    assert calls == ["credits.granted"]  # processed exactly once


def test_duplicate_without_event_id_uses_payload_hash():
    store = MemoryIdempotencyStore()
    payload, signature = _signed({"type": "invoice.paid", "amount_idr": 1})
    first = handle_webhook(payload, signature=signature, secret=SECRET,
                           store=store)
    second = handle_webhook(payload, signature=signature, secret=SECRET,
                            store=store)
    assert first.status == "processed"
    assert second.status == "duplicate"


def test_unknown_event_recorded_not_crash():
    store = MemoryIdempotencyStore()
    payload, signature = _signed(
        {"event_id": "evt-9", "type": "something.brand_new"})
    result = handle_webhook(payload, signature=signature, secret=SECRET,
                            store=store)
    assert result.status == "ignored"
    assert result.reason == "unknown_event"
    assert store.unknown_events == [
        {"event_type": "something.brand_new", "event_id": "evt-9"}]
    # A second delivery of the same unknown event is a duplicate.
    again = handle_webhook(payload, signature=signature, secret=SECRET,
                           store=store)
    assert again.status == "duplicate"


def test_malformed_payload_rejected():
    store = MemoryIdempotencyStore()
    payload = b"{not json"
    signature = "sha256=" + hmac_sha256_hex(SECRET, payload)
    result = handle_webhook(payload, signature=signature, secret=SECRET,
                            store=store)
    assert result.status == "rejected"
    assert result.reason == "malformed_payload"
    payload = json.dumps(["not", "a", "dict"]).encode("utf-8")
    signature = "sha256=" + hmac_sha256_hex(SECRET, payload)
    result = handle_webhook(payload, signature=signature, secret=SECRET,
                            store=store)
    assert result.status == "rejected"
    assert result.reason == "malformed_payload"


def test_each_known_event_type_accepted():
    from puspa.billing.webhook import KNOWN_EVENTS

    store = MemoryIdempotencyStore()
    for index, event_type in enumerate(sorted(KNOWN_EVENTS)):
        payload, signature = _signed(
            {"event_id": f"evt-known-{index}", "type": event_type})
        result = handle_webhook(payload, signature=signature, secret=SECRET,
                                store=store)
        assert result.status == "processed", event_type
