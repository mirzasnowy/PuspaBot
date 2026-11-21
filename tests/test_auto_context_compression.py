"""Tests for automatic context compression (puspa/sessions/auto_compress.py).

AST-03: the G19 compressor could summarize old turns, but nothing
called it on its own. These tests prove the automatic trigger: a
session filled past the threshold compresses by itself when a turn
is appended, the oldest turns become one summary, the newest turns
stay whole, the key facts from the old turns are still recallable
from the summary, and nothing is lost without a trace (the parent
keeps every original turn; the summary lives in a lineage child).

Deterministic throughout: the default summarizer is the extractive
fallback (no live model, no key, no network). No test here touches
the live model loop; the module is not wired into nanobot's
AgentLoop (see the module docstring for why).
"""

from __future__ import annotations

import pytest

from puspa.sessions import open_persistence
from puspa.sessions.auto_compress import AutoCompressor, maybe_compress_session
from puspa.sessions.lineage import SUMMARY_MARKER, find_summary_message

# The auto-compression tests open the session gateway, whose
# single-writer lock lives in Postgres (see test_session_gateway.py).
# Same declaration as there (E-17).
pytestmark = pytest.mark.integration

#: Small limit so a short session crosses the 50% preflight line.
CONTEXT_LIMIT = 600

#: Tail messages kept whole by the compressor in these tests.
PROTECT_LAST_N = 4

FILLER = "The customer asked about the delivery schedule and the agent explained the cutoff. "


def _filler_text(n_chars: int) -> str:
    repeats = n_chars // len(FILLER) + 1
    return (FILLER * repeats)[:n_chars]


def _fact_turns() -> list[tuple[str, str]]:
    """Early turns carrying the facts the summary must preserve."""
    return [
        (
            "user",
            (
                "My name is Nadia, customer number 512345678901, and my PLN bill "
                "is Rp1.250.000 due 2026-11-18."
            ),
        ),
        (
            "assistant",
            "Noted, Nadia: bill Rp1.250.000 for customer 512345678901, due 2026-11-18.",
        ),
        (
            "user",
            "Decision: pay the PLN bill first, Rp1.250.000, before the Rp250.000 water bill.",
        ),
        (
            "assistant",
            "Agreed, I will call back tomorrow, 2026-11-19, with the payment proof.",
        ),
    ]


def _filler_turns(n_turns: int) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for turn in range(n_turns):
        role = "user" if turn % 2 == 0 else "assistant"
        turns.append((role, f"Filler turn {turn}: {_filler_text(120)}"))
    return turns


def _seed_session(gateway, session_id, *, n_filler: int = 10) -> list[tuple[str, str]]:
    """Seed fact turns plus filler; returns the turns in order."""
    turns = _fact_turns() + _filler_turns(n_filler)
    gateway.append_messages(session_id, turns)
    return turns


def _compressor(gateway, **kwargs) -> AutoCompressor:
    options = {"context_limit": CONTEXT_LIMIT, "protect_last_n": PROTECT_LAST_N}
    options.update(kwargs)
    return AutoCompressor(gateway, **options)


# ---------------------------------------------------------------------------
# Below threshold: nothing happens, no trace left behind
# ---------------------------------------------------------------------------


def test_below_threshold_is_unchanged(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("small")
        gateway.append_messages(session["id"], _fact_turns())
        outcome = _compressor(gateway).maybe_compress(session["id"])
        assert outcome["compressed"] is False
        assert outcome["child_session_id"] is None
        assert outcome["stats"] is None
        stored = gateway.get_session(session["id"])["messages"]
        assert [m["content"] for m in outcome["messages"]] == [m["content"] for m in stored]
        assert gateway.children(session["id"]) == []


def test_build_model_messages_returns_original_turns_below_threshold(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("small")
        gateway.append_messages(session["id"], _fact_turns())
        messages = _compressor(gateway).build_model_messages(session["id"])
        assert [m["content"] for m in messages] == [c for _, c in _fact_turns()]
        assert all(m["content"].startswith(SUMMARY_MARKER) is False for m in messages)


# ---------------------------------------------------------------------------
# Done criterion: fill past the threshold, old turns auto-summarize
# ---------------------------------------------------------------------------


def test_append_turn_auto_compresses_past_threshold(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        auto = _compressor(gateway)
        compressed_seen = False
        last_outcome = None
        for role, content in _fact_turns() + _filler_turns(10):
            last_outcome = auto.append_turn(session["id"], role, content)
            compressed_seen = compressed_seen or last_outcome["compressed"]
        # The session grew past the 50% line, so at least one append
        # compressed on its own, with no explicit compress call.
        assert compressed_seen is True
        assert last_outcome is not None and last_outcome["compressed"] is True
        assert last_outcome["stats"]["threshold_hit"] in ("preflight", "gateway")
        assert last_outcome["stats"]["tokens_after"] < last_outcome["stats"]["tokens_before"]
        assert last_outcome["child_session_id"] is not None


def test_old_turns_summarized_new_turns_intact(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        turns = _seed_session(gateway, session["id"], n_filler=10)
        outcome = _compressor(gateway).maybe_compress(session["id"])
        assert outcome["compressed"] is True
        messages = outcome["messages"]
        # Exactly one summary message, carrying the marker.
        summaries = [m for m in messages if m["content"].startswith(SUMMARY_MARKER)]
        assert len(summaries) == 1
        # The protected tail is the original turns, verbatim.
        tail = messages[1:]
        assert [m["content"] for m in tail] == [c for _, c in turns[-len(tail) :]]
        assert len(tail) >= PROTECT_LAST_N
        # The oldest turns are gone from the live context: only the
        # summary stands in for them.
        assert all(c not in [m["content"] for m in messages] for _, c in turns[:4])


def test_key_facts_recallable_from_summary(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        _seed_session(gateway, session["id"], n_filler=10)
        outcome = _compressor(gateway).maybe_compress(session["id"])
        assert outcome["compressed"] is True
        summary = find_summary_message(outcome["messages"])
        assert summary is not None
        # Name, customer number, amount, due date, decision, promise:
        # every key fact from the old turns survives in the summary.
        for fact in (
            "Nadia",
            "512345678901",
            "Rp1.250.000",
            "2026-11-18",
            "pay the PLN bill first",
            "call back tomorrow",
            "2026-11-19",
        ):
            assert fact in summary, f"key fact lost from summary: {fact!r}"


def test_system_message_survives_compression(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        gateway.append_message(session["id"], "system", "You are Puspa.")
        _seed_session(gateway, session["id"], n_filler=10)
        outcome = _compressor(gateway).maybe_compress(session["id"])
        assert outcome["compressed"] is True
        assert outcome["messages"][0] == {"role": "system", "content": "You are Puspa."}


# ---------------------------------------------------------------------------
# No deletion without a trace: parent keeps originals, child holds summary
# ---------------------------------------------------------------------------


def test_parent_keeps_every_original_turn(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        turns = _seed_session(gateway, session["id"], n_filler=10)
        outcome = _compressor(gateway).maybe_compress(session["id"])
        assert outcome["compressed"] is True
        stored = gateway.get_session(session["id"])["messages"]
        assert [m["content"] for m in stored] == [c for _, c in turns]
        assert len(stored) == len(turns)


def test_compression_recorded_as_lineage_child(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        _seed_session(gateway, session["id"], n_filler=10)
        outcome = _compressor(gateway).maybe_compress(session["id"])
        assert outcome["compressed"] is True
        children = gateway.children(session["id"])
        assert len(children) == 1
        assert children[0]["kind"] == "compression"
        assert children[0]["child_session_id"] == outcome["child_session_id"]
        assert "auto context compression" in (children[0]["reason"] or "")
        child = gateway.get_session(outcome["child_session_id"])
        assert child["parent_id"] == session["id"]
        assert len(child["messages"]) == 1
        assert child["messages"][0]["content"] == find_summary_message(outcome["messages"])
        assert child["messages"][0]["role"] == "assistant"


def test_repeated_compress_reuses_identical_child(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        _seed_session(gateway, session["id"], n_filler=10)
        auto = _compressor(gateway)
        first = auto.maybe_compress(session["id"])
        second = auto.maybe_compress(session["id"])
        assert first["compressed"] is True
        assert second["compressed"] is True
        # Same middle, same deterministic summary: no duplicate child.
        assert second["child_session_id"] == first["child_session_id"]
        assert len(gateway.children(session["id"])) == 1
        first_summary = find_summary_message(first["messages"])
        assert first_summary == find_summary_message(second["messages"])


def test_appended_turn_is_stored_even_when_compressed(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        auto = _compressor(gateway)
        outcome = None
        for role, content in _fact_turns() + _filler_turns(10):
            outcome = auto.append_turn(session["id"], role, content)
        assert outcome is not None and outcome["compressed"] is True
        stored = gateway.get_session(session["id"])["messages"]
        assert stored[-1]["content"] == _filler_turns(10)[-1][1]
        assert outcome["message"]["content"] == _filler_turns(10)[-1][1]


# ---------------------------------------------------------------------------
# Entry points and failure behaviour
# ---------------------------------------------------------------------------


def test_maybe_compress_session_helper(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        _seed_session(gateway, session["id"], n_filler=10)
        outcome = maybe_compress_session(
            gateway,
            session["id"],
            context_limit=CONTEXT_LIMIT,
            protect_last_n=PROTECT_LAST_N,
        )
        assert outcome["compressed"] is True
        assert outcome["child_session_id"] is not None
        assert find_summary_message(outcome["messages"]) is not None


def test_raising_summarizer_falls_back_deterministically(tmp_path):
    def _raising(text: str) -> str:
        raise RuntimeError("no key available")

    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        _seed_session(gateway, session["id"], n_filler=10)
        auto = _compressor(gateway, summarize=_raising)
        outcome = auto.maybe_compress(session["id"])
        assert outcome["compressed"] is True
        assert outcome["stats"]["summary_fallback"] is not None
        assert "RuntimeError" in outcome["stats"]["summary_fallback"]
        summary = find_summary_message(outcome["messages"])
        assert summary is not None
        assert "Rp1.250.000" in summary
        assert "512345678901" in summary


def test_live_context_messages_are_plain_chat_shape(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("grows")
        _seed_session(gateway, session["id"], n_filler=10)
        outcome = _compressor(gateway).maybe_compress(session["id"])
        assert outcome["compressed"] is True
        for message in outcome["messages"]:
            # No store-only keys leak into the model-bound list.
            assert "seq" not in message
            assert "id" not in message
            assert "created_at" not in message
            assert set(message) <= {"role", "content", "tool_calls", "tool_call_id"}
