"""Tests: the language switch (COW-18).

- The per-user language store: Indonesian by default, English
  when saved, unknown locales refused, a stored code the
  catalogs no longer ship reads Indonesian.
- The API: GET/POST /api/language (login required), and the
  public catalog GET /api/i18n/catalog (merged, never blank).
- The flip: saving English flips a rendered server string
  (an onboarding hint and the approval 404) to English, and
  saving Indonesian flips it back.
- The CLI: the unknown-command line answers in Indonesian by
  default, in English with --lang en / PUSPA_LOCALE=en.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from puspa.i18n import render
from puspa.pwa.app import create_app
from puspa.pwa.language import (
    LanguageSettings,
    create_schema,
    get_language,
    get_settings,
    save_language,
    save_settings,
)

ALL_KEYS = 62


@pytest.fixture
def client(make_user, test_dsn):
    user = make_user()
    create_schema(test_dsn)
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.user_id = user["user_id"]
        yield test_client


# --- the store ------------------------------------------------------------


def test_get_language_defaults_to_indonesian(make_user, test_dsn):
    user = make_user()
    assert get_language(user["user_id"], dsn=test_dsn) == "id"
    assert get_settings(user["user_id"], dsn=test_dsn) == LanguageSettings(locale="id")


def test_save_language_persists_per_user(make_user, test_dsn):
    user_a, user_b = make_user(), make_user()
    assert save_language(user_a["user_id"], "en", dsn=test_dsn).locale == "en"
    assert get_language(user_a["user_id"], dsn=test_dsn) == "en"
    assert get_language(user_b["user_id"], dsn=test_dsn) == "id"
    assert save_language(user_a["user_id"], "id", dsn=test_dsn).locale == "id"
    assert get_language(user_a["user_id"], dsn=test_dsn) == "id"


def test_save_settings_accepts_dict_and_string(make_user, test_dsn):
    user = make_user()
    assert save_settings(user["user_id"], {"locale": "en"}, dsn=test_dsn).locale == "en"
    assert save_settings(user["user_id"], "id", dsn=test_dsn).locale == "id"
    assert (
        save_settings(user["user_id"], LanguageSettings(locale="en"), dsn=test_dsn).locale == "en"
    )


def test_save_language_refuses_unknown_locales(make_user, test_dsn):
    user = make_user()
    for bad in ("xx", "ENGLISH", "", None, 42):
        with pytest.raises(ValueError, match="unknown locale"):
            save_language(user["user_id"], bad, dsn=test_dsn)
    assert get_language(user["user_id"], dsn=test_dsn) == "id"


def test_stored_code_no_longer_shipped_reads_indonesian(make_user, test_dsn):
    """A locale file removed after the choice was saved must never
    crash a request: the stored code reads Indonesian instead."""
    from puspa.accounts.ports import _connect

    user = make_user()
    create_schema(test_dsn)
    save_language(user["user_id"], "en", dsn=test_dsn)
    with _connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_language SET locale = 'xx' WHERE user_id = %s",
                (user["user_id"],),
            )
        conn.commit()
    assert get_language(user["user_id"], dsn=test_dsn) == "id"


# --- the API --------------------------------------------------------------


def test_get_language_api_defaults_to_indonesian(client):
    resp = client.get("/api/language")
    assert resp.status_code == 200
    assert resp.json() == {"locale": "id", "available_locales": ["id", "en"]}


def test_save_language_api_persists(client):
    resp = client.post("/api/language", json={"locale": "en"})
    assert resp.status_code == 200
    assert resp.json() == {"locale": "en", "available_locales": ["id", "en"]}
    assert client.get("/api/language").json()["locale"] == "en"
    resp = client.post("/api/language", json={"locale": "id"})
    assert resp.json()["locale"] == "id"


def test_save_language_api_refuses_unknown_and_bad_bodies(client):
    for body in (
        {"locale": "xx"},
        {"locale": ""},
        {"locale": 42},
        {"locale": None},
        {},
        {"other": "en"},
        ["en"],
    ):
        resp = client.post("/api/language", json=body)
        assert resp.status_code == 400, body
    assert client.get("/api/language").json()["locale"] == "id"


def test_language_api_requires_login(make_user, test_dsn):
    user = make_user()
    create_schema(test_dsn)
    app = create_app(dsn=test_dsn)
    with TestClient(app, raise_server_exceptions=False) as anon:
        assert anon.get("/api/language").status_code == 401
        assert anon.post("/api/language", json={"locale": "en"}).status_code == 401
    assert user["user_id"] is not None


# --- the catalog ----------------------------------------------------------


def test_catalog_serves_english_when_asked(client):
    resp = client.get("/api/i18n/catalog?locale=en")
    assert resp.status_code == 200
    data = resp.json()
    assert data["locale"] == "en"
    assert len(data["strings"]) == ALL_KEYS
    assert data["strings"]["error.not_logged_in"] == "not signed in"
    assert data["strings"]["ui.nav.tasks"] == "Tasks"
    assert data["strings"]["hint.first_modules_visit.title"] == ("Modules add capabilities")
    assert all(data["strings"].values()), "no blank strings in the catalog"


def test_catalog_unknown_locale_falls_back_to_indonesian_not_an_error(client):
    resp = client.get("/api/i18n/catalog?locale=xx")
    assert resp.status_code == 200
    assert resp.json()["locale"] == "id"
    assert resp.json()["strings"]["error.not_logged_in"] == "belum masuk"


def test_catalog_follows_the_stored_language(client):
    assert client.get("/api/i18n/catalog").json()["locale"] == "id"
    client.post("/api/language", json={"locale": "en"})
    data = client.get("/api/i18n/catalog").json()
    assert data["locale"] == "en"
    assert data["strings"]["error.not_logged_in"] == "not signed in"


def test_catalog_is_public_for_anonymous_users(make_user, test_dsn):
    make_user()
    create_schema(test_dsn)
    app = create_app(dsn=test_dsn)
    with TestClient(app, raise_server_exceptions=False) as anon:
        resp = anon.get("/api/i18n/catalog?locale=en")
        assert resp.status_code == 200
        assert resp.json()["strings"]["ui.nav.tasks"] == "Tasks"
        assert anon.get("/api/i18n/catalog").json()["locale"] == "id"


# --- the flip: a switch flips a rendered string ---------------------------


def test_switch_flips_hints_and_approval_error(client):
    """Saving English flips server-rendered strings to English;
    saving Indonesian flips them back. This is the switch, end
    to end, not just the stored value."""
    hints_url = "/api/onboarding/hints?screen=modules&first_visit=true"

    assert client.get(hints_url).json()["hints"][0]["title"] == ("Modul menambah kemampuan")
    resp = client.post("/api/persetujuan/req-tidak-ada", data={"approve": "ya"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "permintaan tidak dikenal"
    assert resp.json()["error"] == render("error.unknown_request")

    assert client.post("/api/language", json={"locale": "en"}).status_code == 200
    hints = client.get(hints_url).json()["hints"]
    assert [hint["flag"] for hint in hints] == ["first_modules_visit"]
    assert hints[0]["title"] == "Modules add capabilities"
    assert hints[0]["action_label"] == "Open Modules"
    assert hints[0]["title"] == render("hint.first_modules_visit.title", locale="en")
    resp = client.post("/api/persetujuan/req-tidak-ada", data={"approve": "ya"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "unknown request"

    assert client.post("/api/language", json={"locale": "id"}).status_code == 200
    assert client.get(hints_url).json()["hints"][0]["title"] == ("Modul menambah kemampuan")
    resp = client.post("/api/persetujuan/req-tidak-ada", data={"approve": "ya"})
    assert resp.json()["error"] == "permintaan tidak dikenal"


# --- the lite-mode template -----------------------------------------------


def test_settings_lite_template_renders_with_language_switch_without_dist(
    make_user, monkeypatch, tmp_path
):
    """The Jinja settings template must exist and carry the switch:
    CI runs without a dist/ build, so the page falls back to
    apps/pwa/templates/settings.html. A missing template would 500
    here while the local dist/ build hides it."""
    import puspa.pwa.app as app_module

    user = make_user()
    app = create_app(test_user_id=user["user_id"])
    monkeypatch.setattr(app_module, "DIST_DIR", tmp_path / "no-dist-here")
    with TestClient(app, raise_server_exceptions=False) as test_client:
        resp = test_client.get("/settings")
        assert resp.status_code == 200
        assert "language-select" in resp.text
        assert "/api/language" in resp.text
        assert "Bahasa" in resp.text
    assert user["user_id"] is not None


# --- the CLI --------------------------------------------------------------


def test_cli_unknown_command_defaults_to_indonesian(capsys):
    from puspa.cli import main

    assert main(["frobnicate"]) == 0
    assert "perintah tidak dikenal: 'frobnicate'" in capsys.readouterr().out


def test_cli_unknown_command_in_english_with_lang_flag(capsys):
    from puspa.cli import main

    assert main(["--lang", "en", "frobnicate"]) == 0
    assert "unknown command: 'frobnicate'" in capsys.readouterr().out
    assert main(["--lang=en", "frobnicate"]) == 0
    assert "unknown command: 'frobnicate'" in capsys.readouterr().out


def test_cli_unknown_command_in_english_with_env_var(capsys, monkeypatch):
    from puspa.cli import main

    monkeypatch.setenv("PUSPA_LOCALE", "en")
    assert main(["frobnicate"]) == 0
    assert "unknown command: 'frobnicate'" in capsys.readouterr().out


def test_cli_unknown_lang_falls_back_to_indonesian(capsys, monkeypatch):
    from puspa.cli import main

    assert main(["--lang", "xx", "frobnicate"]) == 0
    assert "perintah tidak dikenal: 'frobnicate'" in capsys.readouterr().out
    monkeypatch.setenv("PUSPA_LOCALE", "xx")
    assert main(["frobnicate"]) == 0
    assert "perintah tidak dikenal: 'frobnicate'" in capsys.readouterr().out
