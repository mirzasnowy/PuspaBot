"""Tests: WebUI lite as a real mode (G32).

The lite mode serves the Jinja templates with no React build and
the trial entry page against the G2 billing trial entitlements.
These tests are DB-free on purpose: the app is built with
``test_user_id`` and ``ensure_puspa_agent`` is stubbed, so this
file can run with ``--noconftest`` next to the parallel wave
workers without touching the shared ``puspa_test`` database.

Run: NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1
.venv/bin/python -m pytest tests/test_webui_lite.py -q --noconftest
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import puspa.pwa.app as pwa_app
from puspa.billing.config import BillingConfig
from puspa.billing.entitlements import issue_entitlement, issue_trial_entitlement
from puspa.pwa import lite
from puspa.pwa.app import SCREENS, create_app

SECRET = "test-trial-secret"
USER_ID = "lite-test-user"
NOW = datetime.now(UTC)


@pytest.fixture
def no_dist(monkeypatch):
    """Hide the React build so every screen falls back to Jinja."""
    monkeypatch.setattr(pwa_app, "DIST_DIR", Path("/nonexistent-dist-for-lite-tests"))


@pytest.fixture
def app(monkeypatch, no_dist):
    """Lite app for one test user, trial secret configured."""
    monkeypatch.setenv("PUSPA_ENTITLEMENT_SECRET", SECRET)
    monkeypatch.setattr(
        "puspa.runtime.agents.ensure_puspa_agent",
        lambda user_id, dsn=None: f"puspa-{user_id}",
    )
    return create_app(test_user_id=USER_ID)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _trial_cookie(entitlement) -> str:
    return lite.encode_trial_cookie(entitlement)


def _active_trial(now=NOW):
    return issue_trial_entitlement(
        user_id=USER_ID,
        machine_id=lite.machine_id_for_user(USER_ID),
        tier="pro",
        secret=SECRET,
        now=now,
    )


# -- lite module: trial checks -------------------------------------------


def test_check_trial_active_and_days_left():
    state = lite.check_trial(USER_ID, secret=SECRET, token=_active_trial(), now=NOW)
    assert state.status == "active"
    assert state.is_trial is True
    assert state.tier == "pro"
    assert state.days_left == 14


def test_check_trial_grace_after_expiry():
    issued = issue_trial_entitlement(
        user_id=USER_ID,
        machine_id=lite.machine_id_for_user(USER_ID),
        tier="pro",
        secret=SECRET,
        now=NOW - timedelta(days=20),  # expired 6 days ago, grace is 30 days
    )
    state = lite.check_trial(USER_ID, secret=SECRET, token=issued, now=NOW)
    assert state.status == "grace"
    assert state.days_left == 0


def test_check_trial_expired_past_grace():
    issued = issue_trial_entitlement(
        user_id=USER_ID,
        machine_id=lite.machine_id_for_user(USER_ID),
        tier="pro",
        secret=SECRET,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    state = lite.check_trial(USER_ID, secret=SECRET, token=issued, now=NOW)
    assert state.status == "expired"
    assert state.days_left == 0


def test_check_trial_tampered_is_none():
    entitlement = _active_trial()
    tampered = issue_entitlement(
        user_id=USER_ID,
        machine_id=entitlement.machine_id,
        tier="team",  # re-issued, different tier: signature no longer matches
        secret="another-secret",
        now=NOW,
    )
    assert tampered.tier == "team"
    state = lite.check_trial(USER_ID, secret=SECRET, token=tampered, now=NOW)
    assert state.status == "none"


def test_check_trial_wrong_user_is_none():
    other = issue_trial_entitlement(
        user_id="someone-else",
        machine_id="lite-someone-else",
        tier="pro",
        secret=SECRET,
        now=NOW,
    )
    state = lite.check_trial(USER_ID, secret=SECRET, token=other, now=NOW)
    assert state.status == "none"
    assert "another user" in (state.reason or "")


def test_check_trial_paid_entitlement_also_opens_chat():
    paid = issue_entitlement(
        user_id=USER_ID, machine_id="machine-1", tier="plus", secret=SECRET, now=NOW
    )
    state = lite.check_trial(USER_ID, secret=SECRET, token=paid, now=NOW)
    assert state.status == "active"
    assert state.is_trial is False


def test_check_trial_no_secret_never_trusts():
    state = lite.check_trial(USER_ID, secret="", token=_active_trial(), now=NOW)
    assert state.status == "none"
    assert "secret" in (state.reason or "")
    with pytest.raises(ValueError):
        lite.start_trial(USER_ID, machine_id="lite-x", secret="")


def test_decode_trial_cookie_failures_are_none():
    assert lite.decode_trial_cookie(None) is None
    assert lite.decode_trial_cookie("") is None
    assert lite.decode_trial_cookie("not json") is None
    assert lite.decode_trial_cookie('{"user_id": 1}') is None
    assert lite.decode_trial_cookie(_trial_cookie(_active_trial())) is not None


def test_prior_trial_blocks_second_trial():
    expired = issue_trial_entitlement(
        user_id=USER_ID,
        machine_id=lite.machine_id_for_user(USER_ID),
        tier="pro",
        secret=SECRET,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert lite.prior_trial_entitlement(USER_ID, secret=SECRET, token=expired, now=NOW)
    assert lite.prior_trial_entitlement(USER_ID, secret=SECRET, token=None) is None
    paid = issue_entitlement(
        user_id=USER_ID, machine_id="machine-1", tier="plus", secret=SECRET, now=NOW
    )
    assert lite.prior_trial_entitlement(USER_ID, secret=SECRET, token=paid) is None


def test_trial_cookie_max_age_covers_trial_plus_grace():
    cfg = BillingConfig(trial_days=14, grace_days=30)
    assert lite.trial_cookie_max_age(cfg) == 44 * 24 * 60 * 60


# -- all 9 templates render without dist ---------------------------------


@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_screen_renders_without_dist(client, screen):
    resp = client.get(f"/{screen}")
    assert resp.status_code == 200, screen
    assert "Puspa" in resp.text
    assert "{{" not in resp.text, screen


def test_login_renders_without_dist(client):
    resp = client.get("/masuk")
    assert resp.status_code == 200
    assert "Masuk ke Puspa" in resp.text


def test_evidence_panel_renders_without_dist(client, app):
    app.state.event.append(
        {
            "type": "evidence_recorded",
            "task_id": "",
            "agent_id": "puspa",
            "content": {
                "evidence_id": "ev-1",
                "claim": "Laptop X costs Rp10.000.000",
                "value": 10000000,
                "source_url": "https://example.com/laptop-x",
            },
            "created_at": 0,
        }
    )
    resp = client.get("/bukti/ev-1")
    assert resp.status_code == 200
    assert "Laptop X costs Rp10.000.000" in resp.text
    assert "10000000" in resp.text
    assert "https://example.com/laptop-x" in resp.text
    missing = client.get("/bukti/nope")
    assert missing.status_code == 200
    assert "tidak ditemukan" in missing.text


def test_nav_links_point_to_real_routes(client):
    resp = client.get("/chat")
    for href in (
        "/chat",
        "/tasks",
        "/modules",
        "/connectors",
        "/memory",
        "/settings",
        "/office",
        "/trial",
    ):
        assert f'href="{href}"' in resp.text, href
    for dead in ("/tugas", "/modul", "/konektor", "/memori", "/pengaturan", "/kantor"):
        assert f'href="{dead}"' not in resp.text, dead


def test_chat_page_shows_sender_and_uses_real_form_field(client):
    resp = client.post("/api/chat", data={"text": "halo lite"})
    assert resp.status_code == 200
    page = client.get("/chat")
    assert "halo lite" in page.text
    assert "users" in page.text  # sender, not the old {{ p.dari }} hole
    assert 'name="text"' in page.text
    assert 'hx-get="/office"' in page.text


def test_office_page_shows_live_agent_from_projection(client, app):
    app.state.event.append(
        {
            "type": "task_created",
            "task_id": "task-1",
            "agent_id": "puspa",
            "content": {"cost_rp": 250},
            "created_at": 0,
        }
    )
    page = client.get("/office")
    assert page.status_code == 200
    assert "puspa" in page.text
    assert "working" in page.text
    assert "task-1" in page.text
    assert "250" in page.text


def test_connectors_page_lists_agents_with_real_form_fields(client, monkeypatch):
    monkeypatch.setattr(
        "puspa.runtime.agents.list_agents",
        lambda user_id, dsn=None: [{"agent_id": "agent-1", "name": "Riset"}],
    )
    page = client.get("/connectors")
    assert page.status_code == 200
    assert "Riset" in page.text
    assert 'name="agent_id"' in page.text
    assert 'name="action"' in page.text


def test_memory_page_shows_fact_key_and_value(client, monkeypatch):
    # P-01 moved the memory screen's reads behind MemoryReadService:
    # patch the service seam (it returns a page dict with a "facts"
    # key), not the profile module the app no longer calls here.
    # consent_status is patched too: it opens a real DB connection,
    # so an unpatched call reads live state instead of the test's
    # fixture. is_guest is in-memory and needs no patch.
    monkeypatch.setattr(
        "puspa.pwa.memory_api.MemoryReadService.list_facts",
        lambda self, user_id, **kw: {
            "facts": [
                {
                    "id": "fact-1",
                    "key": "likes",
                    "content": "coffee",
                    "source_label": "chat",
                    "confidence": 0.9,
                    "confidence_stored": True,
                    "status": "active",
                },
            ],
        },
    )
    monkeypatch.setattr(
        "puspa.pwa.memory_api.MemoryReadService.consent_status",
        lambda self, user_id: {
            "status": "none",
            "text_version": None,
            "approved_at": None,
        },
    )
    page = client.get("/memory")
    assert page.status_code == 200
    assert "likes" in page.text
    assert "coffee" in page.text
    assert "{'key'" not in page.text  # no raw dict repr


def test_tasks_page_approval_queue_uses_real_fields(client, app):
    app.state.approval["req-1"] = {
        "request_id": "req-1",
        "kind": "send",
        "status": "requested",
        "task_id": "",
    }
    page = client.get("/tasks")
    assert page.status_code == 200
    assert "send" in page.text
    assert 'name="purpose"' in page.text
    assert 'name="approve"' in page.text
    resp = client.post("/api/persetujuan/req-1", data={"approve": "ya"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


# -- trial flow ------------------------------------------------------------


def test_trial_without_entitlement_shows_upgrade_invite(client):
    resp = client.get("/trial")
    assert resp.status_code == 200
    assert "Mulai trial gratis" in resp.text
    assert "Upgrade" in resp.text
    assert 'action="/trial/start"' in resp.text
    assert 'action="/trial/chat"' not in resp.text


def test_trial_requires_login(no_dist, monkeypatch):
    monkeypatch.setenv("PUSPA_ENTITLEMENT_SECRET", SECRET)
    monkeypatch.setattr(
        "puspa.runtime.agents.ensure_puspa_agent",
        lambda user_id, dsn=None: f"puspa-{user_id}",
    )
    anon = create_app(test_user_id=None)
    with TestClient(anon) as c:
        resp = c.get("/trial", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "/masuk" in resp.headers["location"]


def test_trial_start_mints_cookie_and_opens_chat(client):
    start = client.post("/trial/start", follow_redirects=False)
    assert start.status_code == 303
    assert start.headers["location"] == "/trial"
    assert "puspa_trial=" in start.headers.get("set-cookie", "")
    page = client.get("/trial")
    assert page.status_code == 200
    assert "Trial Pro" in page.text
    assert "Upgrade" in page.text
    assert 'action="/trial/chat"' in page.text
    assert "Mulai trial gratis" not in page.text


def test_trial_start_twice_reuses_the_same_trial(client):
    first = client.post("/trial/start", follow_redirects=False)
    first_cookie = first.headers.get("set-cookie", "")
    second = client.post("/trial/start", follow_redirects=False)
    assert second.status_code == 303
    assert second.headers.get("set-cookie", "") == first_cookie


def test_trial_chat_posts_message_and_redirects(client):
    client.post("/trial/start", follow_redirects=False)
    sent = client.post("/trial/chat", data={"text": "coba trial"}, follow_redirects=False)
    assert sent.status_code == 303
    assert sent.headers["location"] == "/trial"
    page = client.get("/trial")
    assert "coba trial" in page.text


def test_trial_chat_without_trial_redirects_without_storing(client):
    sent = client.post("/trial/chat", data={"text": "nope"}, follow_redirects=False)
    assert sent.status_code == 303
    assert sent.headers["location"] == "/trial"
    page = client.get("/trial")
    assert "nope" not in page.text


def test_trial_expired_shows_upgrade_not_chat(client):
    expired = issue_trial_entitlement(
        user_id=USER_ID,
        machine_id=lite.machine_id_for_user(USER_ID),
        tier="pro",
        secret=SECRET,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    client.cookies.set(lite.TRIAL_COOKIE, _trial_cookie(expired))
    page = client.get("/trial")
    assert page.status_code == 200
    assert "Trial kamu berakhir" in page.text
    assert "Upgrade" in page.text
    assert 'action="/trial/chat"' not in page.text


def test_trial_expired_cannot_start_a_second_trial(client):
    expired = issue_trial_entitlement(
        user_id=USER_ID,
        machine_id=lite.machine_id_for_user(USER_ID),
        tier="pro",
        secret=SECRET,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    client.cookies.set(lite.TRIAL_COOKIE, _trial_cookie(expired))
    start = client.post("/trial/start", follow_redirects=False)
    assert start.status_code == 303
    assert "trial_already_used" in start.headers["location"]
    page = client.get("/trial?error=trial_already_used")
    assert "sudah dipakai" in page.text


def test_trial_tampered_cookie_shows_upgrade(client):
    good = _active_trial()
    tampered = _trial_cookie(good).replace('"pro"', '"team"')
    client.cookies.set(lite.TRIAL_COOKIE, tampered)
    page = client.get("/trial")
    assert "Mulai trial gratis" in page.text
    assert 'action="/trial/chat"' not in page.text


def test_trial_grace_still_opens_chat(client):
    issued = issue_trial_entitlement(
        user_id=USER_ID,
        machine_id=lite.machine_id_for_user(USER_ID),
        tier="pro",
        secret=SECRET,
        now=NOW - timedelta(days=20),
    )
    client.cookies.set(lite.TRIAL_COOKIE, _trial_cookie(issued))
    page = client.get("/trial")
    assert page.status_code == 200
    assert "masa tenggang" in page.text
    assert 'action="/trial/chat"' in page.text


def test_trial_start_without_secret_reports_unavailable(no_dist, monkeypatch):
    monkeypatch.delenv("PUSPA_ENTITLEMENT_SECRET", raising=False)
    monkeypatch.setattr(
        "puspa.runtime.agents.ensure_puspa_agent",
        lambda user_id, dsn=None: f"puspa-{user_id}",
    )
    app = create_app(test_user_id=USER_ID)
    with TestClient(app) as c:
        start = c.post("/trial/start", follow_redirects=False)
        assert start.status_code == 303
        assert "trial_unavailable" in start.headers["location"]
        page = c.get("/trial?error=trial_unavailable")
        assert "belum dikonfigurasi" in page.text


def test_trial_page_is_jinja_even_when_dist_exists(monkeypatch):
    """The trial page never serves the SPA shell: it is lite-only."""
    monkeypatch.setenv("PUSPA_ENTITLEMENT_SECRET", SECRET)
    monkeypatch.setattr(
        "puspa.runtime.agents.ensure_puspa_agent",
        lambda user_id, dsn=None: f"puspa-{user_id}",
    )
    app = create_app(test_user_id=USER_ID)
    with TestClient(app) as c:
        page = c.get("/trial")
        assert page.status_code == 200
        assert "Coba Puspa" in page.text
