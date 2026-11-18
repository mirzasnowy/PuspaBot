"""Authoritative Puspa home and profile resolution (G26).

Precedence, highest first (mirrors the hermes-agent pattern, rewritten
for Puspa):

1. An explicit ``home`` argument to the call.
2. A context-local override (``override_home()``) — per-task, so a
   worker thread or async task can pin its own home without touching
   process-global state.
3. The ``PUSPA_HOME`` environment variable.
4. The platform default: ``~/.puspa``.

A named profile lives at ``<root>/profiles/<name>/`` with its own
``state.db``, config, and log files, so two profiles never share a
database file.

Live-system guard: the real default home (``~/.puspa``) is the live
system. Code that resolves to it without an explicit opt-in raises
:class:`LiveHomeError`. Tests point ``PUSPA_HOME`` at a temporary
directory instead; the escape hatch ``PUSPA_ALLOW_LIVE_HOME=1`` plus
an explicit ``allow_live_home=True`` argument is the only way to
touch the live home on purpose.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .errors import LiveHomeError, ProfileError

#: Environment variable naming the Puspa home directory.
HOME_ENV_VAR = "PUSPA_HOME"

#: Escape hatch: set to "1" to allow intentional live-home access.
ALLOW_LIVE_HOME_ENV_VAR = "PUSPA_ALLOW_LIVE_HOME"

#: Directory name of the default home inside the user home.
DEFAULT_HOME_NAME = ".puspa"

#: Subdirectory holding named profiles under a home root.
PROFILES_DIR_NAME = "profiles"

#: File name of the per-profile session database.
STATE_DB_NAME = "state.db"

#: Profile names: start alphanumeric, then letters, digits, "-" or "_".
_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

#: Context-local home override (per task/thread, never process-global).
_home_override: ContextVar[Path | None] = ContextVar("puspa_home_override", default=None)


@contextmanager
def override_home(path):
    """Temporarily pin the Puspa home for the current context.

    The previous override (if any) is restored on exit. This is the
    mechanism a worker uses to run against its own home without
    mutating ``os.environ`` for the whole process.
    """
    token = _home_override.set(Path(path).expanduser())
    try:
        yield Path(path).expanduser()
    finally:
        _home_override.reset(token)


def default_home() -> Path:
    """The platform default home: ``~/.puspa``."""
    return Path.home() / DEFAULT_HOME_NAME


def is_live_home(path) -> bool:
    """True when ``path`` is the real live home (``~/.puspa``)."""
    try:
        return Path(path).expanduser().resolve() == default_home().resolve()
    except (OSError, RuntimeError):
        return False


def validate_profile_name(name: str) -> str:
    """Validate a profile name; returns it unchanged or raises."""
    if not isinstance(name, str) or not _PROFILE_RE.match(name):
        raise ProfileError(
            "profile name must start with a letter or digit and contain only "
            f"letters, digits, '-' or '_' (got {name!r})"
        )
    return name


def resolve_home(profile=None, *, home=None) -> Path:
    """Resolve the authoritative home directory for a profile.

    Precedence: explicit ``home`` argument, then the context-local
    override, then ``PUSPA_HOME``, then the ``~/.puspa`` default.
    With ``profile`` given, the result is
    ``<root>/profiles/<profile>``.
    """
    if home is not None:
        root = Path(home).expanduser()
    else:
        override = _home_override.get()
        if override is not None:
            root = override
        elif os.environ.get(HOME_ENV_VAR):
            root = Path(os.environ[HOME_ENV_VAR]).expanduser()
        else:
            root = default_home()
    if profile is None:
        return root
    return root / PROFILES_DIR_NAME / validate_profile_name(profile)


def state_db_path(profile=None, *, home=None) -> Path:
    """Path of the per-profile session database file."""
    return resolve_home(profile, home=home) / STATE_DB_NAME


def guard_not_live(path, *, allow_live_home=False) -> None:
    """Raise :class:`LiveHomeError` if ``path`` is the live home.

    The live home is only touchable when the caller explicitly opts
    in (``allow_live_home=True``) *and* the environment escape hatch
    ``PUSPA_ALLOW_LIVE_HOME=1`` is set. One flag alone is not enough:
    a stray ``allow_live_home=True`` in test code, or a stray env var
    in a shell, must not silently write the real home.
    """
    if not is_live_home(path):
        return
    if allow_live_home and os.environ.get(ALLOW_LIVE_HOME_ENV_VAR) == "1":
        return
    raise LiveHomeError(
        "refusing to touch the live Puspa home "
        f"({default_home()}); point PUSPA_HOME at a temporary directory, "
        "or pass allow_live_home=True with PUSPA_ALLOW_LIVE_HOME=1"
    )
