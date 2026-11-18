"""Per-turn todo list for the intercepted ``todo`` tool (G21).

HONEST LIMIT, stated up front: Puspa has NO durable todo store.
No table, no module, no schema anywhere in the repo keeps an
agent's todo list (checked before writing this module). This
store is therefore IN-MEMORY and scoped to one turn loop
instance, keyed by task id: it works inside a turn — add, list,
complete, remove — and it is forgotten when the process ends.
A durable todo store (a ``todos`` table next to the session
store, or a task-plan projection) is a follow-up; until it
lands, the interception returns real results for the live turn
and the ``todo`` tool's docs say the list does not survive a
restart. Nothing here pretends otherwise.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

__all__ = ["TodoItem", "TodoStore"]


@dataclass
class TodoItem:
    """One todo item."""

    item_id: str
    text: str
    done: bool = False

    def to_dict(self) -> dict:
        return {"id": self.item_id, "text": self.text, "done": self.done}


class TodoStore:
    """In-memory todo lists, one list per task id."""

    def __init__(self) -> None:
        self._lists: dict[str, list[TodoItem]] = {}
        self._counter = itertools.count(1)

    def add(self, task_id: str, text: str) -> TodoItem:
        """Append an item; raises ``ValueError`` for empty text."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("todo text must be a non-empty string")
        item = TodoItem(item_id=f"todo-{next(self._counter)}", text=text.strip())
        self._lists.setdefault(task_id, []).append(item)
        return item

    def list_items(self, task_id: str, *, open_only: bool = False) -> list[TodoItem]:
        """Items for a task, in order; ``open_only`` hides done items."""
        items = list(self._lists.get(task_id, []))
        if open_only:
            items = [item for item in items if not item.done]
        return items

    def complete(self, task_id: str, item_id: str) -> TodoItem | None:
        """Mark an item done; returns None when the id is unknown."""
        for item in self._lists.get(task_id, []):
            if item.item_id == item_id:
                item.done = True
                return item
        return None

    def remove(self, task_id: str, item_id: str) -> bool:
        """Remove an item; False when the id is unknown."""
        items = self._lists.get(task_id, [])
        for index, item in enumerate(items):
            if item.item_id == item_id:
                del items[index]
                return True
        return False

    def clear(self, task_id: str) -> int:
        """Drop a task's whole list; returns how many items were dropped."""
        return len(self._lists.pop(task_id, []))
