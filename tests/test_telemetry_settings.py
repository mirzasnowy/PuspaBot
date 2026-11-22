"""Tests for the per-user telemetry toggle in Postgres (AST-09).

The toggle lives in the ``telemetry_settings`` table (one row
per user: the boolean only, never an endpoint, a key, or any
usage data). The default is off, and the stored value is only
one input to the effective switch — the ``PUSPA_TELEMETRY``
environment flag wins when set (see tests/test_telemetry.py).
"""

import psycopg
import pytest

from puspa.telemetry.settings import (
    TelemetrySettings,
    create_schema,
    get_settings,
    save_settings,
)


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture(autouse=True)
def _telemetry_settings_table(test_dsn):
    create_schema(test_dsn)
    yield
    with psycopg.connect(test_dsn) as conn:
        conn.execute("TRUNCATE telemetry_settings")
        conn.commit()


def test_default_toggle_is_off(user, test_dsn):
    settings = get_settings(user["user_id"], dsn=test_dsn)
    assert settings.enabled is False
    assert settings.to_public_dict() == {"enabled": False}


def test_save_and_read_round_trip(user, test_dsn):
    saved = save_settings(user["user_id"], True, dsn=test_dsn)
    assert saved.enabled is True
    assert get_settings(user["user_id"], dsn=test_dsn).enabled is True
    saved = save_settings(user["user_id"], {"enabled": False}, dsn=test_dsn)
    assert saved.enabled is False
    assert get_settings(user["user_id"], dsn=test_dsn).enabled is False
    saved = save_settings(user["user_id"], TelemetrySettings(enabled=True), dsn=test_dsn)
    assert saved.enabled is True


def test_toggle_is_per_user(user, make_user, test_dsn):
    other = make_user()
    save_settings(user["user_id"], True, dsn=test_dsn)
    assert get_settings(user["user_id"], dsn=test_dsn).enabled is True
    assert get_settings(other["user_id"], dsn=test_dsn).enabled is False


def test_save_refuses_non_boolean_values(user, test_dsn):
    for bad in ("yes", "1", 1, 0, None):
        with pytest.raises(ValueError, match="must be a TelemetrySettings"):
            save_settings(user["user_id"], bad, dsn=test_dsn)
    with pytest.raises(ValueError, match="must be a boolean"):
        save_settings(user["user_id"], {"enabled": "yes"}, dsn=test_dsn)
    with pytest.raises(ValueError, match="need an 'enabled' boolean"):
        save_settings(user["user_id"], {}, dsn=test_dsn)
    assert get_settings(user["user_id"], dsn=test_dsn).enabled is False
