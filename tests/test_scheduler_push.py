"""Tes penjadwal (§2.14) + Web Push VAPID."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from puspa.push.push import (create_key_vapid, dekripsi_payload,
                            enkripsi_payload, send, save_langganan,
                            items_langganan)
from puspa.scheduler.scheduler import (create_trigger, heartbeat, jalan_trigger,
                                       jalankan_yang_terlewat,
                                       schedule_backup_daily, sehat, tick)


def _sekarang():
    return datetime.now(timezone.utc)


def test_tick_jalankan_trigger_sekali_idempotent(test_dsn, make_user):
    u = make_user()
    event = []
    sink = lambda event_type, content: event.append({"type": event_type, "content": content})
    create_trigger(u["user_id"], "schedule", _sekarang() - timedelta(minutes=5),
                schedule="harian 07:00", dsn=test_dsn)
    result = tick(sink=sink, dsn=test_dsn)
    assert len(result) == 1
    assert event[0]["type"] == "trigger_fired"
    # Putaran kedua: pemicu sudah geser +1 hari, tidak jalan lagi.
    assert tick(sink=sink, dsn=test_dsn) == []


def test_trigger_terlewat_dijalankan_sekali_and_dilaporkan(test_dsn, make_user):
    u = make_user()
    event = []
    sink = lambda event_type, content: event.append({"type": event_type, "content": content})
    create_trigger(u["user_id"], "schedule", _sekarang() - timedelta(hours=3), dsn=test_dsn)
    result = jalankan_yang_terlewat(sink=sink, dsn=test_dsn)
    assert len(result) == 1 and result[0]["terlewat"] is True
    assert event[0]["type"] == "trigger_missed"
    assert jalankan_yang_terlewat(sink=sink, dsn=test_dsn) == []


def test_heartbeat_penjadal_mati_more_dari_2_minute(test_dsn):
    assert sehat(dsn=test_dsn) is False  # belum pernah detak
    heartbeat(dsn=test_dsn)
    assert sehat(dsn=test_dsn) is True
    heartbeat(dsn=test_dsn, sekarang=_sekarang() - timedelta(minutes=3))
    assert sehat(dsn=test_dsn) is False


def test_backup_daily_terjadwal_sebagai_trigger(test_dsn, make_user):
    u = make_user()
    trigger = schedule_backup_daily(u["user_id"], dsn=test_dsn)
    assert trigger.event_type == "backup_daily"
    assert trigger.next_trigger_at > _sekarang()


def test_vapid_enkripsi_bolak_balik():
    key = create_key_vapid()
    # Pasangan kunci penerima (simulasi browser).
    from puspa.push.push import _muat_privat, _b64url
    from cryptography.hazmat.primitives import serialization

    penerima_privat = __import__("cryptography.hazmat.primitives.asymmetric.ec", fromlist=["generate_private_key"]).generate_private_key(
        __import__("cryptography.hazmat.primitives.asymmetric.ec", fromlist=["SECP256R1"]).SECP256R1())
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec

    d = penerima_privat.private_numbers().private_value.to_bytes(32, "big")
    privat_b64 = base64.urlsafe_b64encode(d).rstrip(b"=").decode()
    publik = penerima_privat.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    publik_b64 = base64.urlsafe_b64encode(publik).rstrip(b"=").decode()
    auth_b64 = base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode()
    badan, key_server, garam = enkripsi_payload(b"Tagihan PLN jatuh tempo besok.",
                                                  p256dh_penerima_b64=publik_b64, auth_b64=auth_b64)
    jelas = dekripsi_payload(badan, garam_b64=garam, key_server_b64=key_server,
                             privat_penerima_b64=privat_b64, auth_b64=auth_b64)
    assert jelas == b"Tagihan PLN jatuh tempo besok."


def test_send_push_past_mock():
    def handler(request):
        assert request.headers["Authorization"].startswith("vapid t=")
        assert request.headers["Content-Encoding"] == "aes128gcm"
        return httpx.Response(201, json={})

    key = create_key_vapid()
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import base64

    penerima = ec.generate_private_key(ec.SECP256R1())
    publik = penerima.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    publik_b64 = base64.urlsafe_b64encode(publik).rstrip(b"=").decode()
    auth_b64 = base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send(endpoint="https://push.example.id/abc", p256dh=publik_b64,
                  auth=auth_b64, content=b"halo", vapid_publik=key["publik"],
                  vapid_privat=key["privat"], http_client=client)
    assert result["status"] == 201


def test_langganan_push_disimpan(test_dsn, make_user):
    u = make_user()
    langganan_id = save_langganan(u["user_id"], endpoint="https://push.example.id/abc",
                                    p256dh="p256dh-uji", auth="auth-uji", dsn=test_dsn)
    items = items_langganan(u["user_id"], dsn=test_dsn)
    assert len(items) == 1 and items[0]["id"] == langganan_id
