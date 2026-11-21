"""Permission tiers for desktop hands (V2-35).

V2-31 asked the user before every single action: safe, but
tiring, so a long task turned into a long queue of prompts.
These tiers replace that with three levels, decided once per
action:

- ``READ``: free. Snapshot, verify, list windows, locate text
  by OCR, open a web page, find text on a page. These change
  nothing, so they never ask.
- ``NORMAL``: clicks and ordinary input inside one app. The
  user allowlists an app once (see :mod:`puspa.computer.allowlist`)
  and the agent then clicks freely inside it; outside an
  allowlisted app the user is still asked, exactly like V2-31.
- ``DESTRUCTIVE``: always asks, even inside an allowlisted
  app. Clipboard write, drag, typing into a field that submits,
  a shortcut that submits (Enter/Return), and closing a window:
  anything that changes data outside the app, or throws data
  away.

The token check from V2-31 (:mod:`puspa.computer.gating`) is a
separate layer and still applies on top: a token without the
tool permission is refused whatever the tier says. Tiers only
decide whether the *user* is asked.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ACTION_TIERS", "Tier", "classify", "is_submit_shortcut"]


class Tier(StrEnum):
    """One permission tier for a computer action."""

    READ = "read"
    NORMAL = "normal"
    DESTRUCTIVE = "destructive"


#: The tier of every computer action, by its action name (the
#: same names ``DesktopHands.run_task`` steps and the MCP tools
#: use). Anything missing here is treated as NORMAL by
#: :func:`classify`: an unknown action must ask, never run free.
ACTION_TIERS: dict[str, Tier] = {
    # Reads: change nothing, never ask.
    "snapshot": Tier.READ,
    "verify": Tier.READ,
    "list_windows": Tier.READ,
    "visual_locate": Tier.READ,
    "web_open": Tier.READ,
    "web_find": Tier.READ,
    # Ordinary acts inside one app: free once the app is
    # allowlisted, otherwise ask.
    "click": Tier.NORMAL,
    "visual_click": Tier.NORMAL,
    "web_click": Tier.NORMAL,
    "type": Tier.NORMAL,
    "shortcut": Tier.NORMAL,
    "scroll": Tier.NORMAL,
    "focus_window": Tier.NORMAL,
    "switch_window": Tier.NORMAL,
    "open_app": Tier.NORMAL,
    # Clipboard read is a read, but it returns the user's own
    # data, so it stays NORMAL: it asks, like V2-33 did.
    "clipboard_read": Tier.NORMAL,
    # Always ask, even in an allowlisted app.
    "clipboard_write": Tier.DESTRUCTIVE,
    "drag": Tier.DESTRUCTIVE,
    "close_window": Tier.DESTRUCTIVE,
}

#: Keys that submit what is on screen (a form, a dialog, a
#: search box): a shortcut holding one of them is DESTRUCTIVE,
#: not NORMAL.
_SUBMIT_KEYS = frozenset({"enter", "return", "kp_enter"})


def is_submit_shortcut(keys: tuple[str, ...] | str) -> bool:
    """True when a keyboard chord submits (holds Enter/Return)."""
    if isinstance(keys, str):
        chord = tuple(part.strip() for part in keys.split("+") if part.strip())
    else:
        chord = tuple(keys)
    return any(key.lower() in _SUBMIT_KEYS for key in chord)


def classify(action: str, *, submits: bool = False) -> Tier:
    """The tier of one action.

    ``submits`` lifts typing and shortcuts to DESTRUCTIVE:
    typing into a field that submits, or pressing Enter, changes
    data outside the app. Unknown actions come back NORMAL, so
    they ask instead of running free.
    """
    if submits and action in ("type", "shortcut"):
        return Tier.DESTRUCTIVE
    return ACTION_TIERS.get(action, Tier.NORMAL)
