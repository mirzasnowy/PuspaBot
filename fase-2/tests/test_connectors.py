"""Tes konektor Google (§2.8) lewat MockTransport — tanpa jaringan."""

import httpx
import pytest

from puspa.connectors.google import ConnectorError, GoogleConnector
from puspa.connectors.grants import (beri_konektor, cabut_konektor,
                                     daftar_konektor, punya_konektor, GrantError)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.id")


def _konektor(handler):
    return GoogleConnector("user-1", "puspa-1", http_client=_client(handler),
                           token_loader=lambda: "token-uji")


def test_gmail_cari_dan_baca_lewat_pembaca():
    def handler(request):
        if "messages" in str(request.url) and request.method == "GET" and "q=" in str(request.url):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if "messages/m1" in str(request.url):
            return httpx.Response(200, json={"id": "m1", "snippet": "Tagihan PLN Rp132.500",
                                             "payload": {"body": {"data": ""}}})
        return httpx.Response(404, json={})

    k = _konektor(handler)
    assert k.gmail_cari("PLN") == [{"id": "m1"}]
    from puspa.quarantine.reader import QuarantinedReader

    # Pembaca hanya menerima domain sumber yang diizinkan.
    pembaca = QuarantinedReader({"mail.google.com"})
    hasil = k.gmail_baca("m1", pembaca=pembaca)
    assert hasil["source"].endswith("m1") or "mail.google.com" in hasil["source"]


def test_gmail_kirim_tanpa_persetujuan_ditolak():
    def handler(request):
        return httpx.Response(200, json={"id": "sent-1"})

    k = _konektor(handler)
    with pytest.raises(ConnectorError, match="persetujuan"):
        k.gmail_kirim(kepada="a@contoh.id", subjek="x", isi="y", disetujui=False)
    hasil = k.gmail_kirim(kepada="a@contoh.id", subjek="x", isi="y", disetujui=True)
    assert hasil["id"] == "sent-1"


def test_drive_cari_dan_calendar():
    def handler(request):
        url = str(request.url)
        if "drive/v3/files" in url:
            return httpx.Response(200, json={"files": [{"id": "f1", "name": "nota.pdf"}]})
        if "calendar" in url and request.method == "GET":
            return httpx.Response(200, json={"items": [{"summary": "Rapat"}]})
        if "calendar" in url and request.method == "POST":
            return httpx.Response(200, json={"id": "ev-1"})
        return httpx.Response(404, json={})

    k = _konektor(handler)
    assert k.drive_cari("nota")[0]["name"] == "nota.pdf"
    assert k.calendar_baca(waktu_min="2026-11-17T00:00:00Z", waktu_max="2026-11-18T00:00:00Z")[0]["summary"] == "Rapat"
    with pytest.raises(ConnectorError, match="persetujuan"):
        k.calendar_buat_event(ringkasan="Rapat", mulai="2026-11-17T10:00:00+07:00",
                              selesai="2026-11-17T11:00:00+07:00", disetujui=False)
    assert k.calendar_buat_event(ringkasan="Rapat", mulai="2026-11-17T10:00:00+07:00",
                                 selesai="2026-11-17T11:00:00+07:00", disetujui=True)["id"] == "ev-1"


def test_konektor_tanpa_token_ditolak():
    k = GoogleConnector("user-1", "puspa-1", http_client=_client(lambda r: httpx.Response(200, json={})),
                        token_loader=lambda: None)
    with pytest.raises(ConnectorError, match="belum terhubung"):
        k.gmail_cari("x")


def test_pemberian_konektor_per_agen(make_user):
    u = make_user()
    from puspa.runtime.agents import buat_agen

    agen = buat_agen(u["user_id"], "Agen Riset")
    assert punya_konektor(u["user_id"], agen["agen_id"], "gmail") is False
    beri_konektor(u["user_id"], agen["agen_id"], "gmail")
    assert punya_konektor(u["user_id"], agen["agen_id"], "gmail") is True
    assert daftar_konektor(u["user_id"], agen["agen_id"])[0]["scope"] == ["baca", "buat_draf", "kirim"]
    cabut_konektor(u["user_id"], agen["agen_id"], "gmail")
    assert punya_konektor(u["user_id"], agen["agen_id"], "gmail") is False
    with pytest.raises(GrantError):
        beri_konektor(u["user_id"], agen["agen_id"], "whatsapp")
