"""Key-check: prove a backend can answer before calling it.

A decider backend that needs a credential must fail fast with
reason ``missing_api_key`` (or ``no_user_engine`` for the
chooser), not burn the 1.500 ms budget on a call that was doomed
from the start. The check lives apart from the call so the
decider — and any caller — can ask first.

Each backend implements ``check_key() -> (ok, reason)``; this
module is the single entry point that calls it and normalises
the answer into ``{"ok": bool, "reason": str | None}``. A
backend that raises inside its check is reported as not ready
instead of throwing: a key-check must never throw either.
"""

from __future__ import annotations

__all__ = ["check_backend"]


def check_backend(backend) -> dict:
    """Check one backend's credentials before calling it.

    Returns ``{"ok": True, "reason": None}`` when the backend is
    ready, else ``{"ok": False, "reason": <stable code>}``.
    Backends without a ``check_key`` method are treated as ready
    (a plain callable backend has nothing to check).
    """
    check = getattr(backend, "check_key", None)
    if check is None:
        return {"ok": True, "reason": None}
    try:
        ok, reason = check()
    except Exception as exc:  # noqa: BLE001 - key-check must not throw
        return {"ok": False,
                "reason": f"key_check_failed: {type(exc).__name__}: {exc}"}
    if ok:
        return {"ok": True, "reason": None}
    return {"ok": False, "reason": reason or "key_check_failed"}
