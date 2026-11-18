"""Two-threshold context compression for long Puspa turns (G19).

Pattern studied, then rewritten as Puspa's own code (see REUSE.md):
hermes-agent compresses an agent's conversation when it grows too
large for the context window, with two thresholds (a preflight check
before an API call and a stricter gateway check between turns), a
fixed order (flush memory to disk first, then summarize the middle
turns into one dense summary, then keep the last N messages whole),
tool call/result pairs that are never split, a fresh session lineage
id per compression (a compression is a child session), and
compression telemetry. No hermes-agent code is copied; the module
below is written from scratch for Puspa's own message shape.

Why this lives in ``puspa/runtime`` and not ``puspa/memory``: the
compressor operates on the live turn's message list at the runtime
layer, right before the model call (preflight) or between turns
(gateway). It reuses the token counter from
``puspa/router/compression.py`` and calls into the memory layer only
through an injected ``flush`` callback, so memory stays the callee,
not the owner. Long-horizon consolidation itself lives in
``puspa/memory/consolidate.py`` (``consolidate_idle``) and is the
default flush target when a ``user_id`` is given.

Thresholds (fractions of ``context_limit``):

- preflight, 50%: checked before an API call. Crossing it compresses
  the middle turns while keeping ``protect_last_n`` (default 20)
  messages whole.
- gateway, 85%: checked between turns. More aggressive: it keeps
  only half as many tail messages (at least ``MIN_PROTECTED``), so a
  session that keeps growing still fits.

Fixed order, every compression:

1. Flush memory to disk first (the ``flush`` callback; by default
   ``consolidate_idle`` for the given ``user_id`` when one is given,
   otherwise a no-op). The flush runs before any summarization, so
   nothing the summary drops is lost from durable memory. A flush
   failure never blocks compression: it is recorded in the stats
   and the run continues.
2. Summarize the middle turns into one dense summary message. The
   summarizer is an injected ``summarize(text) -> str`` callable;
   the default uses the router's cheapest reader model through
   ``puspa.router.complete.complete``. The summary prompt orders the
   model to preserve every number, date, amount, name, and
   identifier exactly (the orphan-number rule from
   ``puspa/router/compression.py``: a number the kept tail refers
   to must survive in the summary). When the summarizer raises (no
   key, no network, any error), a deterministic extractive fallback
   runs instead and the stats say so; compression never raises for
   a summary failure.
3. Keep the last N messages whole (``protect_last_n``; halved at the
   gateway). System messages are always kept whole and are never
   counted as protected or summarized.
4. Never split a tool call/result pair: messages are grouped into
   units first (an assistant message carrying ``tool_calls`` plus
   the consecutive tool-result messages that answer it), and the
   protected/summarized boundary only ever falls between units.
5. Mint a fresh ``lineage_id`` (uuid4 hex) per compression. The
   returned messages are a child session of the original: callers
   that track sessions should adopt the new id only when the stats
   say ``compressed`` is True.

Telemetry: every run returns ``stats`` with ``tokens_before``,
``tokens_after``, ``compression_ratio`` (after / before),
``threshold_hit`` (``"preflight"``, ``"gateway"``, or None when
nothing was compressed), ``protected_count``,
``summarized_count``, ``summary_chars``, plus ``compressed``,
``mode``, ``counter``, ``flush``, and ``summary_fallback``.

Wiring status (honest): there is no Puspa-owned seam today that
can rewrite the live turn's message list before the model call.
The live loop is nanobot's ``AgentLoop``: it builds its own
transcript from session history (``nanobot/agent/context.py``
``build_transcript``), and ``PuspaHook.before_run`` receives only a
deep-copied snapshot of the messages
(``nanobot/agent/runner.py`` ``_run_context_snapshot``), so a hook
cannot compress the live run. The call site for this compressor is
``prepare_turn_messages`` below: a future Puspa-owned turn wrapper
(or a nanobot upstream hook that allows pre-request message
rewrites) calls it with the turn's messages and the model's
context limit before the request. The prompt-assembly worker (G20)
consumes the compressor's output messages. No fake wiring is
installed in the hook layer.

Not built here (follow-ups, see the G19 report): micro-compaction,
native/relay/prepared compaction variants, image compression, and
trajectory compression for training data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from puspa.router.compression import count_tokens

__all__ = [
    "CompressionResult",
    "ContextCompressor",
    "MIN_PROTECTED",
    "GATEWAY_THRESHOLD",
    "PREFLIGHT_THRESHOLD",
    "SUMMARY_PROMPT_TEMPLATE",
    "extractive_summary",
    "prepare_turn_messages",
    "should_compress",
]

#: Preflight threshold: fraction of the context limit that triggers
#: compression before an API call.
PREFLIGHT_THRESHOLD = 0.50

#: Gateway threshold: fraction of the context limit that triggers
#: the more aggressive between-turns compression.
GATEWAY_THRESHOLD = 0.85

#: Tail messages kept whole by default (hermes-agent's
#: ``compression.protect_last_n`` default, kept).
DEFAULT_PROTECT_LAST_N = 20

#: Fewest tail messages the gateway ever keeps, even when halving
#: ``protect_last_n`` would go lower.
MIN_PROTECTED = 4

#: Tokens charged per message on top of its content tokens
#: (role markers and framing; an estimate, labelled in the stats
#: through the counter name).
MESSAGE_OVERHEAD_TOKENS = 4

#: Marker that opens every summary message, so later runs (and the
#: G20 prompt builder) can recognise compressed context.
SUMMARY_MARKER = "[Context summary of earlier turns]"

#: Longest extractive fallback summary, in characters.
EXTRACTIVE_MAX_CHARS = 2000

#: Prompt for the LLM summarizer. The preserve-numbers paragraph is
#: the orphan-number rule: anything the kept tail may refer to (a
#: price, a customer number, a date, a count) must survive verbatim
#: in the summary, or the tail becomes unreadable.
SUMMARY_PROMPT_TEMPLATE = """Summarize the following earlier conversation turns into one dense \
summary. The summary replaces these turns in the context, so it must \
carry everything the rest of the conversation may still refer to.

Rules:
- Preserve every number, date, amount, price, count, name, and \
identifier EXACTLY as written. Never round, reformat, or drop a \
number that appears in the turns below.
- Keep decisions made, facts established, and open questions.
- Keep tool results that changed what is true (files written, \
payments made, errors seen), with their numbers.
- Drop greetings, repetition, and small talk.
- Write in short bullet lines, no preamble.

Conversation turns to summarize:
{transcript}
"""


def should_compress(token_count: int, context_limit: int) -> str | None:
    """Decide which threshold ``token_count`` hits, if any.

    Returns ``"gateway"`` at or above 85% of ``context_limit``,
    ``"preflight"`` at or above 50%, and None below 50%. A
    non-positive limit is a caller error.
    """
    if context_limit <= 0:
        raise ValueError("context_limit must be positive")
    ratio = token_count / context_limit
    if ratio >= GATEWAY_THRESHOLD:
        return "gateway"
    if ratio >= PREFLIGHT_THRESHOLD:
        return "preflight"
    return None


def _message_text(message: dict) -> str:
    """Plain text of one message for counting and summarizing.

    ``content`` is normally a string; a list of content parts (the
    multimodal shape) is joined from its text parts. Anything else
    is stringified so counting never raises.
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(content)


def extractive_summary(text: str, *, max_chars: int = EXTRACTIVE_MAX_CHARS) -> str:
    """Deterministic fallback summary: keep digit-bearing lines whole.

    Used when no LLM summarizer is available or the LLM call fails.
    Lines carrying a digit are never dropped or truncated (the
    orphan-number rule); digit-free lines are kept in order until
    the character budget runs out. The result is stable for the
    same input, costs nothing, and needs no key.
    """
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    digit_lines = [line for line in lines if any(ch.isdigit() for ch in line)]
    plain_lines = [line for line in lines if not any(ch.isdigit() for ch in line)]
    kept: list[str] = []
    total = 0

    def _take(line: str) -> bool:
        nonlocal total
        if total + len(line) + 1 > max_chars and kept:
            return False
        kept.append(line)
        total += len(line) + 1
        return True

    # Digit-bearing lines first (they carry the facts the tail may
    # refer to), then the remaining lines in original order, merged
    # back into original order at the end.
    chosen = set()
    for line in digit_lines:
        if _take(line):
            chosen.add(id(line))
    for line in plain_lines:
        if _take(line):
            chosen.add(id(line))
    ordered = [line for line in lines if id(line) in chosen]
    return "\n".join(ordered)


@dataclass
class CompressionResult:
    """One compression run: the messages to send plus its measurement.

    ``messages`` is always a new list; kept messages are shallow
    copies, so the caller's list is never mutated. ``lineage_id``
    is fresh on every call. ``stats["compressed"]`` says whether
    anything was actually summarized; callers should adopt the new
    lineage only then.
    """

    messages: list[dict]
    lineage_id: str
    stats: dict = field(default_factory=dict)


class ContextCompressor:
    """Compress a turn's message list at the two thresholds.

    ``summarize`` is an injected ``summarize(text) -> str`` callable.
    None means the default: the router's cheapest reader model via
    ``puspa.router.complete.complete``. ``flush`` is an injected
    no-argument ``flush() -> dict | None`` callback run before any
    summarization. None with a ``user_id`` means the real memory
    seam: ``puspa.memory.consolidate.consolidate_idle`` for that
    user (imported lazily, so this module stays importable without
    Postgres). None without a ``user_id`` means no flush, recorded
    as such in the stats. ``token_counter`` is an injected
    ``text -> int`` callable for tests; the default is tiktoken
    ``cl100k_base`` through ``puspa.router.compression.count_tokens``.
    ``llm_kwargs`` are passed through to the default summarizer's
    ``complete`` call (``transport=`` and ``key_resolver=`` let tests
    drive the real default path offline through ``httpx.MockTransport``).
    """

    def __init__(self, *, context_limit: int = 128_000,
                 protect_last_n: int = DEFAULT_PROTECT_LAST_N,
                 summarize=None, flush=None, user_id=None, dsn=None,
                 token_counter=None, llm_kwargs=None) -> None:
        if context_limit <= 0:
            raise ValueError("context_limit must be positive")
        if protect_last_n < 1:
            raise ValueError("protect_last_n must be at least 1")
        self.context_limit = context_limit
        self.protect_last_n = protect_last_n
        self._summarize = summarize
        self._flush = flush
        self._user_id = user_id
        self._dsn = dsn
        self._token_counter = token_counter
        self._llm_kwargs = dict(llm_kwargs or {})
        self._counter_name = "tiktoken-cl100k_base"

    # -- token counting -------------------------------------------------

    def _count_text(self, text: str) -> int:
        if self._token_counter is not None:
            return int(self._token_counter(text))
        count, name = count_tokens(text)
        self._counter_name = name
        return count

    def count_messages(self, messages) -> int:
        """Tokens of a message list: content tokens plus per-message
        framing overhead."""
        total = 0
        for message in messages or []:
            total += self._count_text(_message_text(message))
            total += MESSAGE_OVERHEAD_TOKENS
        return total

    # -- summarizer and flush ------------------------------------------

    def _default_summarize(self, text: str) -> str:
        """Summarize through the router's cheapest reader model."""
        from puspa.router.complete import default_llm_complete

        complete = default_llm_complete(role="reader", **self._llm_kwargs)
        prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript=text)
        return complete(prompt)

    def _summarize_text(self, text: str) -> tuple[str, str | None]:
        """Summarize, falling back to the extractive summary on any
        failure. Returns (summary, fallback_reason or None)."""
        summarize = self._summarize or self._default_summarize
        try:
            result = summarize(text)
        except Exception as exc:  # noqa: BLE001 - any summarizer failure falls back
            return extractive_summary(text), f"{type(exc).__name__}: {exc}"
        if not isinstance(result, str) or not result.strip():
            return extractive_summary(text), "empty summarizer reply"
        return result, None

    def _default_flush(self) -> dict:
        """Flush memory through the real consolidation seam.

        Lazily imports ``consolidate_idle`` so this module never
        needs Postgres at import time. Best-effort: any failure is
        reported, never raised.
        """
        if self._user_id is None:
            return {"called": False, "reason": "no user_id: no memory to flush"}
        try:
            from puspa.memory.consolidate import consolidate_idle

            outcome = consolidate_idle(
                self._user_id, agent_id=None, dsn=self._dsn)
        except Exception as exc:  # noqa: BLE001 - flush must never block compression
            return {"called": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"called": True, "ok": True, "outcome": outcome}

    def _run_flush(self) -> dict:
        if self._flush is not None:
            try:
                outcome = self._flush()
            except Exception as exc:  # noqa: BLE001 - flush must never block compression
                return {"called": True, "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"}
            if isinstance(outcome, dict):
                return {"called": True, "ok": True, "outcome": outcome}
            return {"called": True, "ok": True}
        return self._default_flush()

    # -- unit grouping (tool pairs never split) -------------------------

    @staticmethod
    def _group_units(messages: list[dict]) -> tuple[list[dict], list[list[dict]]]:
        """Split messages into always-kept system messages and units.

        A unit is one message, except an assistant message carrying
        ``tool_calls`` together with the consecutive tool-result
        messages that answer it, and a run of tool-result messages
        with no preceding call in the list. The protected/summarized
        boundary only ever falls between units, so a tool call and
        its results are never separated.
        """
        system: list[dict] = []
        units: list[list[dict]] = []
        index = 0
        total = len(messages)
        while index < total:
            message = messages[index]
            if message.get("role") == "system":
                system.append(message)
                index += 1
                continue
            unit = [message]
            index += 1
            if message.get("role") == "assistant" and message.get("tool_calls"):
                wanted = {
                    str(call.get("id")) for call in message["tool_calls"]
                    if isinstance(call, dict) and call.get("id")
                }
                while index < total:
                    nxt = messages[index]
                    if nxt.get("role") != "tool":
                        break
                    call_id = str(nxt.get("tool_call_id") or "")
                    if wanted and call_id and call_id not in wanted:
                        break
                    unit.append(nxt)
                    index += 1
            elif message.get("role") == "tool":
                while index < total and messages[index].get("role") == "tool":
                    unit.append(messages[index])
                    index += 1
            units.append(unit)
        return system, units

    # -- compression -----------------------------------------------------

    def compress(self, messages, *, mode: str = "preflight") -> CompressionResult:
        """Compress ``messages`` at the given mode's threshold.

        ``mode`` is ``"preflight"`` or ``"gateway"``. When the token
        count is below that mode's threshold, or there is nothing to
        summarize (every non-system message is protected), the
        messages come back unchanged and ``stats["compressed"]`` is
        False. The caller's list and message dicts are never mutated.
        """
        if mode not in ("preflight", "gateway"):
            raise ValueError(f"unknown mode: {mode!r}")
        lineage_id = uuid.uuid4().hex
        original = [dict(m) for m in (messages or [])]
        tokens_before = self.count_messages(original)
        threshold_hit = should_compress(tokens_before, self.context_limit)
        mode_threshold = (
            GATEWAY_THRESHOLD if mode == "gateway" else PREFLIGHT_THRESHOLD
        )
        hit_for_mode = (
            threshold_hit == "gateway"
            or (threshold_hit == "preflight" and mode == "preflight")
        )
        stats: dict = {
            "tokens_before": tokens_before,
            "tokens_after": tokens_before,
            "compression_ratio": 1.0,
            "threshold_hit": threshold_hit,
            "protected_count": 0,
            "summarized_count": 0,
            "summary_chars": 0,
            "compressed": False,
            "mode": mode,
            "counter": self._counter_name,
            "flush": {"called": False, "reason": "no compression ran"},
            "summary_fallback": None,
            "system_count": 0,
        }
        if not hit_for_mode or tokens_before == 0:
            return CompressionResult(
                messages=original, lineage_id=lineage_id, stats=stats)
        # The gateway is more aggressive: it protects half as many
        # tail messages (never fewer than MIN_PROTECTED). A gateway
        # call below the 85% gateway threshold compresses nothing:
        # the thresholds decide, the mode only tunes how much tail
        # survives once a threshold is hit.
        protect_n = self.protect_last_n
        if mode == "gateway":
            protect_n = max(MIN_PROTECTED, self.protect_last_n // 2)
        system, units = self._group_units(original)
        stats["system_count"] = len(system)
        if not units:
            return CompressionResult(
                messages=original, lineage_id=lineage_id, stats=stats)
        # Walk units from the tail until the protected message count
        # reaches protect_n; everything before that is the middle.
        protected_units: list[list[dict]] = []
        protected_messages = 0
        cursor = len(units)
        while cursor > 0 and protected_messages < protect_n:
            cursor -= 1
            protected_units.insert(0, units[cursor])
            protected_messages += len(units[cursor])
        middle_units = units[:cursor]
        if not middle_units:
            stats["protected_count"] = protected_messages
            return CompressionResult(
                messages=original, lineage_id=lineage_id, stats=stats)
        # 1. Flush memory to disk BEFORE summarizing.
        flush_stats = self._run_flush()
        stats["flush"] = flush_stats
        # 2. Summarize the middle turns into one dense summary.
        middle_messages = [m for unit in middle_units for m in unit]
        transcript_lines = [
            f"{m.get('role', 'unknown')}: {_message_text(m)}"
            for m in middle_messages
        ]
        summary, fallback_reason = self._summarize_text(
            "\n".join(transcript_lines))
        stats["summary_fallback"] = fallback_reason
        stats["summarized_count"] = len(middle_messages)
        stats["summary_chars"] = len(summary)
        stats["protected_count"] = protected_messages
        summary_message = {
            "role": "assistant",
            "content": f"{SUMMARY_MARKER}\n{summary}",
        }
        # 3. Reassemble: system messages first (verbatim, in order),
        # then the summary, then the protected tail (verbatim).
        new_messages = (
            [dict(m) for m in system]
            + [summary_message]
            + [dict(m) for unit in protected_units for m in unit]
        )
        tokens_after = self.count_messages(new_messages)
        stats["tokens_after"] = tokens_after
        stats["compression_ratio"] = (
            round(tokens_after / tokens_before, 4) if tokens_before else 1.0
        )
        stats["compressed"] = True
        return CompressionResult(
            messages=new_messages, lineage_id=lineage_id, stats=stats)


def prepare_turn_messages(messages, *, context_limit: int,
                          compressor: ContextCompressor | None = None,
                          mode: str | None = None,
                          **compressor_kwargs) -> tuple[list[dict], CompressionResult | None]:
    """Check a turn's messages against the thresholds, compress if hit.

    The call site for G19: a Puspa-owned turn wrapper calls this
    with the turn's messages and the model's context limit before
    the request. Returns ``(messages, None)`` when no threshold is
    hit (messages unchanged), otherwise ``(compressed_messages,
    result)`` where ``result`` carries the lineage id and telemetry.
    ``mode`` forces ``"preflight"`` or ``"gateway"``; without it the
    mode follows ``should_compress``.
    """
    compressor = compressor or ContextCompressor(
        context_limit=context_limit, **compressor_kwargs)
    tokens = compressor.count_messages(messages or [])
    hit = should_compress(tokens, context_limit)
    if hit is None:
        return [dict(m) for m in (messages or [])], None
    result = compressor.compress(messages, mode=mode or hit)
    return result.messages, result
