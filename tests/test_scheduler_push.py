"""Tes penjadwal (§2.14) + Web Push VAPID."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from puspa.push.push import (buat_kunci_vapid, dekripsi_payload,
                            enkripsi_payload, kirim, simpan_langganan,
                            daftar_langganan)
from puspa.scheduler.scheduler import (buat_pemicu, detak, jalan_pemicu,
                                       jalankan_yang_terlewat,
                                       jadwalkan_backup_harian, sehat, tick)


def _sekarang():
    return datetime.now(timezone.utc)


def test_tick_jalankan_pemicu_sekali_idempotent(test_dsn, make_user):
    u = make_user()
    kejadian = []
    sink = lambda jenis, isi: kejadian.append({"jenis": jenis, "isi": isi})
    buat_pemicu(u["user_id"], "jadwal", _sekarang() - timedelta(minutes=5),
                jadwal="harian 07:00", dsn=test_dsn)
    hasil = tick(sink=sink, dsn=test_dsn)
    assert len(hasil) == 1
    assert kejadian[0]["jenis"] == "pemicu_terpicu"
    # Putaran kedua: pemicu sudah geser +1 hari, tidak jalan lagi.
    assert tick(sink=sink, dsn=test_dsn) == []


def test_pemicu_terlewat_dijalankan_sekali_dan_dilaporkan(test_dsn, make_user):
    u = make_user()
    kejadian = []
    sink = lambda jenis, isi: kejadian.append({"jenis": jenis, "isi": isi})
    buat_pemicu(u["user_id"], "jadwal", _sekarang() - timedelta(hours=3), dsn=test_dsn)
    hasil = jalankan_yang_terlewat(sink=sink, dsn=test_dsn)
    assert len(hasil) == 1 and hasil[0]["terlewat"] is True
    assert kejadian[0]["jenis"] == "pemicu_terlewat"
    assert jalankan_yang_terlewat(sink=sink, dsn=test_dsn) == []


def test_detak_penjadal_mati_lebih_dari_2_menit(test_dsn):
    assert sehat(dsn=test_dsn) is False  # belum pernah detak
    detak(dsn=test_dsn)
    assert sehat(dsn=test_dsn) is True
    detak(dsn=test_dsn, sekarang=_sekarang() - timedelta(minutes=3))
    assert sehat(dsn=test_dsn) is False


def test_backup_harian_terjadwal_sebagai_pemicu(test_dsn, make_user):
    u = make_user()
    pemicu = jadwalkan_backup_harian(u["user_id"], dsn=test_dsn)
    assert pemicu.jenis == "backup_harian"
    assert pemicu.pemicu_berikutnya > _sekarang()


def test_vapid_enkripsi_bolak_balik():
    kunci = buat_kunci_vapid()
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
    badan, kunci_server, garam = enkripsi_payload(b"Tagihan PLN jatuh tempo besok.",
                                                  p256dh_penerima_b64=publik_b64, auth_b64=auth_b64)
    jelas = dekripsi_payload(badan, garam_b64=garam, kunci_server_b64=kunci_server,
                             privat_penerima_b64=privat_b64, auth_b64=auth_b64)
    assert jelas == b"Tagihan PLN jatuh tempo besok."


def test_kirim_push_lewat_mock():
    def handler(request):
        assert request.headers["Authorization"].startswith("vapid t=")
        assert request.headers["Content-Encoding"] == "aes128gcm"
        return httpx.Response(201, json={})

    kunci = buat_kunci_vapid()
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import base64

    penerima = ec.generate_private_key(ec.SECP256R1())
    publik = penerima.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    publik_b64 = base64.urlsafe_b64encode(publik).rstrip(b"=").decode()
    auth_b64 = base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    hasil = kirim(endpoint="https://push.example.id/abc", p256dh=publik_b64,
                  auth=auth_b64, isi=b"halo", vapid_publik=kunci["publik"],
                  vapid_privat=kunci["privat"], http_client=client)
    assert hasil["status"] == 201


def test_langganan_push_disimpan(test_dsn, make_user):
    u = make_user()
    langganan_id = simpan_langganan(u["user_id"], endpoint="https://push.example.id/abc",
                                    p256dh="p256dh-uji", auth="auth-uji", dsn=test_dsn)
    daftar = daftar_langganan(u["user_id"], dsn=test_dsn)
    assert len(daftar) == 1 and daftar[0]["id"] == langganan_id
