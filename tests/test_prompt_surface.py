"""Snapshot + behavior tests for the layered prompt surface (G20).

Covers the hermes-agent invariants rewritten for Puspa
(``puspa/runtime/prompt_assembly.py``,
``puspa/runtime/prompt_caching.py``):

- fixed layer order: stable, context, volatile;
- stable layer byte-identical across turns of one session, and
  the cache breakpoint never moves when context/volatile change;
- the builder never compresses: messages pass through as-is, and
  compression is the only mid-session mutation allowed;
- the snapshot test: a prompt or tool change fails until the
  snapshot is regenerated and committed with the change;
- prompt-state-changing commands are deferred to the next session
  unless the caller opts in explicitly.

Snapshot regeneration: run the snapshot test with
``PROMPT_SURFACE_REGEN=1`` to rewrite
``tests/snapshots/prompt_surface.json`` from the current fixture,
then commit the file together with the change that made the old
snapshot stale. Never regenerate to silence a failure you have
not read: the snapshot exists to make prompt changes visible.

These tests are in-memory only (no database, no network, no live
LLM call) and run with ``--noconftest`` as well as under the full
suite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from puspa.router.translate import translate_request
from puspa.runtime.prompt_assembly import (
    DeferredChange,
    DeferredChangeQueue,
    PromptBuilder,
    PromptSession,
)
from puspa.runtime.prompt_caching import (
    anthropic_request,
    anthropic_system_blocks,
    openai_messages,
    openai_request,
    stable_prefix,
)

SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshots" / "prompt_surface.json"
SNAPSHOT_VERSION = 1

#: Canonical fixture: the one prompt surface the snapshot pins.
#: The volatile layer carries a FIXED timestamp so the snapshot
#: is deterministic; a real session passes the live time instead.
FIXTURE_STABLE = (
    "# Identity\n\nYou are Puspa, a personal AI agent. Answer in the "
    "user's language.\n\n"
    "# Tool guidance\n\nCall tools to act. Never invent a tool result.\n\n"
    "# Skill: price-compare\n\nCompare new-product prices across marketplaces."
)
FIXTURE_CONTEXT = (
    "# Project context\n\nWorking directory: /home/user/puspa-demo\n\n"
    "Use it as the default root for project files."
)
FIXTURE_VOLATILE = (
    "# Memory\n\nThe user prefers short replies.\n\n"
    "# Profile\n\nTimezone: Asia/Jakarta.\n\n"
    "# Timestamp\n\n2026-11-18T15:00:00+07:00"
)
FIXTURE_MESSAGES = [
    {"role": "user", "content": "Find the cheapest price for a rice cooker."},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "search_prices",
                    "arguments": '{"query": "rice cooker"}',
                },
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "Rp 350.000 at Toko A."},
]
FIXTURE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_prices",
            "description": "Search new-product prices across marketplaces.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_bill",
            "description": "Read a utility bill by customer number.",
            "parameters": {
                "type": "object",
                "properties": {"customer": {"type": "string"}},
                "required": ["customer"],
            },
        },
    },
]
FIXTURE_MODEL = "openrouter/test-model"


def build_fixture_surface(builder=None):
    """The canonical surface: fixture layers + messages + tools."""
    builder = builder or PromptBuilder(tools=FIXTURE_TOOLS)
    return builder.build(
        FIXTURE_MESSAGES,
        stable=FIXTURE_STABLE,
        context=FIXTURE_CONTEXT,
        volatile=FIXTURE_VOLATILE,
    )


def render_snapshot(surface, *, tools) -> dict:
    """Snapshot dict for a surface (what the committed file holds)."""
    return {
        "fixture_version": SNAPSHOT_VERSION,
        "system_prompt": surface.system_prompt,
        "cache_breakpoint": surface.cache_breakpoint,
        "snapshot_hash": surface.snapshot_hash,
        "tools": tools,
        "messages": surface.messages,
    }


def write_snapshot(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_snapshot(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- Layer order and the frozen stable prefix ----------------------


def test_layer_order_is_fixed_stable_then_context_then_volatile():
    surface = build_fixture_surface()
    stable_at = surface.system_prompt.index("# Identity")
    context_at = surface.system_prompt.index("# Project context")
    volatile_at = surface.system_prompt.index("# Memory")
    assert stable_at < context_at < volatile_at
    assert surface.system_prompt.count("\n\n---\n\n") == 2


def test_stable_layer_byte_identical_across_turns_in_one_session():
    session = PromptSession(stable=FIXTURE_STABLE, tools=FIXTURE_TOOLS)
    first = session.build(FIXTURE_MESSAGES, context=FIXTURE_CONTEXT, volatile=FIXTURE_VOLATILE)
    second = session.build(
        FIXTURE_MESSAGES,
        context="# Project context\n\nWorking directory: /other/project",
        volatile="# Memory\n\nThe user changed their mind.\n\n# Timestamp\n\n2026-11-18T15:05:00+07:00",
    )
    assert first.stable_prefix == second.stable_prefix
    assert first.stable_prefix.encode() == second.stable_prefix.encode()
    assert first.stable_prefix == FIXTURE_STABLE
    assert first.cache_breakpoint == second.cache_breakpoint
    assert first.snapshot_hash != second.snapshot_hash  # volatile changed
    assert session.turn_count == 2


def test_volatile_or_context_change_does_not_move_cache_breakpoint():
    builder = PromptBuilder(tools=FIXTURE_TOOLS)
    base = builder.build(
        FIXTURE_MESSAGES,
        stable=FIXTURE_STABLE,
        context=FIXTURE_CONTEXT,
        volatile=FIXTURE_VOLATILE,
    )
    changed = builder.build(
        FIXTURE_MESSAGES,
        stable=FIXTURE_STABLE,
        context=FIXTURE_CONTEXT + "\n\n# Extra file\n\nNotes.",
        volatile="# Memory\n\nNothing remembered yet.",
    )
    assert changed.cache_breakpoint == base.cache_breakpoint
    assert changed.cache_breakpoint == len(FIXTURE_STABLE)
    assert changed.system_prompt[: changed.cache_breakpoint] == FIXTURE_STABLE


def test_empty_layers_render_without_dangling_separators():
    surface = PromptBuilder().build([], stable="", context=None, volatile=None)
    assert surface.system_prompt == ""
    assert surface.cache_breakpoint == 0
    stable_only = PromptBuilder().build([], stable="Only stable.")
    assert stable_only.system_prompt == "Only stable."
    assert stable_only.cache_breakpoint == len("Only stable.")
    assert stable_only.stable_prefix == "Only stable."


def test_builder_freezes_stable_and_records_refused_change():
    builder = PromptBuilder(tools=FIXTURE_TOOLS)
    first = builder.build(FIXTURE_MESSAGES, stable=FIXTURE_STABLE, volatile=FIXTURE_VOLATILE)
    second = builder.build(
        FIXTURE_MESSAGES,
        stable="# Identity\n\nYou are someone else now.",
        volatile=FIXTURE_VOLATILE,
    )
    assert second.stable_prefix == FIXTURE_STABLE
    assert second.system_prompt == first.system_prompt
    assert len(builder.pending_changes) == 1
    refused = builder.pending_changes[0]
    assert refused.kind == "replace_stable"
    assert refused.detail["frozen"] == FIXTURE_STABLE
    fresh = builder.new_session()
    changed = fresh.build(
        FIXTURE_MESSAGES,
        stable="# Identity\n\nYou are someone else now.",
        volatile=FIXTURE_VOLATILE,
    )
    assert changed.stable_prefix.startswith("# Identity\n\nYou are someone else")


# --- The builder never compresses ----------------------------------


def test_builder_passes_compressed_messages_through_untouched():
    # What the G19 context compressor hands over: a summary in place
    # of the middle turns, tool pairs kept together, ids intact.
    compressed = [
        {"role": "user", "content": "Find the cheapest price for a rice cooker."},
        {
            "role": "assistant",
            "content": "[Summary of 12 earlier turns: prices compared.]",
        },
        {"role": "user", "content": "And check the electricity bill."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {
                        "name": "read_bill",
                        "arguments": '{"customer": "12345"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_9", "content": "Rp 120.000 due."},
    ]
    before = [dict(message) for message in compressed]
    builder = PromptBuilder(tools=FIXTURE_TOOLS)
    surface = builder.build(compressed, stable=FIXTURE_STABLE, volatile=FIXTURE_VOLATILE)
    assert surface.messages == compressed
    assert surface.messages is not compressed  # new list, same dicts
    assert all(a is b for a, b in zip(surface.messages, compressed))
    assert compressed == before  # input untouched
    # Compression mid-session moves neither the prefix nor the breakpoint.
    assert surface.cache_breakpoint == len(FIXTURE_STABLE)
    assert surface.stable_prefix == FIXTURE_STABLE


def test_message_contract_is_validated_not_rewritten():
    builder = PromptBuilder()
    with pytest.raises(ValueError, match="tool_call_id"):
        builder.build(
            [{"role": "tool", "content": "orphan result"}],
            stable=FIXTURE_STABLE,
        )
    with pytest.raises(ValueError, match="role must be one of"):
        builder.build([{"role": "observer", "content": "hello"}], stable=FIXTURE_STABLE)
    with pytest.raises(ValueError, match="must be a dict"):
        builder.build(["not a dict"], stable=FIXTURE_STABLE)


# --- Snapshot --------------------------------------------------------


def test_snapshot_matches_committed_file():
    surface = build_fixture_surface()
    if os.environ.get("PROMPT_SURFACE_REGEN") == "1":
        write_snapshot(SNAPSHOT_PATH, render_snapshot(surface, tools=FIXTURE_TOOLS))
    snapshot = load_snapshot(SNAPSHOT_PATH)
    assert snapshot["fixture_version"] == SNAPSHOT_VERSION
    assert snapshot["system_prompt"] == surface.system_prompt
    assert snapshot["cache_breakpoint"] == surface.cache_breakpoint
    assert snapshot["snapshot_hash"] == surface.snapshot_hash
    assert snapshot["tools"] == FIXTURE_TOOLS
    assert snapshot["messages"] == FIXTURE_MESSAGES


def test_snapshot_fails_when_prompt_or_tools_change():
    snapshot = load_snapshot(SNAPSHOT_PATH)
    changed_prompt = PromptBuilder(tools=FIXTURE_TOOLS).build(
        FIXTURE_MESSAGES,
        stable=FIXTURE_STABLE + "\n\n# New rule\n\nBe brief.",
        context=FIXTURE_CONTEXT,
        volatile=FIXTURE_VOLATILE,
    )
    assert changed_prompt.snapshot_hash != snapshot["snapshot_hash"]
    changed_tools = PromptBuilder(
        tools=[
            dict(
                FIXTURE_TOOLS[0],
                function={
                    **FIXTURE_TOOLS[0]["function"],
                    "description": "Search prices anywhere.",
                },
            ),
            FIXTURE_TOOLS[1],
        ]
    ).build(
        FIXTURE_MESSAGES,
        stable=FIXTURE_STABLE,
        context=FIXTURE_CONTEXT,
        volatile=FIXTURE_VOLATILE,
    )
    assert changed_tools.snapshot_hash != snapshot["snapshot_hash"]
    assert changed_tools.system_prompt == snapshot["system_prompt"]
    # Same prompt, same tools, same messages: same hash.
    rebuilt = build_fixture_surface()
    assert rebuilt.snapshot_hash == snapshot["snapshot_hash"]


def test_snapshot_regeneration_round_trips(tmp_path):
    surface = build_fixture_surface()
    path = tmp_path / "prompt_surface.json"
    write_snapshot(path, render_snapshot(surface, tools=FIXTURE_TOOLS))
    assert load_snapshot(path)["snapshot_hash"] == surface.snapshot_hash


# --- Deferral: next session unless explicit opt-in -------------------


def test_deferred_skill_change_held_to_next_session():
    session = PromptSession(stable=FIXTURE_STABLE, tools=FIXTURE_TOOLS)
    outcome = session.request_change(
        "add_skill",
        {"skill": "billing", "text": "Pay PLN and PDAM bills on time."},
    )
    assert outcome == "deferred"
    assert len(session.pending_changes) == 1
    current = session.build(FIXTURE_MESSAGES, context=FIXTURE_CONTEXT, volatile=FIXTURE_VOLATILE)
    assert "billing" not in current.system_prompt
    nxt = session.next_session()
    assert session.pending_changes == []
    applied = nxt.build(FIXTURE_MESSAGES, context=FIXTURE_CONTEXT, volatile=FIXTURE_VOLATILE)
    assert "# Skill: billing" in applied.system_prompt
    assert "Pay PLN and PDAM bills on time." in applied.system_prompt
    # The old session keeps its frozen stable layer until dropped.
    again = session.build(FIXTURE_MESSAGES, context=FIXTURE_CONTEXT, volatile=FIXTURE_VOLATILE)
    assert "billing" not in again.system_prompt


def test_explicit_opt_in_applies_skill_change_immediately():
    session = PromptSession(stable=FIXTURE_STABLE, tools=FIXTURE_TOOLS)
    outcome = session.request_change(
        "add_skill",
        {"skill": "billing", "text": "Pay PLN and PDAM bills on time."},
        apply_now=True,
    )
    assert outcome == "applied"
    assert session.pending_changes == []
    surface = session.build(FIXTURE_MESSAGES, context=FIXTURE_CONTEXT, volatile=FIXTURE_VOLATILE)
    assert "# Skill: billing" in surface.system_prompt


def test_deferred_model_switch_applies_next_session_only():
    session = PromptSession(stable=FIXTURE_STABLE, tools=FIXTURE_TOOLS, model=FIXTURE_MODEL)
    assert session.request_change("set_model", {"model": "other/model"}) == "deferred"
    assert session.model == FIXTURE_MODEL
    nxt = session.next_session()
    assert nxt.model == "other/model"
    assert session.model == FIXTURE_MODEL


def test_opt_in_model_switch_applies_immediately():
    session = PromptSession(stable=FIXTURE_STABLE, tools=FIXTURE_TOOLS, model=FIXTURE_MODEL)
    assert (
        session.request_change("set_model", {"model": "other/model"}, apply_now=True) == "applied"
    )
    assert session.model == "other/model"


def test_deferred_remove_skill_applies_next_session():
    session = PromptSession(stable=FIXTURE_STABLE, tools=FIXTURE_TOOLS)
    session.request_change("add_skill", {"skill": "billing", "text": "Pay bills."}, apply_now=True)
    assert session.request_change("remove_skill", {"skill": "billing"}) == "deferred"
    current = session.build([], context=None, volatile=None)
    assert "# Skill: billing" in current.system_prompt
    nxt = session.next_session()
    applied = nxt.build([], context=None, volatile=None)
    assert "# Skill: billing" not in applied.system_prompt
    assert applied.stable_prefix == FIXTURE_STABLE


def test_deferred_change_queue_is_fifo_and_drains():
    queue = DeferredChangeQueue()
    assert not queue
    queue.request(DeferredChange(kind="set_model", detail={"model": "a"}))
    queue.request(DeferredChange(kind="set_model", detail={"model": "b"}))
    assert len(queue) == 2
    pending = queue.pending()
    assert [change.detail["model"] for change in pending] == ["a", "b"]
    assert [change.seq for change in pending] == [1, 2]
    assert [change.detail["model"] for change in queue.drain()] == ["a", "b"]
    assert not queue and queue.pending() == []
    with pytest.raises(ValueError, match="unknown deferred-change kind"):
        DeferredChange(kind="explode")


# --- Cache markers and both provider formats ------------------------


def test_anthropic_blocks_mark_only_the_stable_prefix():
    surface = build_fixture_surface()
    blocks = anthropic_system_blocks(surface)
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == FIXTURE_STABLE
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]
    assert "".join(block["text"] for block in blocks) == surface.system_prompt
    assert stable_prefix(surface) == FIXTURE_STABLE


def test_anthropic_blocks_single_block_when_prompt_is_all_stable():
    surface = PromptBuilder().build([], stable="Only stable.")
    blocks = anthropic_system_blocks(surface)
    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"] == "Only stable."
    empty = PromptBuilder().build([], stable="")
    assert anthropic_system_blocks(empty) == [{"type": "text", "text": ""}]


def test_openai_messages_put_system_first_and_keep_history():
    surface = build_fixture_surface()
    messages = openai_messages(surface)
    assert messages[0] == {"role": "system", "content": surface.system_prompt}
    assert messages[1:] == FIXTURE_MESSAGES
    assert surface.as_messages() == messages


def test_surface_feeds_both_formats_through_translate():
    surface = build_fixture_surface()
    openai_req = openai_request(surface, model=FIXTURE_MODEL, tools=FIXTURE_TOOLS)
    to_anthropic = translate_request(openai_req, source="openai", target="anthropic")
    assert to_anthropic.payload["system"] == surface.system_prompt
    assert to_anthropic.payload["messages"][0]["role"] == "user"
    assert to_anthropic.report.dropped == []
    anthropic_tools = to_anthropic.payload["tools"]
    assert [tool["name"] for tool in anthropic_tools] == [
        "search_prices",
        "read_bill",
    ]
    anthropic_req = anthropic_request(surface, model=FIXTURE_MODEL, tools=anthropic_tools)
    blocks = anthropic_req["system"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    back = translate_request(anthropic_req, source="anthropic", target="openai")
    assert back.payload["messages"][0] == {
        "role": "system",
        "content": surface.system_prompt,
    }
    assert back.report.dropped == []


def test_anthropic_request_rejects_system_message_in_history():
    surface = PromptBuilder().build(
        [{"role": "system", "content": "stray system message"}],
        stable=FIXTURE_STABLE,
    )
    with pytest.raises(ValueError, match=r"system.*messages"):
        anthropic_request(surface, model=FIXTURE_MODEL)


def test_to_prompt_text_flattens_surface_for_single_string_complete():
    surface = build_fixture_surface()
    text = surface.to_prompt_text()
    assert text.startswith(surface.system_prompt)
    assert "User: Find the cheapest price for a rice cooker." in text
    assert "Assistant (calls search_prices):" in text
    assert "Tool result (call_1): Rp 350.000 at Toko A." in text
