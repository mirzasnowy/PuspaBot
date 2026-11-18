"""Subprocess environment propagation for the Puspa home (G26).

The failure mode: a spawner launches a child process without
forwarding ``PUSPA_HOME`` (or the context-local override, which
never crosses a process boundary), and the child silently falls
back to the default profile (``~/.puspa``) — writing session state
into the wrong profile.

Rule, enforced here: spawners build the child's environment with
:func:`child_env`, which always sets ``PUSPA_HOME`` explicitly to
the resolved home, and code that receives an environment for a
child checks it with :func:`require_home_in_env` before spawning.
"""

from __future__ import annotations

import os

from .errors import ProfileError
from .home import HOME_ENV_VAR, resolve_home


def child_env(*, profile=None, home=None, base_env=None):
    """Build a child-process environment with ``PUSPA_HOME`` set.

    ``base_env`` defaults to the current process environment. The
    resolved home (explicit ``home``, named ``profile``, or the
    current resolution) is always written into ``PUSPA_HOME`` — the
    child never has to guess, and it can never fall back to the
    default profile by accident.
    """
    env = dict(os.environ if base_env is None else base_env)
    env[HOME_ENV_VAR] = str(resolve_home(profile, home=home))
    return env


def require_home_in_env(env):
    """Return the ``PUSPA_HOME`` from ``env``, or raise.

    Call this on the environment a spawner is about to hand to a
    child process. A missing value means the spawner forgot to
    propagate the home, and the spawn must not proceed.
    """
    value = (env or {}).get(HOME_ENV_VAR)
    if not value:
        raise ProfileError(
            f"child environment is missing {HOME_ENV_VAR}: the spawner "
            "must propagate the Puspa home explicitly (see child_env)"
        )
    return value


def home_from_env(env):
    """Resolve the home a child process would see from ``env``.

    Returns None when ``PUSPA_HOME`` is absent (the child would fall
    back to the default profile — the failure this module prevents).
    """
    value = (env or {}).get(HOME_ENV_VAR)
    return value or None
