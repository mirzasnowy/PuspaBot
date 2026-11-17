"""Tes konektor Google (§2.8) lewat MockTransport — tanpa jaringan."""

import httpx
import pytest

from puspa.connectors.google import ConnectorError, GoogleConnector
from puspa.connectors.grants import (give_connector, cabut_connector,
                                     items_connector, punya_connector, GrantError)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.id")


def _connector(handler):
    return GoogleConnector("user-1", "puspa-1", http_client=_client(handler),
                           token_loader=lambda: "token-uji")


def test_gmail_search_and_read_past_pembaca():
    def handler(request):
        if "messages" in str(request.url) and request.method == "GET" and "q=" in str(request.url):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if "messages/m1" in str(request.url):
            return httpx.Response(200, json={"id": "m1", "snippet": "Tagihan PLN Rp132.500",
                                             "payload": {"body": {"data": ""}}})
        return httpx.Response(404, json={})

    k = _connector(handler)
    assert k.gmail_search("PLN") == [{"id": "m1"}]
    from puspa.quarantine.reader import QuarantinedReader

    # Pembaca hanya menerima domain sumber yang diizinkan.
    pembaca = QuarantinedReader({"mail.google.com"})
    result = k.gmail_read("m1", pembaca=pembaca)
    assert result["source"].endswith("m1") or "mail.google.com" in result["source"]


def test_gmail_send_without_approval_rejected():
    def handler(request):
        return httpx.Response(200, json={"id": "sent-1"})

    k = _connector(handler)
    with pytest.raises(ConnectorError, match="approval"):
        k.gmail_send(kepada="a@contoh.id", subjek="x", content="y", disetujui=False)
    result = k.gmail_send(kepada="a@contoh.id", subjek="x", content="y", disetujui=True)
    assert result["id"] == "sent-1"


def test_drive_search_and_calendar():
    def handler(request):
        url = str(request.url)
        if "drive/v3/files" in url:
            return httpx.Response(200, json={"files": [{"id": "f1", "name": "nota.pdf"}]})
        if "calendar" in url and request.method == "GET":
            return httpx.Response(200, json={"items": [{"summaries": "Rapat"}]})
        if "calendar" in url and request.method == "POST":
            return httpx.Response(200, json={"id": "ev-1"})
        return httpx.Response(404, json={})

    k = _connector(handler)
    assert k.drive_search("nota")[0]["name"] == "nota.pdf"
    assert k.calendar_read(time_min="2026-11-17T00:00:00Z", time_max="2026-11-18T00:00:00Z")[0]["summaries"] == "Rapat"
    with pytest.raises(ConnectorError, match="approval"):
        k.calendar_create_event(summary="Rapat", mulai="2026-11-17T10:00:00+07:00",
                              completed_at="2026-11-17T11:00:00+07:00", disetujui=False)
    assert k.calendar_create_event(summary="Rapat", mulai="2026-11-17T10:00:00+07:00",
                                 completed_at="2026-11-17T11:00:00+07:00", disetujui=True)["id"] == "ev-1"


def test_connector_without_token_rejected():
    k = GoogleConnector("user-1", "puspa-1", http_client=_client(lambda r: httpx.Response(200, json={})),
                        token_loader=lambda: None)
    with pytest.raises(ConnectorError, match="belum terhubung"):
        k.gmail_search("x")


def test_pemberian_connector_per_agent(make_user):
    u = make_user()
    from puspa.runtime.agents import create_agent

    agent = create_agent(u["user_id"], "Agen Riset")
    assert punya_connector(u["user_id"], agent["agent_id"], "gmail") is False
    give_connector(u["user_id"], agent["agent_id"], "gmail")
    assert punya_connector(u["user_id"], agent["agent_id"], "gmail") is True
    assert items_connector(u["user_id"], agent["agent_id"])[0]["scope"] == ["read", "create_draft", "send"]
    cabut_connector(u["user_id"], agent["agent_id"], "gmail")
    assert punya_connector(u["user_id"], agent["agent_id"], "gmail") is False
    with pytest.raises(GrantError):
        give_connector(u["user_id"], agent["agent_id"], "whatsapp")
