"""The decider log: every decision, hashed for audit and replay.

Each entry records the job, the question, the backend that
answered, the result, and a ``state_hash`` — the SHA-256 of the
canonical JSON of ``{job, question, backend, value/reason}``.
The same input, job, backend, and result always produce the
same hash, so a decision can be audited (was this input decided
this way before?) and replayed (does the same input still decide
the same way?) without trusting wall-clock time: the hash
covers no timestamp.

The log is in-memory only. Persisting it (for example into the
ledger) would need a new ledger event type, which is out of
scope for this item — see the G9 report.
"""

from __future__ import annotations

import hashlib
import json

__all__ = ["DecisionLog", "state_hash"]


def _canonical(value) -> str:
    """Canonical JSON: sorted keys, no whitespace, ASCII-safe."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def state_hash(
    *, job: str, question, backend: str | None, value=None, reason: str | None = None
) -> str:
    """The state hash of one decision.

    ``question`` is a question object (``to_dict()``) or None
    when the decision failed before a question was built.
    ``value`` is the interpreted result on success; ``reason``
    is the failure code otherwise.
    """
    payload = {
        "job": job,
        "question": question.to_dict() if question is not None else None,
        "backend": backend,
        "value": value,
        "reason": reason,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


class DecisionLog:
    """An append-only in-memory log of decisions.

    ``record`` takes the decision dict the decider returns plus
    the question it asked, computes the state hash, and stores
    the entry. ``find`` looks an entry up by its hash.
    """

    def __init__(self) -> None:
        self._entries: list[dict] = []
        self._by_hash: dict[str, dict] = {}

    def record(self, decision: dict, question=None) -> dict:
        digest = state_hash(
            job=decision["job"],
            question=question,
            backend=decision.get("backend"),
            value=decision.get("value"),
            reason=decision.get("reason"),
        )
        entry = {
            "seq": len(self._entries),
            "state_hash": digest,
            "job": decision["job"],
            "backend": decision.get("backend"),
            "ok": bool(decision.get("ok")),
            "value": decision.get("value"),
            "reason": decision.get("reason"),
            "latency_ms": decision.get("latency_ms"),
        }
        self._entries.append(entry)
        self._by_hash[digest] = entry
        return entry

    def find(self, digest: str) -> dict | None:
        """The entry with this state hash, or None."""
        return self._by_hash.get(digest)

    @property
    def entries(self) -> list[dict]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
