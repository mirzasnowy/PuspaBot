"""Idempotency keys for write-ahead step records (E-06).

One key identifies one attempted step exactly once:
``task_id + step number + hash of the step arguments``. The same
step retried with the same arguments carries the same key, so a
recovery pass can tell "this effect already happened" from "this
step never ran" without trusting any in-memory state.
"""

from __future__ import annotations

import hashlib

from puspa.ledger import canonical_json


def args_hash(args: dict) -> str:
    """Stable sha256 of the canonical JSON of ``args``.

    Falls back to a ``repr`` of the sorted items when the arguments
    are not JSON-serializable, so key computation never fails a
    step: a degraded key is still deterministic for one process.
    """
    try:
        payload = canonical_json(args)
    except (TypeError, ValueError):
        payload = repr(sorted(args.items(), key=lambda item: str(item[0])))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def idempotency_key(task_id: str, step_number: int, args: dict) -> str:
    """``"<task_id>:<step_number>:<args_hash>"`` for one step attempt."""
    return f"{task_id}:{step_number}:{args_hash(args)}"
