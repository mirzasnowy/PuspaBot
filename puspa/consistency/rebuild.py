"""Rebuild session transcripts from the Postgres ledger (E-06).

``state.db`` holds session transcripts and the search index, and
it must be rebuildable: when the SQLite file is lost or corrupt,
the ledger still holds the ``user_message`` / ``agent_message``
events, so transcripts can be rebuilt from it. Rebuilt sessions
are marked in their metadata (``recovered_from_ledger``) and keep
the ledger task id, so a second rebuild is a no-op instead of a
duplicate.

Honest limit: the ledger records what the agent did, not the full
text of every turn in every channel, so a rebuilt transcript is a
partial record — it is marked as recovered, never presented as
the original.
"""

from __future__ import annotations

from datetime import UTC, datetime

from puspa.ledger import Ledger, canonical_json


def _message_text(content: dict) -> str:
    """Best text for one ledger message event."""
    text = content.get("text")
    if isinstance(text, str) and text:
        return text
    raw = content.get("content")
    if isinstance(raw, str) and raw:
        return raw
    return canonical_json(content)


def rebuild_sessions_from_ledger(
    ledger: Ledger,
    store,
    *,
    task_ids: list[str] | None = None,
    since: datetime | None = None,
) -> dict:
    """Rebuild missing sessions from ledger message events.

    ``store`` is a writable ``SessionStore``. Only tasks with at
    least one ``user_message`` / ``agent_message`` event produce
    a session, and tasks already rebuilt (a session whose
    metadata carries the same ``ledger_task_id``) are skipped.
    Returns ``{"rebuilt": [...], "messages": int, "skipped": [...]}``.
    """
    start = since or datetime(1970, 1, 1, tzinfo=UTC)
    events = ledger.recent_events(start, limit=100000)
    grouped: dict[str, list[tuple[str, str]]] = {}
    for event in events:
        if event["type"] not in ("user_message", "agent_message"):
            continue
        task_id = event["task_id"]
        if not task_id:
            continue
        if task_ids is not None and task_id not in task_ids:
            continue
        role = "user" if event["type"] == "user_message" else "assistant"
        grouped.setdefault(task_id, []).append((role, _message_text(event["content"])))
    already = set()
    for session in store.list_sessions(limit=10000):
        ledger_task_id = (session.get("metadata") or {}).get("ledger_task_id")
        if ledger_task_id:
            already.add(ledger_task_id)
    rebuilt: list[str] = []
    skipped: list[str] = []
    messages = 0
    for task_id, turns in grouped.items():
        if task_id in already:
            skipped.append(task_id)
            continue
        session = store.create_session(
            title=f"Recovered task {task_id[:8]}",
            metadata={"recovered_from_ledger": True, "ledger_task_id": task_id},
        )
        store.append_messages(session["id"], turns)
        rebuilt.append(task_id)
        messages += len(turns)
    if rebuilt:
        store.rebuild_fts()
    return {"rebuilt": rebuilt, "messages": messages, "skipped": skipped}
