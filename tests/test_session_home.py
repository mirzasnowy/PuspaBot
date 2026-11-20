"""Home/profile resolution + live-system guard (G26).

All tests run against temporary homes; none of them touches the
real ``~/.puspa``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from puspa.sessions import (
    LiveHomeError,
    ProfileError,
    default_home,
    is_live_home,
    override_home,
    resolve_home,
    state_db_path,
)
from puspa.sessions.assembly import open_persistence
from puspa.sessions.home import HOME_ENV_VAR


def test_default_home_is_dot_puspa():
    assert default_home() == Path.home() / ".puspa"


def test_is_live_home_only_for_the_real_default(tmp_path):
    assert is_live_home(default_home())
    assert is_live_home(Path.home() / ".puspa")
    assert not is_live_home(tmp_path / ".puspa")
    assert not is_live_home(tmp_path)


def test_env_var_beats_default(monkeypatch, tmp_path):
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path / "custom"))
    assert resolve_home() == tmp_path / "custom"


def test_explicit_home_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path / "from-env"))
    assert resolve_home(home=tmp_path / "explicit") == tmp_path / "explicit"


def test_context_override_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path / "from-env"))
    with override_home(tmp_path / "override"):
        assert resolve_home() == tmp_path / "override"
    # Restored after the context exits.
    assert resolve_home() == tmp_path / "from-env"


def test_named_profile_layout(tmp_path):
    home = resolve_home("work", home=tmp_path)
    assert home == tmp_path / "profiles" / "work"
    assert state_db_path("work", home=tmp_path) == home / "state.db"


def test_two_profiles_do_not_share_a_database(tmp_path):
    assert state_db_path("a", home=tmp_path) != state_db_path("b", home=tmp_path)
    assert state_db_path(home=tmp_path) != state_db_path("a", home=tmp_path)


def test_invalid_profile_names_rejected(tmp_path):
    for bad in ("", "../escape", "has space", "a/b", "-lead", ".hidden"):
        with pytest.raises(ProfileError):
            resolve_home(bad, home=tmp_path)


def test_live_home_refused_without_opt_in(monkeypatch):
    monkeypatch.delenv(HOME_ENV_VAR, raising=False)
    monkeypatch.delenv("PUSPA_ALLOW_LIVE_HOME", raising=False)
    with pytest.raises(LiveHomeError):
        open_persistence(home=default_home())


def test_live_home_refused_with_only_one_flag(monkeypatch):
    monkeypatch.delenv(HOME_ENV_VAR, raising=False)
    # Env hatch alone, without the explicit argument, is not enough.
    monkeypatch.setenv("PUSPA_ALLOW_LIVE_HOME", "1")
    with pytest.raises(LiveHomeError):
        open_persistence(home=default_home())
    monkeypatch.delenv("PUSPA_ALLOW_LIVE_HOME", raising=False)


@pytest.mark.integration
def test_tmp_home_opens_fine(monkeypatch, tmp_path):
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path))
    with open_persistence() as gateway:
        session = gateway.create_session("hello")
        assert session["id"]
        assert (tmp_path / "state.db").exists()
