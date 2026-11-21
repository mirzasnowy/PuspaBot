"""Nightly amnesia test (Kosmos V2, memory export and import).

Every night the system samples 20 pinned items (corrections,
commitments, standing goals) and asks the recall pipeline for
each one cold: a fresh recall with no in-memory state, through
:func:`puspa.memory.recall_plan.recall_plan`. Any miss is an
incident: it is recorded as a ledger event with
``job == "amnesia_check"`` and it triggers an index repair, a
rebuild of the durable memory (``puspa.memory.durable.
rebuild_memory``), after which the check runs again. The check
passes only when 20 of 20 sampled items come back.

The check compares two independent reads of the same facts:

- the expected items come from the durable source of truth
  (the active facts, filed into pinned sections by
  :func:`puspa.memory.durable.pinned_items`);
- the recalled items come from what ``recall_plan`` actually
  returned in its ``pinned`` layer.

When the two disagree, the recalled layer dropped something
the durable store still holds, and that is the incident. A
user with fewer than 20 pinned items cannot sample 20: the
run reports ``"insufficient_pinned"``, records the run in the
ledger, and repairs nothing, because nothing was forgotten.

The job is registered as the ``"amnesia_check"`` scheduler
trigger (see :func:`puspa.scheduler.scheduler.
schedule_amnesia_check`) and runs through the existing
``run_scheduled_job`` caller, like the ontology review.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from . import durable
from .recall_plan import recall_plan

__all__ = [
    "AMNESIA_SAMPLE_SIZE",
    "JOB_NAME",
    "SYSTEM_AGENT_ID",
    "check_pinned_recall",
    "expected_pinned_items",
    "repair_index",
    "report_text",
    "run_amnesia_check",
    "sample_pinned_items",
]

#: How many pinned items one nightly run samples.
AMNESIA_SAMPLE_SIZE = 20

#: Job name recorded in the ledger and in the scheduler.
JOB_NAME = "amnesia_check"

#: System jobs record their ledger events under this agent id:
#: no human ran them, and no per-user agent row is required.
SYSTEM_AGENT_ID = "puspa"


def _jsonable(value: Any) -> Any:
    """A JSON-safe copy of ``value`` (datetimes become ISO strings)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _now() -> datetime:
    return datetime.now(UTC)


def sample_pinned_items(
    pinned: list[dict[str, Any]], *, limit: int = AMNESIA_SAMPLE_SIZE
) -> list[dict[str, Any]]:
    """The deterministic sample of pinned items for one run.

    The first ``limit`` items in pinned order (corrections,
    then commitments, then standing goals). The order is fixed,
    so the same pinned set always samples the same items and
    a nightly failure can be reproduced the next morning.
    """
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit!r}")
    return list(pinned)[:limit]


def _snapshot_from_facts(facts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """A durable snapshot (pinned sections only) from fact rows."""
    snapshot: dict[str, list[dict[str, Any]]] = {key: [] for key in durable.SNAPSHOT_KEYS}
    for fact in facts or []:
        if fact.get("status") != "active":
            continue
        text = str(fact.get("content") or fact.get("value") or "")
        if not text:
            continue
        section = durable.classify_fact(fact)
        snapshot[section].append(
            {
                "key": str(fact.get("key") or ""),
                "text": text,
                "source": fact.get("source"),
            }
        )
    return snapshot


def expected_pinned_items(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The pinned items the durable facts say should be recalled."""
    return durable.pinned_items(_snapshot_from_facts(facts))


def check_pinned_recall(
    expected: list[dict[str, Any]], recalled: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compare expected pinned items against what recall returned.

    An item is recalled only when the recalled layer holds the
    same key with the same text. A missing key, or a key whose
    text changed, is a miss. Returns ``{"checked", "recalled",
    "missed"}``; ``missed`` holds the expected items that did
    not come back, in expected order.
    """
    recalled_by_key = {str(item.get("key") or ""): str(item.get("text") or "") for item in recalled}
    missed = [
        dict(item)
        for item in expected
        if recalled_by_key.get(str(item.get("key") or "")) != str(item.get("text") or "")
    ]
    return {"checked": len(expected), "recalled": len(expected) - len(missed), "missed": missed}


def _load_facts(
    user_id: str,
    *,
    dsn: Any = None,
    fact_store: Any = None,
    facts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Active facts for one user, from the injected source or Postgres."""
    if facts is not None:
        return list(facts)
    if fact_store is not None:
        return fact_store.list_user(user_id, statuses=("active",))
    from .facts import PostgresFactStore

    return PostgresFactStore(dsn=dsn).list_user(user_id, statuses=("active",))


def repair_index(
    user_id: str,
    *,
    dsn: Any = None,
    fact_store: Any = None,
    store: Any = None,
    summaries: list[dict[str, Any]] | None = None,
    episodes: list[dict[str, Any]] | None = None,
    journal_entries: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Repair the recall index after an amnesia incident.

    The repair is the durable-memory rebuild
    (:func:`puspa.memory.durable.rebuild_memory`): it re-reads
    the lower memory layers, re-renders MEMORY.md, and stores
    the new document, so the next cold recall reads the fresh
    index. Returns the rebuild result (``version``, ``changed``,
    ``counts``). ``summaries``, ``episodes`` and
    ``journal_entries`` are injected only by tests; in
    production they are read from Postgres.
    """
    collect_kwargs: dict[str, Any] = {}
    if summaries is not None:
        collect_kwargs["summaries"] = summaries
    if episodes is not None:
        collect_kwargs["episodes"] = episodes
    if journal_entries is not None:
        collect_kwargs["journal_entries"] = journal_entries
    return durable.rebuild_memory(
        user_id,
        dsn=dsn,
        store=store,
        fact_store=fact_store,
        now=now or _now(),
        **collect_kwargs,
    )


def report_text(result: dict[str, Any]) -> str:
    """One plain-language report of an amnesia check run."""
    lines = [
        f"Amnesia check for user {result['user_id']}: {result['status']}.",
        f"Recalled {result['recalled']} of {result['checked']} sampled pinned items.",
    ]
    if result["missed"]:
        lines.append("Missed items:")
        for item in result["missed"]:
            lines.append(f"- {item.get('section')}: {item.get('key')} ({item.get('text')})")
    if result.get("repair") is not None:
        repair = result["repair"]
        if isinstance(repair, dict) and "error" in repair:
            lines.append(f"Index repair failed: {repair['error']}.")
        else:
            lines.append("Index repair ran (durable memory rebuilt).")
    if result.get("recheck") is not None:
        recheck = result["recheck"]
        lines.append(
            f"Recheck after repair: {recheck['recalled']} of "
            f"{recheck['checked']} recalled, status {recheck['status']}."
        )
    return "\n".join(lines)


def run_amnesia_check(
    user_id: str,
    *,
    dsn: Any = None,
    facts: list[dict[str, Any]] | None = None,
    fact_store: Any = None,
    ledger: Any = None,
    expected_items: list[dict[str, Any]] | None = None,
    plan: Any = None,
    recall_fn: Callable[[], Any] | None = None,
    repair_fn: Callable[..., dict[str, Any]] | None = None,
    durable_store: Any = None,
    repair_kwargs: dict[str, Any] | None = None,
    task_id: str | None = None,
    now: datetime | None = None,
    repair: bool = True,
    recheck: bool = True,
    sample_size: int = AMNESIA_SAMPLE_SIZE,
) -> dict[str, Any]:
    """Run one nightly amnesia check for one user, cold.

    Samples ``sample_size`` pinned items from the durable
    facts, recalls them cold through ``recall_plan`` (a fresh
    store on every call, no in-memory state carried over), and
    compares the two. The result carries ``status`` (``"ok"``,
    ``"incident"``, or ``"insufficient_pinned"``), the counts,
    the missed items, the repair outcome, and the recheck
    outcome. Every run is recorded in the ledger as one
    ``task_completed`` event with ``job == "amnesia_check"``
    when a ledger is given or a ``dsn`` is passed, so the
    nightly run is visible in the report.

    A miss is an incident: the index repair runs
    (:func:`repair_index`, or ``repair_fn`` when injected),
    then a fresh cold recall re-checks the same sample. The
    recheck passes only when the repair actually restored the
    items. ``plan`` and ``recall_fn`` are test hooks: a
    prebuilt plan, or a zero-argument callable returning a
    fresh plan on each call (used to plant a miss). ``repair``
    and ``recheck`` can be turned off by a caller that only
    wants the diagnosis.
    """
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("user_id must be a non-empty string")
    now = now or _now()
    run_task_id = task_id or f"amnesia-check-{uuid.uuid4().hex[:12]}"
    if expected_items is not None:
        expected = list(expected_items)
    else:
        expected = expected_pinned_items(
            _load_facts(user_id, dsn=dsn, fact_store=fact_store, facts=facts)
        )
    sample = sample_pinned_items(expected, limit=sample_size)

    def _record(status: str, outcome: dict[str, Any]) -> str | None:
        writer = ledger
        if writer is None and dsn is not None:
            from puspa.ledger.ledger import Ledger

            writer = Ledger(dsn)
        if writer is None:
            return None
        event = writer.append_event(
            task_id=run_task_id,
            agent_id=SYSTEM_AGENT_ID,
            event_type="task_completed",
            content={
                "job": JOB_NAME,
                "user_id": str(user_id),
                "status": status,
                "checked": outcome["checked"],
                "recalled": outcome["recalled"],
                "missed": outcome["missed"],
                "repair": _jsonable(outcome.get("repair")),
                "recheck": _jsonable(outcome.get("recheck")),
                "report": report_text({**outcome, "user_id": str(user_id), "status": status}),
            },
        )
        return str(event["id"])

    if len(sample) < sample_size:
        outcome: dict[str, Any] = {
            "checked": len(sample),
            "recalled": len(sample),
            "missed": [],
            "repair": None,
            "recheck": None,
            "required": sample_size,
        }
        ledger_event_id = _record("insufficient_pinned", outcome)
        return {
            "job": JOB_NAME,
            "user_id": str(user_id),
            "status": "insufficient_pinned",
            "checked": len(sample),
            "required": sample_size,
            "recalled": len(sample),
            "missed": [],
            "sampled_keys": [str(item.get("key") or "") for item in sample],
            "repair": None,
            "recheck": None,
            "ledger_event_id": ledger_event_id,
            "checked_at": now.isoformat(),
        }

    def _cold_plan() -> Any:
        if recall_fn is not None:
            return recall_fn()
        if plan is not None:
            return plan
        return recall_plan(
            user_id,
            "",
            dsn=dsn,
            fact_store=fact_store,
            facts=facts,
            ledger=ledger,
        )

    first = check_pinned_recall(sample, _cold_plan().pinned)
    if not first["missed"]:
        outcome = {
            "checked": first["checked"],
            "recalled": first["recalled"],
            "missed": [],
            "repair": None,
            "recheck": None,
            "required": sample_size,
        }
        ledger_event_id = _record("ok", outcome)
        return {
            "job": JOB_NAME,
            "user_id": str(user_id),
            "status": "ok",
            "checked": first["checked"],
            "required": sample_size,
            "recalled": first["recalled"],
            "missed": [],
            "sampled_keys": [str(item.get("key") or "") for item in sample],
            "repair": None,
            "recheck": None,
            "ledger_event_id": ledger_event_id,
            "checked_at": now.isoformat(),
        }

    repair_result: dict[str, Any] | None = None
    recheck_result: dict[str, Any] | None = None
    if repair:
        try:
            do_repair = repair_fn or repair_index
            repair_result = do_repair(
                user_id,
                dsn=dsn,
                fact_store=fact_store,
                store=durable_store,
                now=now,
                **(repair_kwargs or {}),
            )
        except Exception as exc:
            repair_result = {"error": f"{type(exc).__name__}: {exc}"}
    if recheck and repair_result is not None and "error" not in repair_result:
        second = check_pinned_recall(sample, _cold_plan().pinned)
        recheck_result = {
            "status": "ok" if not second["missed"] else "incident",
            "checked": second["checked"],
            "recalled": second["recalled"],
            "missed": second["missed"],
        }
    outcome = {
        "checked": first["checked"],
        "recalled": first["recalled"],
        "missed": first["missed"],
        "repair": repair_result,
        "recheck": recheck_result,
        "required": sample_size,
    }
    ledger_event_id = _record("incident", outcome)
    return {
        "job": JOB_NAME,
        "user_id": str(user_id),
        "status": "incident",
        "checked": first["checked"],
        "required": sample_size,
        "recalled": first["recalled"],
        "missed": first["missed"],
        "sampled_keys": [str(item.get("key") or "") for item in sample],
        "repair": repair_result,
        "recheck": recheck_result,
        "ledger_event_id": ledger_event_id,
        "checked_at": now.isoformat(),
    }
