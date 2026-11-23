"""Turn-loop takeover gate tests (P-09).

No live provider, no network: every model call here is an
injected fake, like ``tests/test_turn_loop.py``. Covers the
gate the loop consults before every model call and every
registry tool call: a held turn ends as ``interrupted`` with a
takeover note, no further tool call runs, and the history is
restored to before the turn; releasing the hold lets the same
loop run again; a hold check that fails fails open, like the
intent advisor and the autonomy dial.
"""

from __future__ import annotations

import sqlite3

from puspa.turns.config import TurnConfig
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply


def _text_reply(text: str = "Done.") -> ModelReply:
    return ModelReply(text=text, input_tokens=10, output_tokens=5)


def _tool_reply(name: str, arguments: dict | None = None) -> ModelReply:
    return ModelReply(
        text="",
        tool_calls=[{"id": "call-1", "name": name, "arguments": arguments or {}}],
        input_tokens=10,
        output_tokens=5,
    )


def test_held_turn_ends_interrupted_before_first_model_call():
    def model_call(entry, wire_messages, tools):
        raise AssertionError("no model call may run while the agent is held")

    loop = TurnLoop(model_call=model_call, takeover_hold=lambda agent_id: True)
    history = [InternalMessage.user("Work on this.")]
    result = loop.run_turn(history, agent_id="agen-1")
    assert result.status == "interrupted"
    assert result.text == ""
    assert result.messages == history
    assert "taken over" in result.message
    assert any(
        event["type"] == "turn_interrupted"
        and "taken over" in str(event["content"].get("reason", ""))
        for event in loop.event_memory()
    )


def test_hold_landing_mid_turn_stops_the_next_tool_call():
    calls = {"model": 0, "tool": 0}
    held = {"now": False}

    def model_call(entry, wire_messages, tools):
        calls["model"] += 1
        return _tool_reply("search_listings")

    def tool_call(name, arguments):
        calls["tool"] += 1
        held["now"] = True  # the user takes over right after the first tool runs
        return {"ok": True}

    loop = TurnLoop(
        model_call=model_call,
        tool_call=tool_call,
        takeover_hold=lambda agent_id: held["now"],
        config=TurnConfig(max_iterations=10),
    )
    result = loop.run_turn([InternalMessage.user("Find this.")], agent_id="agen-1")
    assert result.status == "interrupted"
    assert "taken over" in result.message
    assert calls == {"model": 1, "tool": 1}
    # The second tool call never ran, and no second model call did either.
    assert result.iterations_used == 1


def test_released_hold_lets_the_same_loop_run_again():
    held = {"now": True}

    def model_call(entry, wire_messages, tools):
        return _text_reply("Done after release.")

    loop = TurnLoop(model_call=model_call, takeover_hold=lambda agent_id: held["now"])
    history = [InternalMessage.user("Work on this.")]
    held_result = loop.run_turn(history, agent_id="agen-1")
    assert held_result.status == "interrupted"
    held["now"] = False
    result = loop.run_turn(history, agent_id="agen-1")
    assert result.status == "completed"
    assert result.text == "Done after release."


def test_unheld_turn_runs_exactly_as_before():
    calls = {"model": 0, "tool": 0}

    def model_call(entry, wire_messages, tools):
        calls["model"] += 1
        if calls["model"] == 1:
            return _tool_reply("search_listings")
        return _text_reply("Done.")

    def tool_call(name, arguments):
        calls["tool"] += 1
        return {"ok": True}

    loop = TurnLoop(
        model_call=model_call,
        tool_call=tool_call,
        takeover_hold=lambda agent_id: False,
    )
    result = loop.run_turn([InternalMessage.user("Find this.")], agent_id="agen-1")
    assert result.status == "completed"
    assert calls == {"model": 2, "tool": 1}


def test_broken_hold_check_fails_open():
    def failing_hold(agent_id):
        # The error aliases in puspa.db are (sqlite, psycopg)
        # tuples so one except catches both backends; a tuple
        # cannot be raised, so the fake raises the concrete
        # SQLite error the hold store would really raise here.
        raise sqlite3.OperationalError("the hold store is down")

    def model_call(entry, wire_messages, tools):
        return _text_reply("Still working.")

    loop = TurnLoop(model_call=model_call, takeover_hold=failing_hold)
    result = loop.run_turn([InternalMessage.user("Hello")], agent_id="agen-1")
    assert result.status == "completed"
    assert result.text == "Still working."


def test_no_hold_callable_means_no_hold():
    loop = TurnLoop(model_call=lambda entry, wire, tools: _text_reply("Hello."))
    result = loop.run_turn([InternalMessage.user("Hello")], agent_id="agen-1")
    assert result.status == "completed"
