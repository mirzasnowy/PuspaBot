"""Fail-closed size limits for the public subagent lifecycle (G25).

Every piece of caller-supplied text is bounded before a child is
spawned. Oversized input is refused with
:class:`puspa.subagents.errors.LimitExceededError`, never silently
truncated at launch time: silent truncation would change what the
child was asked to do without the caller knowing.
"""

from __future__ import annotations

import json

from .errors import LimitExceededError

__all__ = [
    "MAX_CONTEXT_CHARS",
    "MAX_GOAL_CHARS",
    "MAX_METADATA_CHARS",
    "MAX_METADATA_KEYS",
    "MAX_RESULT_CHARS",
    "MAX_STEER_CHARS",
    "MAX_STEER_ITEMS",
    "validate_launch_sizes",
    "validate_steer_text",
]

#: Longest goal text a launch may carry.
MAX_GOAL_CHARS = 8_000
#: Longest context text a launch may carry.
MAX_CONTEXT_CHARS = 32_768
#: Longest canonical-JSON rendering of the launch metadata.
MAX_METADATA_CHARS = 4_096
#: Most top-level keys the launch metadata may carry.
MAX_METADATA_KEYS = 32
#: Longest result text kept by :func:`puspa.subagents.results.build_result`.
MAX_RESULT_CHARS = 32_768
#: Longest single steered text.
MAX_STEER_CHARS = 4_000
#: Most steered texts that may wait in one child's queue.
MAX_STEER_ITEMS = 16


def validate_launch_sizes(
    *,
    goal: str,
    context: str,
    metadata: dict | None,
) -> None:
    """Refuse oversized launch input before anything is spawned.

    ``goal`` and ``context`` must be strings within their caps.
    ``metadata`` must be a dict with at most ``MAX_METADATA_KEYS``
    top-level keys whose canonical JSON rendering fits in
    ``MAX_METADATA_CHARS``.
    """
    if not isinstance(goal, str) or not goal:
        raise LimitExceededError("goal must be a non-empty string")
    if len(goal) > MAX_GOAL_CHARS:
        raise LimitExceededError(f"goal is {len(goal)} characters; limit is {MAX_GOAL_CHARS}")
    if not isinstance(context, str):
        raise LimitExceededError("context must be a string")
    if len(context) > MAX_CONTEXT_CHARS:
        raise LimitExceededError(
            f"context is {len(context)} characters; limit is {MAX_CONTEXT_CHARS}"
        )
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        raise LimitExceededError("metadata must be a dict")
    if len(metadata) > MAX_METADATA_KEYS:
        raise LimitExceededError(f"metadata has {len(metadata)} keys; limit is {MAX_METADATA_KEYS}")
    rendered = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(rendered) > MAX_METADATA_CHARS:
        raise LimitExceededError(
            f"metadata renders to {len(rendered)} characters; limit is {MAX_METADATA_CHARS}"
        )


def validate_steer_text(text: str) -> None:
    """Refuse an empty or oversized steered text."""
    if not isinstance(text, str) or not text:
        raise LimitExceededError("steered text must be a non-empty string")
    if len(text) > MAX_STEER_CHARS:
        raise LimitExceededError(
            f"steered text is {len(text)} characters; limit is {MAX_STEER_CHARS}"
        )
