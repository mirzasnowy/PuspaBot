"""Compression lineage: a compression births a child session (G26).

Seam to G19 (``puspa.runtime.context_compression``): the compressor
mints a fresh ``lineage_id`` per compression and returns a
``CompressionResult`` with the compressed messages and
``stats["compressed"]``. This module adopts that result into the
session store:

- When ``stats["compressed"]`` is False, nothing is recorded — the
  caller keeps the original session (the compressor's own rule:
  adopt the new lineage only when something was actually
  summarized).
- When True, the summary message the compressor produced becomes the
  first message of a new child session, linked to the parent by a
  ``session_lineage`` edge of kind ``compression``. The store writes
  session + summary + edge in one transaction, so the lineage can
  never be half-recorded.

The summary is found in the compressed messages as the assistant
message carrying the compressor's summary marker; the caller may
also pass the summary text explicitly. This module only depends on
the ``CompressionResult`` shape (``messages`` + ``stats``), never on
the compressor class, so it stays importable without the router.
"""

from __future__ import annotations

#: Marker the G19 compressor prefixes its summary message with.
#: Same value as ``puspa.runtime.context_compression.SUMMARY_MARKER``
#: (imported lazily in tests to avoid a router import here; keep the
#: two in sync — ``test_session_fts_lineage`` asserts the equality).
SUMMARY_MARKER = "[Context summary of earlier turns]"


def find_summary_message(messages):
    """Return the summary text from compressed messages, or None.

    The summary is the first assistant message whose content starts
    with the G19 summary marker.
    """
    for message in messages or []:
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "")
        if content.startswith(SUMMARY_MARKER):
            return content
    return None


def adopt_compression_result(gateway, *, parent_session_id, result, summary=None, reason=None):
    """Adopt a G19 ``CompressionResult`` into the session lineage.

    Returns the child session record (with messages) when the result
    was actually compressed, otherwise ``None`` (no child, no edge —
    the parent session stays canonical). ``result`` needs only
    ``.messages`` and ``.stats["compressed"]``; a plain object or a
    ``{"messages": ..., "stats": ...}`` mapping both work.
    """
    if isinstance(result, dict):
        messages = result.get("messages") or []
        stats = result.get("stats") or {}
    else:
        messages = getattr(result, "messages", None) or []
        stats = getattr(result, "stats", None) or {}
    if not stats.get("compressed"):
        return None
    text = summary if summary is not None else find_summary_message(messages)
    if text is None:
        raise ValueError(
            "compressed result carries no summary message "
            f"(marker {SUMMARY_MARKER!r} not found); pass summary= explicitly"
        )
    return gateway.create_child_session(
        parent_session_id,
        summary=text,
        kind="compression",
        reason=reason,
    )


def record_compression(gateway, *, parent_session_id, summary, reason=None):
    """Record a compression directly (no compressor result needed).

    Returns the child session record (with messages).
    """
    return gateway.create_child_session(
        parent_session_id,
        summary=summary,
        kind="compression",
        reason=reason,
    )
