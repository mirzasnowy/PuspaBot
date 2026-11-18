"""Tests for two-threshold context compression (puspa/runtime/context_compression.py).

G19 pattern (hermes-agent, rewritten, not copied): a preflight
check at 50% of the context limit before an API call, a stricter
gateway check at 85% between turns, flush memory to disk before
summarizing, the last N messages kept whole, tool call/result pairs
never split, a fresh lineage id per compression, and compression
telemetry. These tests prove the mechanism, not the claim:

- the thresholds fire at the right fractions and not below them;
- the gateway protects fewer tail messages than the preflight run
  on the same history (more aggressive);
- protect_last_n is respected verbatim, and system messages always
  survive whole;
- no tool pair is ever split by the protected/summarized boundary,
  even when the boundary falls inside a pair;
- the lineage id changes on every compression;
- the memory flush runs before the summarizer, and a failing flush
  never blocks compression;
- telemetry records before/after tokens, the ratio, the threshold
  hit, and the protected/summarized counts;
- the summary keeps the numbers the tail refers to (orphan-number
  rule), both through the LLM prompt and through the deterministic
  extractive fallback that runs when the summarizer raises;
- compressing an already-compressed history does not corrupt it.

In-memory only: this module shadows the conftest autouse Postgres
reset fixture (``_reset_tables``) with a no-op, so no test here
touches the puspa_test database. The summarizer is always an
injected deterministic stub; the live LLM path (router ``complete``
with the cheapest reader model) is NOT tested here because it needs
a key and network. No live call is made anywhere in this file.
"""

from __future__ import annotations

import json
import re
import uuid

import httpx
import pytest

from puspa.runtime.context_compression import (
    SUMMARY_MARKER,
    SUMMARY_PROMPT_TEMPLATE,
    ContextCompressor,
    extractive_summary,
    prepare_turn_messages,
    should_compress,
)


@pytest.fixture(autouse=True)
def _reset_tables():
    """Shadow the conftest autouse Postgres reset: in-memory only."""
    yield


# ---------------------------------------------------------------------------
# Fixtures: deterministic histories and stub summarizers
# ---------------------------------------------------------------------------

FILLER = (
    "The customer asked about the delivery schedule and the agent "
    "explained the warehouse cutoff and the courier handoff. "
)


def make_text(n_chars: int) -> str:
    """Deterministic filler text of roughly ``n_chars`` characters."""
    repeats = n_chars // len(FILLER) + 1
    return (FILLER * repeats)[:n_chars]


def make_message(role: str, content: str, **extra) -> dict:
    message = {"role": role, "content": content}
    message.update(extra)
    return message


def make_history(n_turns: int, *, chars_per_turn: int = 400) -> list[dict]:
    """Alternating user/assistant history with distinct content."""
    messages: list[dict] = [
        make_message("system", "You are Puspa, a helpful assistant."),
    ]
    for turn in range(n_turns):
        messages.append(make_message("user", f"User turn {turn}: {make_text(chars_per_turn)}"))
        messages.append(
            make_message("assistant", f"Assistant turn {turn}: {make_text(chars_per_turn)}")
        )
    return messages


def make_history_with_tools(n_pairs: int = 6) -> list[dict]:
    """History where every assistant turn calls a tool and gets results.

    Each pair is an assistant message with ``tool_calls`` followed by
    its tool-result message. The boundary between the summarized
    middle and the protected tail will fall inside a pair unless the
    grouping logic keeps pairs whole.
    """
    messages: list[dict] = [
        make_message("system", "You are Puspa, a helpful assistant."),
    ]
    for pair in range(n_pairs):
        call_id = f"call-{pair}"
        messages.append(
            make_message(
                "assistant",
                f"Checking the bill for customer {1000 + pair}.",
                tool_calls=[
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "check_bill", "arguments": "{}"},
                    }
                ],
            )
        )
        messages.append(
            make_message(
                "tool",
                f"Bill for customer {1000 + pair}: Rp{250_000 + pair * 1000}, "
                f"due 2026-11-{10 + pair}.",
                tool_call_id=call_id,
            )
        )
        messages.append(make_message("user", f"Thanks, noted for pair {pair}. {make_text(200)}"))
    return messages


def stub_summarizer(seen: list | None = None):
    """Deterministic stub: records its input, returns a fixed summary
    that keeps the numbers the test history carries."""

    def _summarize(text: str) -> str:
        if seen is not None:
            seen.append(text)
        numbers = sorted(set(re.findall(r"Rp[\d.,]+|\d{4}-\d{2}-\d{2}", text)))
        kept = "; ".join(numbers) if numbers else "no numbers seen"
        return (
            "Earlier turns covered the delivery schedule and bill "
            f"checks. Preserved figures: {kept}."
        )

    return _summarize


def raising_summarizer(text: str) -> str:
    raise RuntimeError("no key available")


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


def test_should_compress_thresholds():
    assert should_compress(0, 1000) is None
    assert should_compress(499, 1000) is None
    assert should_compress(500, 1000) == "preflight"
    assert should_compress(849, 1000) == "preflight"
    assert should_compress(850, 1000) == "gateway"
    assert should_compress(1200, 1000) == "gateway"


def test_should_compress_rejects_bad_limit():
    with pytest.raises(ValueError):
        should_compress(10, 0)


def test_prepare_turn_messages_below_threshold_is_unchanged():
    messages = make_history(2)
    compressor = ContextCompressor(context_limit=100_000, summarize=stub_summarizer())
    out, result = prepare_turn_messages(messages, context_limit=100_000, compressor=compressor)
    assert result is None
    assert out == messages
    assert out is not messages  # new list, caller input untouched


def test_preflight_compresses_above_50_percent():
    messages = make_history(30)
    compressor = ContextCompressor(context_limit=1000, summarize=stub_summarizer())
    # Put the history at ~70% of the limit: preflight territory,
    # below the 85% gateway line.
    tokens = compressor.count_messages(messages)
    limit = int(tokens / 0.7)
    compressor = ContextCompressor(context_limit=limit, summarize=stub_summarizer())
    result = compressor.compress(messages, mode="preflight")
    assert result.stats["compressed"] is True
    assert result.stats["threshold_hit"] == "preflight"
    assert result.stats["tokens_before"] == tokens
    assert result.stats["tokens_after"] < result.stats["tokens_before"]
    assert 0 < result.stats["compression_ratio"] < 1
    assert result.stats["summarized_count"] > 0
    assert result.stats["summary_chars"] > 0


def test_gateway_is_more_aggressive_than_preflight():
    messages = make_history(30)
    preflight = ContextCompressor(context_limit=1000, summarize=stub_summarizer()).compress(
        messages, mode="preflight"
    )
    gateway = ContextCompressor(context_limit=1000, summarize=stub_summarizer()).compress(
        messages, mode="gateway"
    )
    assert gateway.stats["compressed"] is True
    assert gateway.stats["threshold_hit"] == "gateway"
    assert gateway.stats["protected_count"] < preflight.stats["protected_count"]
    assert gateway.stats["protected_count"] >= 4  # MIN_PROTECTED floor
    assert gateway.stats["tokens_after"] <= preflight.stats["tokens_after"]


def test_gateway_below_85_percent_does_not_compress():
    # A gateway check at 60% must not compress: the gateway
    # threshold is 85%, even though preflight would fire.
    messages = make_history(30)
    probe = ContextCompressor(context_limit=1000, summarize=stub_summarizer())
    tokens = probe.count_messages(messages)
    limit = int(tokens / 0.6)
    compressor = ContextCompressor(context_limit=limit, summarize=stub_summarizer())
    before = compressor.count_messages(messages)
    assert limit // 2 <= before < int(limit * 0.85)
    result = compressor.compress(messages, mode="gateway")
    assert result.stats["compressed"] is False
    assert result.messages == messages


# ---------------------------------------------------------------------------
# Protected tail, system messages, tool pairs
# ---------------------------------------------------------------------------


def test_protect_last_n_kept_verbatim():
    messages = make_history(30)
    compressor = ContextCompressor(
        context_limit=1000, protect_last_n=20, summarize=stub_summarizer()
    )
    result = compressor.compress(messages, mode="preflight")
    assert result.stats["protected_count"] == 20
    tail = result.messages[-20:]
    assert tail == messages[-20:]
    assert result.messages[0] == messages[0]  # system message verbatim


def test_system_messages_always_survive():
    messages = make_history(30)
    messages.insert(5, make_message("system", "Reminder: always quote prices in rupiah."))
    compressor = ContextCompressor(context_limit=1000, summarize=stub_summarizer())
    result = compressor.compress(messages, mode="preflight")
    contents = [m["content"] for m in result.messages]
    assert "You are Puspa, a helpful assistant." in contents
    assert "Reminder: always quote prices in rupiah." in contents
    assert result.stats["system_count"] == 2


def test_tool_pairs_never_split():
    messages = make_history_with_tools(n_pairs=8)
    for mode in ("preflight", "gateway"):
        compressor = ContextCompressor(
            context_limit=1000, protect_last_n=5, summarize=stub_summarizer()
        )
        result = compressor.compress(messages, mode=mode)
        out = result.messages
        # Every tool result in the output has its calling assistant
        # message in the output, immediately before it (or before
        # the other results of the same call).
        for index, message in enumerate(out):
            if message.get("role") != "tool":
                continue
            call_id = message.get("tool_call_id")
            callers = [
                m
                for m in out[:index]
                if m.get("role") == "assistant"
                and any(
                    c.get("id") == call_id
                    for c in (m.get("tool_calls") or [])
                    if isinstance(c, dict)
                )
            ]
            assert callers, f"orphaned tool result {call_id} in {mode} mode"
        # And no assistant tool call in the output lost its results.
        for index, message in enumerate(out):
            if message.get("role") == "assistant" and message.get("tool_calls"):
                wanted = {c["id"] for c in message["tool_calls"]}
                following = out[index + 1 : index + 1 + len(wanted)]
                got = {m.get("tool_call_id") for m in following if m.get("role") == "tool"}
                assert wanted <= got, f"split tool pair in {mode} mode"


def test_tool_pair_straddling_boundary_stays_together():
    # protect_last_n=5 on a history whose 5th-from-last message is a
    # tool result: the boundary must expand to include the call.
    messages = make_history_with_tools(n_pairs=4)
    compressor = ContextCompressor(
        context_limit=1000, protect_last_n=5, summarize=stub_summarizer()
    )
    result = compressor.compress(messages, mode="preflight")
    out = result.messages
    tool_messages = [m for m in out if m.get("role") == "tool"]
    assert tool_messages, "expected tool results in the protected tail"
    for tool_message in tool_messages:
        call_id = tool_message["tool_call_id"]
        assert any(
            m.get("role") == "assistant"
            and any(c.get("id") == call_id for c in (m.get("tool_calls") or []))
            for m in out
        )


# ---------------------------------------------------------------------------
# Lineage, flush order, telemetry
# ---------------------------------------------------------------------------


def test_lineage_changes_every_compression():
    messages = make_history(30)
    compressor = ContextCompressor(context_limit=1000, summarize=stub_summarizer())
    first = compressor.compress(messages, mode="preflight")
    second = compressor.compress(messages, mode="preflight")
    assert first.lineage_id != second.lineage_id
    for lineage_id in (first.lineage_id, second.lineage_id):
        assert re.fullmatch(r"[0-9a-f]{32}", lineage_id)
        uuid.UUID(hex=lineage_id)  # parses as a uuid4 hex value


def test_flush_runs_before_summarize():
    order: list[str] = []

    def _flush():
        order.append("flush")
        return {"summaries_created": 1}

    def _summarize(text: str) -> str:
        order.append("summarize")
        return "summary"

    compressor = ContextCompressor(context_limit=1000, summarize=_summarize, flush=_flush)
    result = compressor.compress(make_history(30), mode="preflight")
    assert order == ["flush", "summarize"]
    assert result.stats["flush"]["called"] is True
    assert result.stats["flush"]["ok"] is True


def test_failing_flush_does_not_block_compression():
    def _flush():
        raise RuntimeError("Postgres is down")

    compressor = ContextCompressor(context_limit=1000, summarize=stub_summarizer(), flush=_flush)
    result = compressor.compress(make_history(30), mode="preflight")
    assert result.stats["compressed"] is True
    assert result.stats["flush"]["called"] is True
    assert result.stats["flush"]["ok"] is False
    assert "RuntimeError" in result.stats["flush"]["error"]


def test_no_user_id_means_no_flush_and_no_crash():
    compressor = ContextCompressor(context_limit=1000, summarize=stub_summarizer())
    result = compressor.compress(make_history(30), mode="preflight")
    assert result.stats["compressed"] is True
    assert result.stats["flush"]["called"] is False


def test_telemetry_is_complete_and_consistent():
    messages = make_history(30)
    compressor = ContextCompressor(context_limit=1000, summarize=stub_summarizer())
    result = compressor.compress(messages, mode="preflight")
    stats = result.stats
    for key in (
        "tokens_before",
        "tokens_after",
        "compression_ratio",
        "threshold_hit",
        "protected_count",
        "summarized_count",
        "summary_chars",
    ):
        assert key in stats, f"missing telemetry key {key}"
    assert stats["tokens_before"] == compressor.count_messages(messages)
    assert stats["tokens_after"] == compressor.count_messages(result.messages)
    assert stats["compression_ratio"] == round(stats["tokens_after"] / stats["tokens_before"], 4)
    # Protected + summarized + system == the original message count.
    assert stats["protected_count"] + stats["summarized_count"] + stats["system_count"] == len(
        messages
    )
    assert stats["counter"] == "tiktoken-cl100k_base"


def test_compress_does_not_mutate_input():
    messages = make_history(10)
    snapshot = [dict(m) for m in messages]
    ContextCompressor(context_limit=1000, summarize=stub_summarizer()).compress(
        messages, mode="preflight"
    )
    assert messages == snapshot


# ---------------------------------------------------------------------------
# Summary content: orphan-number rule and deterministic fallback
# ---------------------------------------------------------------------------


def test_summary_prompt_orders_number_preservation():
    assert "Preserve every number" in SUMMARY_PROMPT_TEMPLATE
    assert "date" in SUMMARY_PROMPT_TEMPLATE
    assert "amount" in SUMMARY_PROMPT_TEMPLATE


def test_summary_keeps_numbers_the_tail_refers_to():
    messages = [
        make_message("system", "You are Puspa."),
        make_message(
            "user",
            "My PLN bill for customer 512345678901 is Rp1.250.000, "
            "due 2026-11-18. Please check it.",
        ),
        make_message("assistant", "Checking bill 512345678901 for Rp1.250.000."),
        make_message("user", "Any update on the Rp1.250.000 bill?"),
        make_message("assistant", "Still waiting on the provider."),
    ]
    # Force compression with a tiny limit and a small protected tail
    # so the bill turns land in the summarized middle.
    seen: list[str] = []
    compressor = ContextCompressor(
        context_limit=10, protect_last_n=2, summarize=stub_summarizer(seen)
    )
    result = compressor.compress(messages, mode="preflight")
    assert result.stats["compressed"] is True
    summary_messages = [m for m in result.messages if m["content"].startswith(SUMMARY_MARKER)]
    assert len(summary_messages) == 1
    summary = summary_messages[0]["content"]
    assert "Rp1.250.000" in summary
    assert "2026-11-18" in summary
    # The summarizer received the middle turns, numbers included.
    assert "512345678901" in seen[0]


def test_extractive_fallback_keeps_digit_lines():
    text = (
        "Hello there, just checking in\n"
        "The bill is Rp1.250.000 due 2026-11-18\n"
        "Some small talk about the weather\n"
        "Customer number 512345678901 called twice\n"
        "Goodbye for now"
    )
    summary = extractive_summary(text)
    assert "Rp1.250.000" in summary
    assert "2026-11-18" in summary
    assert "512345678901" in summary


def test_raising_summarizer_falls_back_deterministically():
    messages = make_history(30)
    compressor = ContextCompressor(context_limit=1000, summarize=raising_summarizer)
    first = compressor.compress(messages, mode="preflight")
    second = compressor.compress(messages, mode="preflight")
    assert first.stats["compressed"] is True
    assert first.stats["summary_fallback"] is not None
    assert "RuntimeError" in first.stats["summary_fallback"]
    # Same input, same deterministic fallback summary.
    first_summary = [
        m["content"] for m in first.messages if m["content"].startswith(SUMMARY_MARKER)
    ]
    second_summary = [
        m["content"] for m in second.messages if m["content"].startswith(SUMMARY_MARKER)
    ]
    assert first_summary == second_summary


def test_default_summarizer_uses_router_reader_model():
    """The default summarizer goes through the router's cheapest
    reader model (offline: MockTransport + fake key, no network, no
    live key). The model reply is used verbatim, with no fallback."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Bills checked: Rp1.250.000 due 2026-11-18.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "model": "meta-llama/llama-3.1-8b-instruct",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }
        return httpx.Response(
            200, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )

    transport = httpx.MockTransport(handler)
    compressor = ContextCompressor(
        context_limit=1000,
        llm_kwargs={"transport": transport, "key_resolver": lambda provider: "test-key-not-real"},
    )
    result = compressor.compress(make_history(30), mode="preflight")
    assert result.stats["compressed"] is True
    assert result.stats["summary_fallback"] is None
    assert len(requests) == 1
    sent = json.loads(requests[0].content.decode())
    assert sent["model"] == "meta-llama/llama-3.1-8b-instruct"
    prompt = sent["messages"][0]["content"]
    assert "Preserve every number" in prompt
    summary_messages = [m for m in result.messages if m["content"].startswith(SUMMARY_MARKER)]
    assert len(summary_messages) == 1
    assert "Rp1.250.000" in summary_messages[0]["content"]
    assert "2026-11-18" in summary_messages[0]["content"]


def test_nothing_to_summarize_is_a_no_op():
    # Two messages total: both fit inside protect_last_n, so there
    # is no middle to summarize even above the threshold.
    messages = [
        make_message("system", "You are Puspa."),
        make_message("user", make_text(5000)),
    ]
    compressor = ContextCompressor(context_limit=10, protect_last_n=20, summarize=stub_summarizer())
    result = compressor.compress(messages, mode="preflight")
    assert result.stats["compressed"] is False
    assert result.messages == messages


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_recompressing_below_threshold_leaves_messages_untouched():
    messages = make_history(30)
    compressor = ContextCompressor(context_limit=1000, summarize=stub_summarizer())
    first = compressor.compress(messages, mode="preflight")
    assert first.stats["compressed"] is True
    # A second run with a generous limit sees no threshold hit.
    out, result = prepare_turn_messages(
        first.messages, context_limit=100_000, compressor=compressor
    )
    assert result is None
    assert out == first.messages


def test_recompressing_compressed_history_stays_well_formed():
    messages = make_history_with_tools(n_pairs=8)
    compressor = ContextCompressor(
        context_limit=1000, protect_last_n=5, summarize=stub_summarizer()
    )
    first = compressor.compress(messages, mode="preflight")
    assert first.stats["compressed"] is True
    # The first compression shrank the history far below the old
    # limit, so the second run needs a smaller limit to fire again.
    second_compressor = ContextCompressor(
        context_limit=100, protect_last_n=5, summarize=stub_summarizer()
    )
    second = second_compressor.compress(first.messages, mode="preflight")
    assert second.stats["compressed"] is True
    # Still exactly one summary message: the old summary was folded
    # into the new one, not duplicated or dropped.
    markers = [m for m in second.messages if m["content"].startswith(SUMMARY_MARKER)]
    assert len(markers) == 1
    # Tool pairs still intact after the second compression.
    out = second.messages
    for message in out:
        if message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            assert any(
                m.get("role") == "assistant"
                and any(c.get("id") == call_id for c in (m.get("tool_calls") or []))
                for m in out
            ), f"orphaned tool result {call_id} after recompression"
    assert second.lineage_id != first.lineage_id
