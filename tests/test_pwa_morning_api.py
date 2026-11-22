"""Tests for the morning summary + notes-to-actions API (P-07,
plan section 12.8).

The Google connector and the public prayer/holiday sources are
faked (``morning_connector_factory`` + ``morning_http_client``
seams on ``create_app``); the test database is real. No live
network, no real Google token: with no token stored, the status
endpoint honestly reports Calendar and Gmail as not connected.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.voice.transcriber import StubTranscriber

TRANSCRIPT = "Rapat pengajian. @Budi tolong hubungi ustadz besok. Bayar iuran kas."

PRAYER_PAYLOAD = {
    "code": 200,
    "status": "OK",
    "data": {
        "timings": {
            "Fajr": "04:03",
            "Sunrise": "05:26",
            "Dhuhr": "11:38",
            "Asr": "15:02",
            "Sunset": "17:51",
            "Maghrib": "17:51",
            "Isha": "19:05",
            "Imsak": "03:53",
            "Midnight": "23:38",
            "Firstthird": "21:42",
            "Lastthird": "01:34",
        },
        "date": {
            "readable": "20 Nov 2026",
            "timestamp": "1795132800",
            "hijri": {
                "date": "10-06-1448",
                "month": {"number": 6, "en": "Jumādá al-ākhirah"},
                "year": "1448",
            },
            "gregorian": {"date": "20-11-2026"},
        },
        "meta": {"method": {"id": 20, "name": "Kementerian Agama Republik Indonesia"}},
    },
}

HOLIDAY_PAYLOAD = [
    {"date": "2026-11-20", "localName": "Hari Libur Contoh", "name": "Example Holiday"},
    {"date": "2026-12-15", "localName": "Hari Raya Natal", "name": "Christmas Day"},
]


class FakeGoogle:
    """Google connector double: one event + one bill-like email."""

    def calendar_read(self, *, time_min, time_max):
        return [
            {
                "summary": "Rapat pagi",
                "start": {"dateTime": "2026-11-20T09:00:00+07:00"},
                "end": {"dateTime": "2026-11-20T10:00:00+07:00"},
                "location": "Rumah",
            }
        ]

    def gmail_search(self, query, max_results=10):
        assert "tagihan" in query
        return [{"id": "m1"}]

    def gmail_read(self, message_id, *, reader=None):
        assert message_id == "m1"
        return {
            "id": "m1",
            "snippet": "Tagihan listrik Anda jatuh tempo 12 Desember 2026.",
            "payload": {
                "headers": [{"name": "Subject", "value": "Tagihan Listrik"}],
            },
        }


def _public_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _handler(request):
    if "aladhan" in request.url.host:
        return httpx.Response(200, json=PRAYER_PAYLOAD)
    if "nager" in request.url.host:
        return httpx.Response(200, json=HOLIDAY_PAYLOAD)
    return httpx.Response(404, json={})


@pytest.fixture
def client(make_user):
    user = make_user()
    app = create_app(
        test_user_id=user["user_id"],
        transcriber=StubTranscriber(TRANSCRIPT),
        morning_connector_factory=lambda user_id: FakeGoogle(),
        morning_http_client=_public_client(_handler),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        c.user_id = user["user_id"]
        yield c


def _store_google_token(user_id, test_dsn, *, scopes):
    """Store a Google token with the given granted scopes, so the
    status endpoint sees Calendar/Gmail as connected."""
    from puspa.auth.google_oauth import store_tokens

    store_tokens(
        user_id,
        {"access_token": "fake", "refresh_token": "fake", "scope": scopes},
        dsn=test_dsn,
    )


def test_status_is_honest_when_google_is_not_connected(client, vault_master_key):
    body = client.get("/api/morning/status").json()
    assert body["calendar"]["status"] == "not_connected"
    assert body["gmail"]["status"] == "not_connected"
    assert body["transcriber"]["engine"] == "stub"
    assert body["transcriber"]["engine_selected"] is False
    assert body["prayer"]["status"] == "public"
    assert body["holidays"]["status"] == "public"


def test_status_connected_when_scopes_granted(client, test_dsn, vault_master_key):
    _store_google_token(
        client.user_id,
        test_dsn,
        scopes=(
            "https://www.googleapis.com/auth/calendar.readonly "
            "https://www.googleapis.com/auth/gmail.readonly"
        ),
    )
    body = client.get("/api/morning/status").json()
    assert body["calendar"]["status"] == "connected"
    assert body["gmail"]["status"] == "connected"


def test_status_not_connected_for_missing_scope_only(client, test_dsn, vault_master_key):
    _store_google_token(
        client.user_id,
        test_dsn,
        scopes="https://www.googleapis.com/auth/gmail.readonly",
    )
    body = client.get("/api/morning/status").json()
    assert body["calendar"]["status"] == "not_connected"
    assert body["gmail"]["status"] == "connected"


def test_summary_with_connected_google(client, test_dsn, vault_master_key):
    _store_google_token(
        client.user_id,
        test_dsn,
        scopes=(
            "https://www.googleapis.com/auth/calendar.readonly "
            "https://www.googleapis.com/auth/gmail.readonly"
        ),
    )
    resp = client.get("/api/morning/summary?date=2026-11-20&city=Jakarta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-11-20"
    assert body["schedule"]["events"][0]["summary"] == "Rapat pagi"
    assert body["schedule"]["events"][0]["location"] == "Rumah"
    assert body["bills"]["candidates"][0]["subject"] == "Tagihan Listrik"
    assert body["bills"]["candidates"][0]["due_hint"] == "12 Desember 2026"
    assert body["bills"]["candidates"][0]["verified"] is False
    assert body["holidays"]["today"][0]["local_name"] == "Hari Libur Contoh"
    assert body["holidays"]["upcoming"][0]["local_name"] == "Hari Raya Natal"
    assert body["prayer"]["timings"]["dhuhr"] == "11:38"
    assert body["prayer"]["timings"]["imsak"] is None  # not Ramadan
    assert body["source_status"]["calendar"]["status"] == "ok"
    assert body["source_status"]["gmail"]["status"] == "ok"
    assert body["source_status"]["tasks"]["status"] == "ok"
    assert body["source_status"]["prayer"]["status"] == "ok"


def test_summary_focus_is_picked_by_workspace_and_recorded(client, test_dsn, vault_master_key):
    """The live wiring: GET /api/morning/summary carries a focus
    picked by the K18 bus, and the broadcast is ledger-recorded.

    The fake briefing has one event (09:00 on 2026-11-20, long
    past by the time this runs, so no imminent-event veto) and
    one bill with a due hint: bills outscore the schedule 0.77
    to 0.75, so the focus is the bill."""
    from puspa.ledger.ledger import Ledger

    _store_google_token(
        client.user_id,
        test_dsn,
        scopes=(
            "https://www.googleapis.com/auth/calendar.readonly "
            "https://www.googleapis.com/auth/gmail.readonly"
        ),
    )
    resp = client.get("/api/morning/summary?date=2026-11-20&city=Jakarta")
    assert resp.status_code == 200
    body = resp.json()
    focus = body["focus"]
    assert focus["winner"]["module"] == "bills"
    assert focus["action"] == "review_bill"
    assert focus["winner"]["content"] == "Tagihan Listrik"
    assert focus["scores"]["bills"] == 0.77
    assert focus["scores"]["schedule"] == 0.75
    assert focus["blocked"] == []
    assert focus["ledger_event_id"] is not None
    events = Ledger(test_dsn).events(
        f"morning-focus-{client.user_id}-2026-11-20",
        event_type="task_completed",
    )
    assert len(events) == 1
    assert events[0]["content"]["job"] == "workspace_broadcast"
    assert events[0]["content"]["winner"]["module"] == "bills"
    assert str(events[0]["id"]) == focus["ledger_event_id"]


def test_summary_without_google_reports_sources_honestly(client, vault_master_key):
    body = client.get("/api/morning/summary?date=2026-11-20").json()
    assert body["source_status"]["calendar"]["status"] == "not_connected"
    assert body["source_status"]["gmail"]["status"] == "not_connected"
    assert body["schedule"]["events"] == []
    assert body["bills"]["candidates"] == []
    # Public sources still load: they need no account.
    assert body["source_status"]["prayer"]["status"] == "ok"
    assert body["source_status"]["holidays"]["status"] == "ok"
    assert body["prayer"]["timings"]["fajr"] == "04:03"


def test_summary_rejects_bad_date(client):
    assert client.get("/api/morning/summary?date=not-a-date").status_code == 400


def test_summary_public_source_failure_degrades_only_that_section(make_user):
    def failing_handler(request):
        return httpx.Response(503, json={})

    user = make_user()
    app = create_app(
        test_user_id=user["user_id"],
        morning_http_client=_public_client(failing_handler),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/morning/summary?date=2026-11-20").json()
    assert body["source_status"]["prayer"]["status"] == "unavailable"
    assert body["source_status"]["holidays"]["status"] == "unavailable"
    assert body["prayer"] is None
    assert body["holidays"]["today"] == []
    assert body["source_status"]["tasks"]["status"] == "ok"


def test_create_note_from_text_creates_tracked_tasks(client, test_dsn):
    resp = client.post(
        "/api/morning/notes/text",
        json={
            "text": "Rapat pengajian. @Budi tolong hubungi ustadz besok. Bayar iuran kas.",
            "source": "meeting",
            "title": "Pengajian",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["note_id"]
    assert "Rapat pengajian" in body["summary"]
    assert len(body["actions"]) == 2
    assert body["actions"][0]["owner"] == "Budi"
    assert all(action["task_id"] for action in body["actions"])
    # The actions are tracked tasks: the summary shows them.
    summary = client.get("/api/morning/summary?date=2026-11-20").json()
    assert len(summary["tasks"]["open"]) == 2
    # And the note lists back.
    listed = client.get("/api/morning/notes").json()["notes"]
    assert [note["note_id"] for note in listed] == [body["note_id"]]
    one = client.get(f"/api/morning/notes/{body['note_id']}").json()
    assert one["raw_text"].startswith("Rapat pengajian")
    assert one["title"] == "Pengajian"


def test_create_note_from_voice_uses_the_transcriber_seam(client):
    resp = client.post(
        "/api/morning/notes/voice",
        files={"audio": ("rec.webm", b"fake-recording", "audio/webm")},
        data={"source": "meeting"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["transcript"] == TRANSCRIPT
    assert body["engine"] == "stub"
    assert body["audio_deleted"] is True
    assert len(body["actions"]) == 2
    assert body["actions"][0]["owner"] == "Budi"


def test_create_note_rejects_empty_text_bad_source_and_bad_audio(client):
    assert client.post("/api/morning/notes/text", json={"text": "  "}).status_code == 400
    assert (
        client.post(
            "/api/morning/notes/text",
            json={"text": "cek harga", "source": "email"},
        ).status_code
        == 400
    )
    resp = client.post(
        "/api/morning/notes/voice",
        files={"audio": ("rec.mp4", b"x", "video/mp4")},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/morning/notes/voice",
        files={"audio": ("rec.webm", b"x", "audio/webm")},
        data={"source": "email"},
    )
    assert resp.status_code == 400


def test_notes_unknown_id_is_404(client):
    assert client.get("/api/morning/notes/00000000-0000-0000-0000-000000000000").status_code == 404


def test_morning_routes_require_login():
    app = create_app(test_user_id=None)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/api/morning/status").status_code == 401
        assert c.get("/api/morning/summary").status_code == 401
        assert c.get("/api/morning/notes").status_code == 401
        assert c.post("/api/morning/notes/text", json={"text": "cek harga"}).status_code == 401
