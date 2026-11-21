"""Automatic context compression for a session (AST-03, issue #79).

Pattern studied, then rewritten as Puspa's own code (see REUSE.md):
AstrBot summarizes old turns automatically when the context grows,
so the model keeps working with a bounded window instead of failing
or forgetting the start of the conversation. Only the PATTERN is
taken from AstrBot (AGPL-3.0): no AstrBot code is copied, AstrBot is
not a dependency, and every line below is written for Puspa's own
session shape.

What G19 already built, and what this module adds: G19
(``puspa/runtime/context_compression.py``) built the compressor
itself — two thresholds, a protected tail, tool pairs never split,
a deterministic extractive fallback — and G26
(``puspa/sessions/lineage.py``) built the adoption of a compression
into session lineage. But nothing called either of them on its own:
the compressor only ran when a caller remembered to call
``prepare_turn_messages``. This module is the automatic trigger that
was missing. A Puspa-owned turn wrapper calls it, and it decides by
itself whether the session needs compressing:

- ``append_turn`` appends one turn to a session, then checks the
  thresholds. This is the call for the turn loop: every turn goes
  through it, so compression fires on its own when the context
  grows, never because someone remembered to ask.
- ``maybe_compress`` checks one session right now and compresses
  it when a threshold is hit, without appending anything.
- ``build_model_messages`` loads a session and returns the message
  list the model call should use — compressed when a threshold is
  hit, the original turns otherwise. This is the pre-request call
  site G19's docstring asked for.

Threshold (one measurable number, token-based): the session's
messages are counted in tokens (tiktoken ``cl100k_base`` through
``puspa/router/compression.py``, with the chars/4 estimate when
tiktoken is unavailable) against ``context_limit`` (default
128_000, the same default as the G19 compressor). At 50% of the
limit the preflight threshold fires; at 85% the gateway threshold
fires and keeps only half as many tail messages. ``protect_last_n``
(default 20) tail messages always stay whole.

What compression does, and what it never does:

- The oldest turns are replaced in the LIVE context by one dense
  summary message; the newest turns stay whole. The summary keeps
  names, numbers, dates, decisions, and promises (the orphan-number
  rule from ``puspa/router/compression.py``: a number the kept tail
  may refer to must survive in the summary).
- Nothing is deleted without a trace. The parent session keeps
  every original turn exactly as stored; the summary is recorded
  as a child session linked by a ``compression`` lineage edge, so
  the summary (or the original turns) are always there to read.
  A compression that produces the same summary as the latest
  recorded one reuses that child instead of birthing a duplicate.
- The summary is deterministic by default: the extractive fallback
  from G19 (digit-bearing lines kept whole, no model call, no key,
  no network). A model-based summarizer can be injected through
  ``summarize=``; that is a follow-up, not the default.

Honest limits:

- This module is not wired into nanobot's ``AgentLoop``: G19
  established that the loop builds its own transcript and the
  hook only sees a deep-copied snapshot, so no hook can compress
  the live run. The wiring is the caller: a Puspa-owned turn
  wrapper calls ``append_turn`` / ``build_model_messages`` before
  the model request. Until that wrapper exists, this module runs
  only where a caller drives it (and in these tests).
- The summary is extractive, not a model summary: it keeps the
  fact-carrying lines and drops small talk, but it does not
  rephrase or reason about what matters. A model summarizer
  injected through ``summarize=`` replaces it.
- ``maybe_compress`` re-reads the parent session every call, so
  calling it twice on the same uncompressed parent re-summarizes
  the same middle. Callers keep the returned messages as the live
  context instead of re-reading the parent.
"""

from __future__ import annotations

from puspa.runtime.context_compression import (
    ContextCompressor,
    extractive_summary,
    prepare_turn_messages,
)
from puspa.sessions.lineage import adopt_compression_result, find_summary_message

__all__ = [
    "AutoCompressor",
    "maybe_compress_session",
]

#: Keys a stored session message carries that are not part of the
#: chat shape the model sees. They are stripped before the messages
#: reach the compressor, so the live context is plain
#: role/content (plus tool_calls/tool_call_id when present).
_STORE_ONLY_KEYS = ("id", "seq", "created_at")


def _to_chat_message(message: dict) -> dict:
    """A stored session message as a plain chat message."""
    chat = {"role": message.get("role"), "content": message.get("content", "")}
    for key in ("tool_calls", "tool_call_id"):
        if message.get(key) is not None:
            chat[key] = message[key]
    return chat


def _reason_text(stats: dict) -> str:
    """One-line reason stored on the lineage edge."""
    return (
        "auto context compression "
        f"({stats.get('mode', 'preflight')}, "
        f"{stats.get('tokens_before', 0)} -> {stats.get('tokens_after', 0)} tokens, "
        f"ratio {stats.get('compression_ratio', 1.0)})"
    )


class AutoCompressor:
    """Compress a session's turns automatically when context grows.

    ``gateway`` is a session gateway (``puspa.sessions``) — the
    caller opens it; this class never opens a database itself.
    ``summarize`` is an injected ``summarize(text) -> str`` callable;
    None means the deterministic extractive summary (no live model).
    ``flush``, ``user_id`` and ``dsn`` are passed through to the
    G19 compressor (flush memory to disk before summarizing).
    ``token_counter`` is an injected ``text -> int`` callable for
    tests; the default is the shared token counter.
    """

    def __init__(
        self,
        gateway,
        *,
        context_limit: int = 128_000,
        protect_last_n: int = 20,
        summarize=None,
        flush=None,
        user_id=None,
        dsn=None,
        token_counter=None,
    ) -> None:
        self.gateway = gateway
        self.context_limit = context_limit
        self.protect_last_n = protect_last_n
        self._compressor = ContextCompressor(
            context_limit=context_limit,
            protect_last_n=protect_last_n,
            summarize=summarize or extractive_summary,
            flush=flush,
            user_id=user_id,
            dsn=dsn,
            token_counter=token_counter,
        )

    # -- helpers ---------------------------------------------------------

    def _live_messages(self, session_id: str) -> list[dict]:
        """The session's turns as plain chat messages, oldest first."""
        session = self.gateway.get_session(session_id)
        return [_to_chat_message(m) for m in (session.get("messages") or [])]

    def _latest_compression_child(self, session_id: str) -> tuple[str, str] | None:
        """(child id, summary text) of the newest compression child.

        None when the session has no compression child yet. The
        newest edge is checked first (``children`` is newest first).
        """
        for edge in self.gateway.children(session_id):
            if edge.get("kind") != "compression":
                continue
            child = self.gateway.get_session(edge["child_session_id"])
            messages = child.get("messages") or []
            if messages:
                return str(edge["child_session_id"]), str(messages[0].get("content") or "")
        return None

    def _adopt(self, session_id: str, result, *, summary_text: str) -> str:
        """Record the compression in lineage, reusing a duplicate.

        Returns the child session id. When the newest compression
        child already holds this exact summary, it is reused instead
        of birthing a duplicate child for the same middle turns.
        """
        latest = self._latest_compression_child(session_id)
        if latest is not None and latest[1] == summary_text:
            return latest[0]
        child = adopt_compression_result(
            self.gateway,
            parent_session_id=session_id,
            result=result,
            reason=_reason_text(result.stats),
        )
        return str(child["id"])

    # -- automatic trigger ------------------------------------------------

    def maybe_compress(self, session_id: str, *, mode: str | None = None) -> dict:
        """Compress ``session_id`` now when a threshold is hit.

        Returns an outcome dict: ``compressed`` (bool), ``messages``
        (the live context: the summary plus the protected tail when
        compressed, the original turns otherwise), ``child_session_id``
        (the lineage child holding the summary, or None),
        ``stats`` (the G19 telemetry, or None), and ``session_id``.
        The parent session is never modified: its original turns
        stay stored, and the summary lives in the child session.
        """
        messages = self._live_messages(session_id)
        compressed_messages, result = prepare_turn_messages(
            messages,
            context_limit=self.context_limit,
            compressor=self._compressor,
            mode=mode,
        )
        if result is None or not result.stats.get("compressed"):
            return {
                "compressed": False,
                "messages": compressed_messages,
                "child_session_id": None,
                "stats": None,
                "session_id": session_id,
            }
        summary_text = find_summary_message(compressed_messages)
        assert summary_text is not None, "compressed result carries no summary message"
        child_id = self._adopt(session_id, result, summary_text=summary_text)
        return {
            "compressed": True,
            "messages": compressed_messages,
            "child_session_id": child_id,
            "stats": result.stats,
            "session_id": session_id,
        }

    def append_turn(
        self, session_id: str, role: str, content: str, *, mode: str | None = None
    ) -> dict:
        """Append one turn, then compress automatically if needed.

        The turn-loop entry point: every turn goes through here, so
        compression fires on its own when the context grows. Returns
        the ``maybe_compress`` outcome plus ``message`` (the stored
        turn record). The appended turn is always stored in the
        parent session, compressed or not.
        """
        message = self.gateway.append_message(session_id, role, content)
        outcome = self.maybe_compress(session_id, mode=mode)
        outcome["message"] = message
        return outcome

    def build_model_messages(self, session_id: str, *, mode: str | None = None) -> list[dict]:
        """Messages the model call should use for ``session_id``.

        Loads the session and compresses it when a threshold is hit,
        so the caller sends this list instead of the raw turns. The
        summary message (when present) starts with
        ``SUMMARY_MARKER``; callers that need the summary text use
        ``find_summary_message`` from ``puspa.sessions.lineage``.
        """
        return self.maybe_compress(session_id, mode=mode)["messages"]


def maybe_compress_session(
    gateway, session_id: str, *, mode: str | None = None, **compressor_kwargs
) -> dict:
    """One-shot automatic compression of ``session_id``.

    Builds a throwaway :class:`AutoCompressor` with the given
    compressor options (``context_limit``, ``protect_last_n``,
    ``summarize``, ...) and returns its ``maybe_compress`` outcome.
    """
    return AutoCompressor(gateway, **compressor_kwargs).maybe_compress(session_id, mode=mode)
