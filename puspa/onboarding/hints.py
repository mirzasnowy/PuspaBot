"""Contextual first-run hints for the PWA (G28).

A hint is a short, one-time nudge shown exactly when the user first
reaches the behaviour branch it belongs to -- never as a long upfront
tour. Each hint is shown once per install: once the user has seen it
(or dismissed it), its flag is stored in Postgres
(``onboarding_hint_seen``, one row per user per flag) and it is never
shown again for that user.

Hints never contain secrets and never trigger outbound actions: they
are plain text plus the name of the screen they point at. The client
reports which branch the user just reached (``context``); the server
only decides which unseen hints that branch makes due.

Trigger branches (``context`` keys, all optional):

- ``screen`` + ``first_visit``: the screen the user just opened for
  the first time this install (e.g. ``"modules"``).
- ``approval_requested``: the user just received their first
  approval request.
- ``memory_used``: the user just used memory for the first time
  (a fact was read back or saved).
- ``task_cost_idr``: the cost of the task that just finished, in
  Rupiah; the hint fires when the task reached the per-task cost
  ceiling (``TASK_COST_CEILING_IDR``).
- ``billing_rows``: how many usage-ledger rows the user has so far;
  the hint fires on the first recorded row.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from puspa.migrations import apply_migrations

from ..billing.config import TASK_COST_CEILING_IDR
from ..i18n import DEFAULT_LOCALE
from ..i18n.server_lines import render as render_server_line
from ..memory.schema import resolve_dsn


@dataclass(frozen=True)
class Hint:
    """One contextual hint: its flag, trigger branch, copy, target."""

    flag: str
    trigger: str
    title: str
    body: str
    screen: str
    action_label: str


def _format_rupiah(amount_idr: int) -> str:
    """Format an amount as Indonesian Rupiah (``Rp2.000``)."""
    return f"Rp{amount_idr:,}".replace(",", ".")


#: The full hint registry, in the order hints are presented when
#: several become due at once. Keep this small on purpose: a hint
#: earns its place only by being tied to a real behaviour branch.
#:
#: Copy comes from server-lines (``puspa.i18n``), in Indonesian —
#: the server never mixes English into the Indonesian UI (G18).
#: Only the flag, trigger and screen are code; every sentence the
#: user reads lives in ``puspa/i18n/locales/id.json``.
HINTS: tuple[Hint, ...] = (
    Hint(
        flag="first_modules_visit",
        trigger="first_modules_visit",
        title=render_server_line("hint.first_modules_visit.title"),
        body=render_server_line("hint.first_modules_visit.body"),
        screen="modules",
        action_label=render_server_line("hint.first_modules_visit.action_label"),
    ),
    Hint(
        flag="first_approval",
        trigger="first_approval",
        title=render_server_line("hint.first_approval.title"),
        body=render_server_line("hint.first_approval.body"),
        screen="tasks",
        action_label=render_server_line("hint.first_approval.action_label"),
    ),
    Hint(
        flag="first_memory_use",
        trigger="first_memory_use",
        title=render_server_line("hint.first_memory_use.title"),
        body=render_server_line("hint.first_memory_use.body"),
        screen="memory",
        action_label=render_server_line("hint.first_memory_use.action_label"),
    ),
    Hint(
        flag="first_costly_task",
        trigger="first_costly_task",
        title=render_server_line("hint.first_costly_task.title"),
        body=render_server_line(
            "hint.first_costly_task.body",
            ceiling=_format_rupiah(TASK_COST_CEILING_IDR),
        ),
        screen="insights",
        action_label=render_server_line("hint.first_costly_task.action_label"),
    ),
    Hint(
        flag="first_billing_row",
        trigger="first_billing_row",
        title=render_server_line("hint.first_billing_row.title"),
        body=render_server_line("hint.first_billing_row.body"),
        screen="insights",
        action_label=render_server_line("hint.first_billing_row.action_label"),
    ),
)

HINT_FLAGS = tuple(hint.flag for hint in HINTS)


def hints_in_locale(locale: str = DEFAULT_LOCALE) -> tuple[Hint, ...]:
    """The hint registry rendered in ``locale``.

    The import-time :data:`HINTS` is Indonesian; this rebuilds the
    same flags, triggers and screens with every sentence rendered
    in ``locale``. A key the locale is missing falls back to the
    Indonesian sentence for that key (recorded by the renderer) —
    never a blank, never a silently wrong sentence.
    """
    return tuple(
        Hint(
            flag=hint.flag,
            trigger=hint.trigger,
            title=render_server_line(f"hint.{hint.flag}.title", locale=locale),
            body=render_server_line(
                f"hint.{hint.flag}.body",
                locale=locale,
                **(
                    {"ceiling": _format_rupiah(TASK_COST_CEILING_IDR)}
                    if hint.flag == "first_costly_task"
                    else {}
                ),
            ),
            screen=hint.screen,
            action_label=render_server_line(f"hint.{hint.flag}.action_label", locale=locale),
        )
        for hint in HINTS
    )


#: Cost threshold for the costly-task hint: the per-task ceiling.
#: A task that reaches it is, by definition, an expensive task.
COSTLY_TASK_THRESHOLD_IDR = TASK_COST_CEILING_IDR


def hint_by_flag(flag: str) -> Hint | None:
    """The registered hint for ``flag``, or None when unknown."""
    for hint in HINTS:
        if hint.flag == flag:
            return hint
    return None


def hint_to_dict(hint: Hint) -> dict:
    """Public wire shape of a hint (text only, no secrets)."""
    return {
        "flag": hint.flag,
        "trigger": hint.trigger,
        "title": hint.title,
        "body": hint.body,
        "screen": hint.screen,
        "action_label": hint.action_label,
    }


def hint_triggered(hint: Hint, context: dict | None) -> bool:
    """Pure trigger check: does this ``context`` reach the hint's branch?

    ``context`` is the client-reported branch facts for the moment
    (see the module docstring). Missing keys mean "branch not
    reached"; unknown keys are ignored.
    """
    ctx = context or {}
    if hint.trigger == "first_modules_visit":
        return ctx.get("screen") == "modules" and bool(ctx.get("first_visit"))
    if hint.trigger == "first_approval":
        return bool(ctx.get("approval_requested"))
    if hint.trigger == "first_memory_use":
        return bool(ctx.get("memory_used"))
    if hint.trigger == "first_costly_task":
        cost = ctx.get("task_cost_idr")
        return isinstance(cost, (int, float)) and cost >= COSTLY_TASK_THRESHOLD_IDR
    if hint.trigger == "first_billing_row":
        rows = ctx.get("billing_rows")
        return isinstance(rows, (int, float)) and rows >= 1
    return False


def create_schema(dsn=None) -> None:
    """Apply the ``onboarding_hint_seen`` table (migration 0017_onboarding_hints)."""
    apply_migrations(resolve_dsn(dsn), target="0017")


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def seen_flags(user_id, dsn=None) -> set[str]:
    """Flags this user has already seen (empty set for a new user)."""
    create_schema(dsn)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT flag FROM onboarding_hint_seen WHERE user_id = %s",
            (user_id,),
        )
        return {row["flag"] for row in cur.fetchall()}


def mark_seen(user_id, flag, dsn=None) -> dict:
    """Record that the user has seen (or dismissed) a hint.

    Idempotent: marking twice is a no-op. Raises ValueError for an
    unknown flag, so a typo can never create a phantom flag.
    """
    if hint_by_flag(flag) is None:
        raise ValueError(f"unknown hint flag: {flag!r}")
    create_schema(dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO onboarding_hint_seen (user_id, flag) VALUES (%s, %s) "
                "ON CONFLICT (user_id, flag) DO NOTHING",
                (user_id, flag),
            )
        conn.commit()
    return {"user_id": str(user_id), "flag": flag, "seen": True}


def due_hints(user_id, context=None, dsn=None, locale: str = DEFAULT_LOCALE) -> list[dict]:
    """Hints due right now: triggered by ``context`` and not yet seen.

    Returned in registry order, rendered in ``locale`` (Indonesian
    when not given). A hint whose branch was never reached is never
    due, and a seen hint is never due again.
    """
    seen = seen_flags(user_id, dsn=dsn)
    hints = HINTS if locale == DEFAULT_LOCALE else hints_in_locale(locale)
    return [
        hint_to_dict(hint)
        for hint in hints
        if hint.flag not in seen and hint_triggered(hint, context)
    ]
