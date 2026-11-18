"""Builtin single-select plugin: simple context engine (G27).

A real (small) context engine plus the tool that feeds it. The
plugin owns an in-memory note store in ``ctx.shared``; the
``remember_note`` tool writes notes into it, and the ``simple``
context engine reads notes back by keyword overlap with the
query and formats them as context text. Tool and engine share
state only through the plugin's own context, never through core.

Registered as the ``context_engine`` single-select kind, so it
is the active engine only when explicitly selected.
"""

from __future__ import annotations

import re
from typing import ClassVar

from puspa.plugins.context_engine import ContextEngine

PLUGIN_ID = "simple_context"
PLUGIN_VERSION = "1.0.0"

#: Words too common to count as a keyword match.
STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "where",
        "what",
        "how",
        "when",
        "why",
        "who",
        "which",
        "that",
        "this",
        "it",
        "its",
    }
)


def _keywords(text):
    """Lowercase alphanumeric words, minus stopwords and short words."""
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) > 2 and word not in STOPWORDS
    }


class SimpleContextEngine(ContextEngine):
    """Keyword-overlap context engine over the plugin's notes."""

    name = "simple"

    config_schema: ClassVar[dict] = {
        "max_notes": {"type": "integer", "required": False, "default": 5},
    }

    def __init__(self, store, config=None):
        super().__init__(config=config)
        self._store = store

    def build_context(self, user_id, query, *, limit=5):
        """Return notes matching ``query``, best overlap first."""
        if not isinstance(query, str) or not query:
            return ""
        words = _keywords(query)
        if not words:
            return ""
        notes = self._store.get(user_id, [])
        scored = []
        for note in notes:
            overlap = len(words & _keywords(note))
            if overlap:
                scored.append((overlap, note))
        scored.sort(key=lambda item: -item[0])
        cap = min(limit, self.config.get("max_notes") or 5)
        picked = [note for _, note in scored[:cap]]
        if not picked:
            return ""
        lines = [f"- {note}" for note in picked]
        return "Notes:\n" + "\n".join(lines)


def register(ctx):
    """Register the note tool and the simple context engine."""
    store = ctx.shared.setdefault("notes", {})

    def remember_note(text, user_id="default"):
        store.setdefault(user_id, []).append(text)
        return f"noted ({len(store[user_id])} notes stored)"

    ctx.register_tool(
        "remember_note",
        "Store a short note for later context retrieval.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Note text."},
                "user_id": {
                    "type": "string",
                    "description": "Owner of the note.",
                },
            },
            "required": ["text"],
        },
        remember_note,
    )

    def factory(config=None):
        return SimpleContextEngine(store, config=config)

    ctx.register_context_engine("simple", factory, default=True)
