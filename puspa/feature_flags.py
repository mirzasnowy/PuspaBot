"""Central feature flags (E-21, plan section 9.6).

One file, one source of truth for every feature flag in Puspa.
Before E-21 the only flag lived inside
:mod:`puspa.marketplace.discovery` (the skills.sh install flag from
E-12, plan 6.7); that flag now lives here too, and the discovery
module delegates to it, so there is exactly one place that decides
what is on.

Flags
-----
- ``marketplace`` — installable skills/modules through the G3
  registry (import a skill folder, install/enable a skill).
- ``discovery`` — searching the skills.sh registry and installing
  a skill found there. Searching never installs anything; the flag
  gates the search itself, so a disabled discovery surface cannot
  even list third-party skills.
- ``workflow`` — user-built workflows: create, activate, run, tick
  (the scheduler pass), and execute an approved action.
- ``teams`` — multi-tenant teams: create a team, add a member,
  issue or accept an invitation.
- ``payment_execution`` — moving money: the billing checkout route
  and the payment execution endpoint. Preparation (``prepare``)
  is not execution and stays available; execution has no executor
  in this codebase at all (see ``puspa.payments.prepare``), so the
  flag is a second layer on top of the always-refuse design.
- ``skill_install`` — installing a skill discovered via skills.sh
  (E-12, plan 6.7). Off by default; only built-in skills and the
  self-signed allowlist may install while it is off.

How a flag is read
------------------
An explicit per-flag environment variable wins over everything.
Otherwise the profile (``PUSPA_PROFILE``) applies: the ``beta``
profile turns the five beta-frozen surfaces off. Otherwise the
built-in default applies: the five surfaces are on, ``skill_install``
is off. The value is read on every call (nothing is cached), so an
operator or a test can flip a flag without a restart.

The beta profile is the deploy-time default for the limited beta
(see ``config/beta.env``): marketplace, discovery, workflow, teams,
and payment execution are all off. Turning one back on is an
explicit operator act — set its variable to ``1`` — never a code
change.

What "off" means
----------------
Off blocks creation and execution at the entry point, before any
database write, network call, or provider call: the gated function
raises :class:`FeatureDisabledError` (or, for the plain-function
billing routes, returns a 403 ``feature_disabled`` response). Off
never blocks reads (listing, getting, quoting a price) and never
blocks cleanup (deleting a workflow, revoking an invitation,
rolling a skill install back). A disabled surface is unreachable,
not just hidden from a menu.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Profile environment variable. ``beta`` applies the beta defaults.
PROFILE_ENV_VAR = "PUSPA_PROFILE"

#: The one profile name the code knows. Anything else is rejected
#: loudly instead of silently behaving like the default profile.
BETA_PROFILE = "beta"

FLAG_MARKETPLACE = "marketplace"
FLAG_DISCOVERY = "discovery"
FLAG_WORKFLOW = "workflow"
FLAG_TEAMS = "teams"
FLAG_PAYMENT_EXECUTION = "payment_execution"
FLAG_SKILL_INSTALL = "skill_install"

#: Every flag and the environment variable that turns it on or off.
#: ``skill_install`` keeps the E-12 variable name
#: (``PUSPA_SKILL_INSTALL_ENABLED``) so existing operators and
#: tests keep working; the other five share one naming shape.
FLAG_ENV_VARS: dict[str, str] = {
    FLAG_MARKETPLACE: "PUSPA_FLAG_MARKETPLACE",
    FLAG_DISCOVERY: "PUSPA_FLAG_DISCOVERY",
    FLAG_WORKFLOW: "PUSPA_FLAG_WORKFLOW",
    FLAG_TEAMS: "PUSPA_FLAG_TEAMS",
    FLAG_PAYMENT_EXECUTION: "PUSPA_FLAG_PAYMENT_EXECUTION",
    FLAG_SKILL_INSTALL: "PUSPA_SKILL_INSTALL_ENABLED",
}

#: Built-in defaults (no profile, no explicit variable): the five
#: beta-frozen surfaces are on for development; skills.sh installs
#: stay off (E-12, plan 6.7).
DEFAULTS: dict[str, bool] = {
    FLAG_MARKETPLACE: True,
    FLAG_DISCOVERY: True,
    FLAG_WORKFLOW: True,
    FLAG_TEAMS: True,
    FLAG_PAYMENT_EXECUTION: True,
    FLAG_SKILL_INSTALL: False,
}

#: Beta defaults (``PUSPA_PROFILE=beta``): every frozen surface off.
#: The beta build ships with these off; nothing here can turn a
#: surface on by accident, because only an explicit per-flag
#: variable overrides the profile.
BETA_DEFAULTS: dict[str, bool] = {
    FLAG_MARKETPLACE: False,
    FLAG_DISCOVERY: False,
    FLAG_WORKFLOW: False,
    FLAG_TEAMS: False,
    FLAG_PAYMENT_EXECUTION: False,
    FLAG_SKILL_INSTALL: False,
}

#: What counts as "on" in an environment variable. Anything else
#: (including an empty string) counts as off, matching the E-12
#: behaviour: a flag is on only when it is said on, plainly.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class FeatureDisabledError(ValueError):
    """A gated surface was called while its feature flag is off.

    Carries the flag name so callers (and HTTP handlers) can report
    exactly which surface is disabled instead of a bare refusal.
    """

    def __init__(self, flag: str) -> None:
        self.flag = flag
        super().__init__(
            f"feature {flag!r} is disabled by the feature flag "
            f"({FLAG_ENV_VARS[flag]}); enable it explicitly to use this surface"
        )


def beta_profile() -> dict[str, bool]:
    """The beta default for every flag (a copy; mutate freely)."""
    return dict(BETA_DEFAULTS)


def _profile_defaults(env: Mapping[str, str]) -> dict[str, bool]:
    """Defaults for the profile named in ``env`` (or the built-ins)."""
    profile = (env.get(PROFILE_ENV_VAR) or "").strip().lower()
    if not profile:
        return dict(DEFAULTS)
    if profile == BETA_PROFILE:
        return dict(BETA_DEFAULTS)
    raise ValueError(
        f"unknown {PROFILE_ENV_VAR} value {profile!r}; expected {BETA_PROFILE!r} or unset"
    )


def is_enabled(name: str, *, env: Mapping[str, str] | None = None) -> bool:
    """True when the flag ``name`` is on.

    Resolution, in order: an explicit per-flag variable, the active
    profile's default, the built-in default. ``env`` defaults to the
    process environment; pass a dict in tests to avoid touching it.
    """
    if name not in FLAG_ENV_VARS:
        raise ValueError(f"unknown feature flag {name!r}; known flags: {sorted(FLAG_ENV_VARS)}")
    source = os.environ if env is None else env
    raw = source.get(FLAG_ENV_VARS[name])
    if raw is None:
        return _profile_defaults(source)[name]
    return raw.strip().lower() in _TRUTHY


def require_enabled(name: str, *, env: Mapping[str, str] | None = None) -> None:
    """Raise :class:`FeatureDisabledError` unless flag ``name`` is on.

    Gate entry points with this as their first statement, before any
    database write, network call, or provider call.
    """
    if not is_enabled(name, env=env):
        raise FeatureDisabledError(name)


def enabled_flags(*, env: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Every flag and whether it is on (for status pages and tests)."""
    return {name: is_enabled(name, env=env) for name in FLAG_ENV_VARS}


def describe(*, env: Mapping[str, str] | None = None) -> dict[str, dict[str, object]]:
    """Every flag with its state, variable, and default.

    ``source`` is ``explicit`` when the per-flag variable is set,
    ``profile`` when the profile decided, and ``default`` otherwise.
    """
    source = os.environ if env is None else env
    profile = (source.get(PROFILE_ENV_VAR) or "").strip().lower()
    out: dict[str, dict[str, object]] = {}
    for name, var in FLAG_ENV_VARS.items():
        if source.get(var) is not None:
            origin = "explicit"
        elif profile:
            origin = "profile"
        else:
            origin = "default"
        out[name] = {
            "enabled": is_enabled(name, env=source),
            "env_var": var,
            "default": DEFAULTS[name],
            "beta_default": BETA_DEFAULTS[name],
            "source": origin,
        }
    return out
