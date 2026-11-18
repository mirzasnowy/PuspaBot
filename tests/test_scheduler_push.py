"""Tes penjadwal (§2.14) + Web Push VAPID."""

from datetime import UTC, datetime, timedelta

import httpx

from puspa.push.push import (
    create_vapid_key,
    decrypt_payload,
    encrypt_payload,
    list_subscriptions,
    save_subscription,
    send,
)
from puspa.scheduler.scheduler import (
    create_trigger,
    heartbeat,
    is_healthy,
    run_missed,
    schedule_backup_daily,
    tick,
)


def _sekarang():
    return datetime.now(UTC)


def test_tick_runs_trigger_once_idempotently(test_dsn, make_user):
    u = make_user()
    event = []

    def sink(event_type, content):
        return event.append({"type": event_type, "content": content})

    create_trigger(
        u["user_id"],
        "schedule",
        _sekarang() - timedelta(minutes=5),
        schedule="harian 07:00",
        dsn=test_dsn,
    )
    result = tick(sink=sink, dsn=test_dsn)
    assert len(result) == 1
    assert event[0]["type"] == "trigger_fired"
    # Putaran kedua: pemicu sudah geser +1 hari, tidak jalan lagi.
    assert tick(sink=sink, dsn=test_dsn) == []


def test_missed_trigger_runs_once_and_is_reported(test_dsn, make_user):
    u = make_user()
    event = []

    def sink(event_type, content):
        return event.append({"type": event_type, "content": content})

    create_trigger(u["user_id"], "schedule", _sekarang() - timedelta(hours=3), dsn=test_dsn)
    result = run_missed(sink=sink, dsn=test_dsn)
    assert len(result) == 1 and result[0]["missed"] is True
    assert event[0]["type"] == "trigger_missed"
    assert run_missed(sink=sink, dsn=test_dsn) == []


def test_scheduler_heartbeat_dead_for_more_than_2_minutes(test_dsn):
    assert is_healthy(dsn=test_dsn) is False  # belum pernah detak
    heartbeat(dsn=test_dsn)
    assert is_healthy(dsn=test_dsn) is True
    heartbeat(dsn=test_dsn, now=_sekarang() - timedelta(minutes=3))
    assert is_healthy(dsn=test_dsn) is False


def test_daily_backup_scheduled_as_trigger(test_dsn, make_user):
    u = make_user()
    trigger = schedule_backup_daily(u["user_id"], dsn=test_dsn)
    assert trigger.event_type == "backup_daily"
    assert trigger.next_trigger_at > _sekarang()


def test_vapid_encryption_roundtrip():
    create_vapid_key()
    # Pasangan kunci penerima (simulasi browser).
    from cryptography.hazmat.primitives import serialization

    recipient_private = __import__(
        "cryptography.hazmat.primitives.asymmetric.ec", fromlist=["generate_private_key"]
    ).generate_private_key(
        __import__(
            "cryptography.hazmat.primitives.asymmetric.ec", fromlist=["SECP256R1"]
        ).SECP256R1()
    )
    import base64

    d = recipient_private.private_numbers().private_value.to_bytes(32, "big")
    private_b64 = base64.urlsafe_b64encode(d).rstrip(b"=").decode()
    public = recipient_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public_b64 = base64.urlsafe_b64encode(public).rstrip(b"=").decode()
    auth_b64 = base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode()
    body, server_key, salt = encrypt_payload(
        b"Tagihan PLN jatuh tempo besok.", p256dh_recipient_b64=public_b64, auth_b64=auth_b64
    )
    plaintext = decrypt_payload(
        body,
        salt_b64=salt,
        server_key_b64=server_key,
        recipient_private_b64=private_b64,
        auth_b64=auth_b64,
    )
    assert plaintext == b"Tagihan PLN jatuh tempo besok."


def test_send_push_past_mock():
    def handler(request):
        assert request.headers["Authorization"].startswith("vapid t=")
        assert request.headers["Content-Encoding"] == "aes128gcm"
        return httpx.Response(201, json={})

    key = create_vapid_key()
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    recipient = ec.generate_private_key(ec.SECP256R1())
    public = recipient.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public_b64 = base64.urlsafe_b64encode(public).rstrip(b"=").decode()
    auth_b64 = base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send(
        endpoint="https://push.example.id/abc",
        p256dh=public_b64,
        auth=auth_b64,
        content=b"halo",
        vapid_public=key["public"],
        vapid_private=key["private"],
        http_client=client,
    )
    assert result["status"] == 201


def test_push_subscription_stored(test_dsn, make_user):
    u = make_user()
    subscription_id = save_subscription(
        u["user_id"],
        endpoint="https://push.example.id/abc",
        p256dh="p256dh-uji",
        auth="auth-uji",
        dsn=test_dsn,
    )
    items = list_subscriptions(u["user_id"], dsn=test_dsn)
    assert len(items) == 1 and items[0]["id"] == subscription_id
