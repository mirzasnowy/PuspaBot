"""Tes PWA 14 layar (FastAPI + Jinja + SSE).

Revisi 7 membatasi PWA pada 7 layar, tetapi kerja kematangan G28
menambah Insights dan Onboarding (perintah Nero: kerjakan semua
28 celah), kanvas mockup menambah satu lagi, P-07 menambah layar
pagi, P-09 menambah layar live, dan P-10 menambah layar replay,
dan perbaikan layar Ruang yang rusak mendaftarkan layar Ruang
yang selama ini ada di SPA tapi tidak terdaftar di server — jadi
total sekarang 14. Tes jumlah layar di bawah mencerminkan batas
baru itu.
"""

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import SCREENS, create_app


@pytest.fixture
def client(make_user):
    u = make_user()
    app = create_app(test_user_id=u["user_id"])
    with TestClient(app) as c:
        c.user_id = u["user_id"]
        yield c


def test_eleven_screens_exist_and_200(client):
    for screen in SCREENS:
        resp = client.get(f"/{screen}")
        assert resp.status_code == 200, screen
        assert "Puspa" in resp.text


def test_fifteen_screens_exist_including_rooms():
    """The morning, live, and replay screens are the eleventh, twelfth, and thirteenth,
    and Rooms is the fourteenth (the SPA already listed
    it, the server did not). Artifacts is the fifteenth
    (the artifacts + run-logs browser from CowAgent research).

    The count is pinned on purpose: a sixteenth screen must be a
    deliberate decision with its own ticket, not an accident.
    """
    assert len(SCREENS) == 15
    assert set(SCREENS) == {
        "chat",
        "tasks",
        "modules",
        "connectors",
        "memory",
        "settings",
        "office",
        "live",
        "insights",
        "onboarding",
        "mockup",
        # P-07: morning summary + notes-to-actions (plan section 12.8).
        "morning",
        # P-10: replay timeline of a bot's steps (plan section 12.10).
        "replay",
        # The Rooms screen the SPA already listed.
        "rooms",
        # Artifacts + run-logs browser (CowAgent research).
        "artifacts",
    }


def test_without_login_redirected_to_login():
    app = create_app(test_user_id=None)
    with TestClient(app) as c:
        resp = c.get("/chat", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "/masuk" in resp.headers["location"]


def test_send_chat_and_sse(client):
    resp = client.post("/api/chat", data={"text": "halo Puspa"})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    page = client.get("/chat")
    assert "halo Puspa" in page.text
    sse = client.get("/sse")
    assert sse.status_code == 200
    assert "text/event-stream" in sse.headers["content-type"]
    assert "data:" in sse.text


def test_api_agent_create_and_delete_puspa_rejected(client, make_user):
    resp = client.post(
        "/api/agen", data={"name": "Agen Riset", "role": "riset", "purpose": "search"}
    )
    assert resp.status_code == 200
    agent_id = resp.json()["agent_id"]
    resp2 = client.post("/api/agen", data={"name": "Agen Ketiga"})
    assert resp2.status_code == 200
    resp3 = client.post("/api/agen", data={"name": "Agen Keempat"})
    assert resp3.status_code == 400  # maks 3
    delete = client.delete(f"/api/agen/puspa-{client.user_id}")
    assert delete.status_code == 400  # Puspa tidak bisa dihapus
    hapus2 = client.delete(f"/api/agen/{agent_id}")
    assert hapus2.status_code == 200


def test_api_task_and_stop(client):
    resp = client.post("/api/tugas", data={"purpose": "riset dan rangkum lima artikel"})
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    assert resp.json()["status"] == "completed"
    henti = client.post(f"/api/tugas/{task_id}/hentikan")
    assert henti.status_code == 200
    assert henti.json()["stopped"] == 0  # tidak ada sub-agen aktif


def test_service_worker_and_manifest(client):
    assert client.get("/sw.js").status_code == 200
    assert client.get("/manifest.json").json()["name"] == "Puspa"
