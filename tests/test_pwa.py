"""Tes PWA 7 layar (FastAPI + Jinja + SSE)."""

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import LAYAR, create_app


@pytest.fixture
def client(make_user):
    u = make_user()
    app = create_app(test_user_id=u["user_id"])
    with TestClient(app) as c:
        c.user_id = u["user_id"]
        yield c


def test_tujuh_layar_ada_dan_200(client):
    for layar in LAYAR:
        resp = client.get(f"/{layar}")
        assert resp.status_code == 200, layar
        assert "Puspa" in resp.text


def test_tidak_ada_layar_kedelapan():
    assert len(LAYAR) == 7
    assert set(LAYAR) == {"chat", "tugas", "modul", "konektor", "memori", "pengaturan", "kantor"}


def test_tanpa_login_dialihkan_ke_masuk():
    app = create_app(test_user_id=None)
    with TestClient(app) as c:
        resp = c.get("/chat", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "/masuk" in resp.headers["location"]


def test_kirim_chat_dan_sse(client):
    resp = client.post("/api/chat", data={"teks": "halo Puspa"})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    halaman = client.get("/chat")
    assert "halo Puspa" in halaman.text
    sse = client.get("/sse")
    assert sse.status_code == 200
    assert "text/event-stream" in sse.headers["content-type"]
    assert "data:" in sse.text


def test_api_agen_buat_dan_hapus_puspa_ditolak(client, make_user):
    resp = client.post("/api/agen", data={"nama": "Agen Riset", "peran": "riset", "tujuan": "cari"})
    assert resp.status_code == 200
    agen_id = resp.json()["agen_id"]
    resp2 = client.post("/api/agen", data={"nama": "Agen Ketiga"})
    assert resp2.status_code == 200
    resp3 = client.post("/api/agen", data={"nama": "Agen Keempat"})
    assert resp3.status_code == 400  # maks 3
    hapus = client.delete(f"/api/agen/puspa-{client.user_id}")
    assert hapus.status_code == 400  # Puspa tidak bisa dihapus
    hapus2 = client.delete(f"/api/agen/{agen_id}")
    assert hapus2.status_code == 200


def test_api_tugas_dan_hentikan(client):
    resp = client.post("/api/tugas", data={"tujuan": "riset dan rangkum lima artikel"})
    assert resp.status_code == 200
    tugas_id = resp.json()["tugas_id"]
    assert resp.json()["status"] == "selesai"
    henti = client.post(f"/api/tugas/{tugas_id}/hentikan")
    assert henti.status_code == 200
    assert henti.json()["dihentikan"] == 0  # tidak ada sub-agen aktif


def test_service_worker_dan_manifest(client):
    assert client.get("/sw.js").status_code == 200
    assert client.get("/manifest.json").json()["name"] == "Puspa"
