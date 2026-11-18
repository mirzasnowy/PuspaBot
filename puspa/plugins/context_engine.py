"""Context engine interface (G27 single-select kind).

A context engine builds the extra context text injected into a
turn for one user query. It is a single-select plugin kind: only
one context engine is active at a time, selected explicitly (see
``puspa.plugins.single_select``). This ABC is part of the
published plugin API, so a third-party plugin may subclass it
without importing any other Puspa core module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from puspa.memory.provider import validate_config

__all__ = ["ContextEngine"]


class ContextEngine(ABC):
    """Abstract context engine: one query in, context text out.

    ``build_context`` takes a user id and a free-text query and
    returns the context text to inject (empty string when the
    engine has nothing relevant). ``limit`` caps how many items
    the engine may use.
    """

    #: Registry name of this engine.
    name = "base"

    #: Config schema for this engine (same shape as the G24
    #: provider config schema: ``{"type": ..., "required": bool,
    #: "default": ...}``).
    config_schema: ClassVar[dict] = {}

    def __init__(self, config=None):
        self.config = validate_config(self.config_schema, config)

    @abstractmethod
    def build_context(self, user_id, query, *, limit=5):
        """Build context text for ``query``; return a string."""

    def close(self):
        """Release engine resources (no-op by default)."""
        return
