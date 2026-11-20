"""Tests for the per-area autonomy dial (Kosmos K11).

Three layers, matching the wiring:

- Pure dial rules (in-memory, deterministic, no database): the
  six areas, the three stages, the slow rise / fast fall, the
  emergency drop to 0.0 on a serious failure in an irreversible
  area, the accounts cap, the audit history, and the tool-to-area
  mapping.
- Turn-loop wiring (fake model, no network, no database): the
  loop consults the dial before dispatching a registry tool call
  (``propose`` blocks the call, ``do_and_show`` / ``do_silently``
  run it), the executed outcome feeds the dial back as that
  area's calibration, and a broken dial fails open.
"""

from __future__ import annotations

import json

import pytest

from puspa.autonomy.dial import (
    AREAS,
    INITIAL_SCORE,
    IRREVERSIBLE_AREAS,
    MAX_HISTORY_PER_AREA,
    STAGE_DO_AND_SHOW,
    STAGE_DO_SILENTLY,
    STAGE_PROPOSE,
    AutonomyDial,
    area_for_tool,
    dial_ask_payload,
)
from puspa.intent import ActionSpec, IntentTurnAdvisor
from puspa.turns.config import TurnConfig
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply


def _climb(dial: AutonomyDial, area: str, correct: int) -> None:
    """Feed ``correct`` correct outcomes into one area."""
    for _ in range(correct):
        dial.record_outcome(area, True)


# ---------------------------------------------------------------------------
# Pure dial: areas, stages, starting point
# ---------------------------------------------------------------------------


def test_all_areas_start_at_propose_with_no_trust_assumed():
    dial = AutonomyDial()
    assert set(AREAS) == {
        "money",
        "messaging",
        "files",
        "scheduling",
        "system",
        "accounts",
        "shopping",
    }
    for area in AREAS:
        assert dial.score(area) == pytest.approx(INITIAL_SCORE)
        assert dial.stage(area) == STAGE_PROPOSE
        assert dial.accuracy(area) is None
        decision = dial.advise(area)
        assert decision.stage == STAGE_PROPOSE
        assert decision.run is False
        assert decision.show is False


def test_unknown_area_raises_everywhere():
    dial = AutonomyDial()
    with pytest.raises(ValueError, match="unknown dial area"):
        dial.advise("teleportation")
    with pytest.raises(ValueError, match="unknown dial area"):
        dial.score("teleportation")
    with pytest.raises(ValueError, match="unknown dial area"):
        dial.record_outcome("teleportation", True)
    with pytest.raises(ValueError, match="unknown dial area"):
        dial.record_serious_failure("teleportation")


def test_bad_initial_score_is_rejected():
    with pytest.raises(ValueError, match="initial score"):
        AutonomyDial(initial_score=1.5)


def test_five_correct_outcomes_reach_do_and_show():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 4)
    assert dial.stage("scheduling") == STAGE_PROPOSE
    _climb(dial, "scheduling", 1)
    assert dial.score("scheduling") == pytest.approx(0.6131, abs=1e-3)
    assert dial.stage("scheduling") == STAGE_DO_AND_SHOW
    decision = dial.advise("scheduling")
    assert decision.stage == STAGE_DO_AND_SHOW
    assert decision.run is True
    assert decision.show is True


def test_twenty_four_correct_outcomes_reach_do_silently():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 23)
    assert dial.stage("scheduling") == STAGE_DO_AND_SHOW
    _climb(dial, "scheduling", 1)
    assert dial.score("scheduling") == pytest.approx(0.8540, abs=1e-3)
    assert dial.stage("scheduling") == STAGE_DO_SILENTLY
    decision = dial.advise("scheduling")
    assert decision.stage == STAGE_DO_SILENTLY
    assert decision.run is True
    assert decision.show is False


def test_one_wrong_outcome_drops_from_silent_to_show():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 24)
    assert dial.stage("scheduling") == STAGE_DO_SILENTLY
    dial.record_outcome("scheduling", False)
    assert dial.score("scheduling") == pytest.approx(0.7040, abs=1e-3)
    assert dial.stage("scheduling") == STAGE_DO_AND_SHOW


def test_two_wrong_outcomes_drop_from_silent_to_propose():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 24)
    dial.record_outcome("scheduling", False)
    dial.record_outcome("scheduling", False)
    assert dial.score("scheduling") == pytest.approx(0.5540, abs=1e-3)
    assert dial.stage("scheduling") == STAGE_PROPOSE
    assert dial.advise("scheduling").run is False


def test_areas_move_independently():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 24)
    assert dial.stage("scheduling") == STAGE_DO_SILENTLY
    for area in ("money", "messaging", "files", "system", "accounts"):
        assert dial.stage(area) == STAGE_PROPOSE


# ---------------------------------------------------------------------------
# Pure dial: the emergency drop
# ---------------------------------------------------------------------------


def test_serious_failure_in_irreversible_area_drops_to_zero_at_once():
    dial = AutonomyDial()
    _climb(dial, "messaging", 24)
    assert dial.stage("messaging") == STAGE_DO_SILENTLY
    decision = dial.record_serious_failure("messaging", note="message went to the wrong person")
    assert dial.score("messaging") == 0.0
    assert dial.stage("messaging") == STAGE_PROPOSE
    assert decision.stage == STAGE_PROPOSE
    assert decision.run is False
    assert dial.state("messaging")["serious_failures"] == 1
    # Other areas are untouched by the drop.
    assert dial.score("scheduling") == pytest.approx(INITIAL_SCORE)


def test_serious_failure_drops_immediately_in_every_irreversible_area():
    dial = AutonomyDial()
    # Accounts and money are irreversible too, but capped at
    # propose, so they never climb to do-silently — they are
    # covered separately below.
    for area in sorted(IRREVERSIBLE_AREAS - {"accounts", "money"}):
        _climb(dial, area, 24)
        assert dial.stage(area) == STAGE_DO_SILENTLY
        dial.record_serious_failure(area)
        assert dial.score(area) == 0.0
        assert dial.stage(area) == STAGE_PROPOSE
    _climb(dial, "accounts", 24)
    assert dial.score("accounts") == pytest.approx(0.59, abs=1e-9)
    dial.record_serious_failure("accounts")
    assert dial.score("accounts") == 0.0
    assert dial.stage("accounts") == STAGE_PROPOSE
    _climb(dial, "money", 24)
    assert dial.score("money") == pytest.approx(0.59, abs=1e-9)
    assert dial.stage("money") == STAGE_PROPOSE
    dial.record_serious_failure("money")
    assert dial.score("money") == 0.0
    assert dial.stage("money") == STAGE_PROPOSE


def test_serious_failure_in_reversible_area_drops_by_point_three_not_to_zero():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 24)
    assert dial.score("scheduling") == pytest.approx(0.8540, abs=1e-3)
    decision = dial.record_serious_failure("scheduling", note="reminder fired at 3am")
    assert dial.score("scheduling") == pytest.approx(0.5540, abs=1e-3)
    assert decision.stage == STAGE_PROPOSE
    assert dial.state("scheduling")["serious_failures"] == 1


def test_rebuilding_after_a_drop_takes_eighteen_correct_to_show():
    dial = AutonomyDial()
    _climb(dial, "messaging", 24)
    dial.record_serious_failure("messaging")
    assert dial.score("messaging") == 0.0
    _climb(dial, "messaging", 17)
    assert dial.stage("messaging") == STAGE_PROPOSE
    _climb(dial, "messaging", 1)
    assert dial.score("messaging") == pytest.approx(0.6028, abs=1e-3)
    assert dial.stage("messaging") == STAGE_DO_AND_SHOW


def test_rebuilding_after_a_drop_takes_thirty_seven_correct_to_silent():
    dial = AutonomyDial()
    _climb(dial, "messaging", 24)
    dial.record_serious_failure("messaging")
    _climb(dial, "messaging", 36)
    assert dial.stage("messaging") == STAGE_DO_AND_SHOW
    _climb(dial, "messaging", 1)
    assert dial.score("messaging") == pytest.approx(0.8501, abs=1e-3)
    assert dial.stage("messaging") == STAGE_DO_SILENTLY


def test_accounts_area_never_leaves_propose():
    dial = AutonomyDial()
    _climb(dial, "accounts", 24)
    assert dial.score("accounts") == pytest.approx(0.59, abs=1e-9)
    assert dial.stage("accounts") == STAGE_PROPOSE
    assert dial.advise("accounts").run is False
    # The outcomes are still recorded, even though the stage never moves.
    assert dial.state("accounts")["correct"] == 24


# ---------------------------------------------------------------------------
# P-02: the money lock, the shopping area, ceilings
# ---------------------------------------------------------------------------


def test_money_area_never_leaves_propose():
    """P-02: money is locked to Ask first, however much evidence accrues."""
    dial = AutonomyDial()
    _climb(dial, "money", 24)
    assert dial.score("money") == pytest.approx(0.59, abs=1e-9)
    assert dial.stage("money") == STAGE_PROPOSE
    assert dial.advise("money").run is False
    # The outcomes are still recorded, even though the stage never moves.
    assert dial.state("money")["correct"] == 24
    _climb(dial, "money", 100)
    assert dial.score("money") == pytest.approx(0.59, abs=1e-9)
    assert dial.stage("money") == STAGE_PROPOSE


def test_money_ceiling_cannot_be_raised():
    """P-02: no ceiling change can promote money above propose."""
    dial = AutonomyDial()
    with pytest.raises(ValueError, match="locked to ask first"):
        dial.set_ceiling("money", STAGE_DO_AND_SHOW)
    with pytest.raises(ValueError, match="locked to ask first"):
        dial.set_ceiling("money", STAGE_DO_SILENTLY)
    assert dial.set_ceiling("money", STAGE_PROPOSE).stage == STAGE_PROPOSE
    assert dial.ceiling("money") == STAGE_PROPOSE


def test_shopping_area_climbs_and_drops_like_other_areas():
    """P-02: shopping is a real dial area with the same rise/fall rules."""
    dial = AutonomyDial()
    assert dial.stage("shopping") == STAGE_PROPOSE
    _climb(dial, "shopping", 5)
    assert dial.stage("shopping") == STAGE_DO_AND_SHOW
    _climb(dial, "shopping", 19)
    assert dial.stage("shopping") == STAGE_DO_SILENTLY
    # Shopping is irreversible: a serious failure drops it to zero at once.
    dial.record_serious_failure("shopping", note="ordered the wrong item")
    assert dial.score("shopping") == 0.0
    assert dial.stage("shopping") == STAGE_PROPOSE


def test_shopping_tools_are_not_mapped_to_shopping():
    """P-02: purchases stay money-gated; no tool consults shopping."""
    assert area_for_tool("pay_bill") == "money"
    assert area_for_tool("search_prices") is None


def test_ceiling_holds_an_area_down_below_its_score_stage():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 24)
    assert dial.stage("scheduling") == STAGE_DO_SILENTLY
    decision = dial.set_ceiling("scheduling", STAGE_DO_AND_SHOW)
    assert decision.stage == STAGE_DO_AND_SHOW
    assert decision.run is True
    assert decision.show is True
    assert decision.ceiling == STAGE_DO_AND_SHOW
    assert dial.effective_stage("scheduling") == STAGE_DO_AND_SHOW
    # The score underneath did not move: the ceiling only holds it down.
    assert dial.score("scheduling") == pytest.approx(0.8540, abs=1e-3)
    assert dial.stage("scheduling") == STAGE_DO_SILENTLY


def test_ceiling_above_the_score_stage_changes_nothing_yet():
    dial = AutonomyDial()
    decision = dial.set_ceiling("scheduling", STAGE_DO_SILENTLY)
    assert decision.stage == STAGE_PROPOSE
    assert decision.run is False
    assert decision.ceiling is None
    _climb(dial, "scheduling", 24)
    assert dial.advise("scheduling").stage == STAGE_DO_SILENTLY


def test_clearing_a_ceiling_restores_the_score_stage():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 5)
    dial.set_ceiling("scheduling", STAGE_PROPOSE)
    assert dial.advise("scheduling").stage == STAGE_PROPOSE
    decision = dial.clear_ceiling("scheduling")
    assert decision.stage == STAGE_DO_AND_SHOW
    assert dial.ceiling("scheduling") is None


def test_unknown_ceiling_and_unknown_area_raise():
    dial = AutonomyDial()
    with pytest.raises(ValueError, match="unknown dial stage"):
        dial.set_ceiling("scheduling", "turbo")
    with pytest.raises(ValueError, match="unknown dial area"):
        dial.set_ceiling("teleportation", STAGE_PROPOSE)
    with pytest.raises(ValueError, match="unknown dial area"):
        dial.ceiling("teleportation")
    with pytest.raises(ValueError, match="unknown dial area"):
        dial.clear_ceiling("teleportation")


def test_state_and_summary_carry_the_ceiling_and_effective_stage():
    dial = AutonomyDial()
    _climb(dial, "messaging", 24)
    dial.set_ceiling("messaging", STAGE_DO_AND_SHOW)
    state = dial.state("messaging")
    assert state["stage"] == STAGE_DO_SILENTLY
    assert state["effective_stage"] == STAGE_DO_AND_SHOW
    assert state["ceiling"] == STAGE_DO_AND_SHOW
    assert dial.summary()["areas"]["messaging"]["effective_stage"] == STAGE_DO_AND_SHOW


def test_reset_area_keeps_the_ceiling():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 24)
    dial.set_ceiling("scheduling", STAGE_DO_AND_SHOW)
    dial.reset_area("scheduling")
    assert dial.score("scheduling") == pytest.approx(INITIAL_SCORE)
    assert dial.ceiling("scheduling") == STAGE_DO_AND_SHOW
    assert dial.advise("scheduling").stage == STAGE_PROPOSE


# ---------------------------------------------------------------------------
# Pure dial: accuracy, history, reset, summary
# ---------------------------------------------------------------------------


def test_accuracy_tracks_scored_outcomes():
    dial = AutonomyDial()
    assert dial.accuracy("files") is None
    dial.record_outcome("files", True)
    dial.record_outcome("files", True)
    dial.record_outcome("files", True)
    dial.record_outcome("files", False)
    assert dial.accuracy("files") == pytest.approx(0.75)
    assert dial.state("files")["correct"] == 3
    assert dial.state("files")["wrong"] == 1


def test_history_audits_moves_and_is_capped():
    dial = AutonomyDial()
    _climb(dial, "files", 5)
    dial.record_outcome("files", False)
    history = dial.history("files")
    assert len(history) == 6
    assert history[0].event == "correct"
    assert history[-1].event == "wrong"
    assert history[-1].stage_after == STAGE_PROPOSE
    for _ in range(MAX_HISTORY_PER_AREA + 20):
        dial.record_outcome("files", True)
    assert len(dial.history("files")) == MAX_HISTORY_PER_AREA


def test_serious_failure_is_audited_in_history():
    dial = AutonomyDial()
    _climb(dial, "messaging", 24)
    dial.record_serious_failure("messaging", note="wrong recipient")
    history = dial.history("messaging")
    assert history[-1].event == "serious_failure"
    assert history[-1].score_before == pytest.approx(0.8540, abs=1e-3)
    assert history[-1].score_after == 0.0
    assert history[-1].note == "wrong recipient"


def test_reset_area_returns_to_the_start():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 24)
    assert dial.stage("scheduling") == STAGE_DO_SILENTLY
    decision = dial.reset_area("scheduling")
    assert dial.score("scheduling") == pytest.approx(INITIAL_SCORE)
    assert dial.stage("scheduling") == STAGE_PROPOSE
    assert decision.stage == STAGE_PROPOSE
    assert dial.history("scheduling") == []
    assert dial.accuracy("scheduling") is None


def test_summary_lists_every_area():
    dial = AutonomyDial()
    _climb(dial, "messaging", 5)
    summary = dial.summary()
    assert set(summary["areas"]) == set(AREAS)
    assert summary["areas"]["messaging"]["stage"] == STAGE_DO_AND_SHOW
    assert summary["areas"]["messaging"]["score"] == pytest.approx(0.6131, abs=1e-3)
    assert summary["areas"]["money"]["stage"] == STAGE_PROPOSE


# ---------------------------------------------------------------------------
# Tool-to-area mapping
# ---------------------------------------------------------------------------


def test_area_for_tool_maps_the_g4_taxonomy_kinds():
    assert area_for_tool("gmail.send") == "messaging"
    assert area_for_tool("gmail.kirim") == "messaging"
    assert area_for_tool("share_file") == "messaging"
    assert area_for_tool("post_update") == "messaging"
    assert area_for_tool("pay_bill") == "money"
    assert area_for_tool("transfer_money") == "money"
    assert area_for_tool("delete_file") == "files"
    assert area_for_tool("remove_note") == "files"
    assert area_for_tool("change_password") == "accounts"
    assert area_for_tool("install_skill") == "system"


def test_area_for_tool_maps_scheduling_tools():
    assert area_for_tool("calendar.create_event") == "scheduling"
    assert area_for_tool("schedule_reminder") == "scheduling"
    assert area_for_tool("remind_me") == "scheduling"


def test_area_for_tool_returns_none_for_unknown_tools():
    assert area_for_tool("search_prices") is None
    assert area_for_tool("do_it") is None
    assert area_for_tool("") is None


def test_area_for_tool_overrides_win_and_bad_overrides_raise():
    assert area_for_tool("search_prices", overrides={"search_prices": "money"}) == "money"
    assert area_for_tool("gmail.send", overrides={"gmail.send": "files"}) == "files"
    with pytest.raises(ValueError, match="unknown dial area"):
        area_for_tool("gmail.send", overrides={"gmail.send": "teleportation"})


# ---------------------------------------------------------------------------
# Dial ask payload
# ---------------------------------------------------------------------------


def test_dial_ask_payload_names_the_action_and_says_it_did_not_run():
    dial = AutonomyDial()
    decision = dial.advise("money")
    payload = json.loads(dial_ask_payload(decision, tool_name="pay_bill"))
    assert payload["autonomy_decision"] == "propose"
    assert payload["tool"] == "pay_bill"
    assert payload["area"] == "money"
    assert payload["score"] == pytest.approx(0.50)
    assert payload["tool_was_run"] is False
    assert "Shall I go ahead" in payload["question"]
    assert "pay_bill" in payload["question"]


def test_dial_ask_payload_rejects_a_non_propose_decision():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 5)
    decision = dial.advise("scheduling")
    assert decision.stage == STAGE_DO_AND_SHOW
    with pytest.raises(ValueError, match="only for a 'propose' decision"):
        dial_ask_payload(decision, tool_name="calendar.create_event")


# ---------------------------------------------------------------------------
# Turn-loop wiring: real runs with a fake model
# ---------------------------------------------------------------------------


def _tool_reply(name, arguments=None):
    return ModelReply(
        text="",
        tool_calls=[{"id": "call-1", "name": name, "arguments": arguments or {}}],
        input_tokens=10,
        output_tokens=5,
    )


def _text_reply(text):
    return ModelReply(text=text, input_tokens=10, output_tokens=5)


def _run_loop(dial, tool_name, *, registry=None, advisor=None, message="do it"):
    """Run one turn where the model calls ``tool_name`` once, then answers."""
    calls = {"count": 0, "registry": []}

    def model_call(entry, wire_messages, tools):
        calls["count"] += 1
        if calls["count"] == 1:
            return _tool_reply(tool_name)
        return _text_reply("done")

    def run_registry(name, arguments):
        calls["registry"].append((name, arguments))
        if registry is not None:
            return registry(name, arguments)
        return {"ok": True}

    loop = TurnLoop(
        model_call=model_call,
        tool_call=run_registry,
        config=TurnConfig(max_iterations=10),
        intent_advisor=advisor,
        autonomy_dial=dial,
    )
    result = loop.run_turn([InternalMessage.user(message)])
    return result, calls, loop


def _tool_results(result):
    return [
        block.get("content", "")
        for message in result.messages
        for block in message.blocks
        if block.get("type") == "tool_result"
    ]


def test_loop_blocks_the_tool_at_propose_and_does_not_run_it():
    dial = AutonomyDial()
    result, calls, loop = _run_loop(dial, "gmail.send")
    assert result.status == "completed"
    assert calls["registry"] == [], "a propose-stage tool must not run"
    assert len(result.dial_decisions) == 1
    decision = result.dial_decisions[0]
    assert decision.area == "messaging"
    assert decision.stage == STAGE_PROPOSE
    assert decision.run is False
    payload = json.loads(_tool_results(result)[0])
    assert payload["autonomy_decision"] == "propose"
    assert payload["tool_was_run"] is False
    assert payload["area"] == "messaging"
    events = [e for e in loop.event_memory() if e["type"] == "dial_ask"]
    assert len(events) == 1
    assert events[0]["content"]["tool"] == "gmail.send"


def test_loop_runs_the_tool_at_do_and_show_and_feeds_the_success_back():
    dial = AutonomyDial()
    _climb(dial, "messaging", 5)
    assert dial.stage("messaging") == STAGE_DO_AND_SHOW
    before = dial.score("messaging")
    result, calls, loop = _run_loop(dial, "gmail.send")
    assert result.status == "completed"
    assert calls["registry"] == [("gmail.send", {})]
    decision = result.dial_decisions[0]
    assert decision.stage == STAGE_DO_AND_SHOW
    assert decision.show is True
    # The successful run fed back as a correct outcome: the score rose.
    assert dial.score("messaging") == pytest.approx(before + (1 - before) * 0.05)
    assert dial.state("messaging")["correct"] == 6
    events = [e for e in loop.event_memory() if e["type"] == "dial_show"]
    assert len(events) == 1


def test_loop_runs_the_tool_silently_at_do_silently():
    dial = AutonomyDial()
    _climb(dial, "scheduling", 24)
    assert dial.stage("scheduling") == STAGE_DO_SILENTLY
    result, calls, loop = _run_loop(dial, "calendar.create_event")
    assert result.status == "completed"
    assert calls["registry"] == [("calendar.create_event", {})]
    decision = result.dial_decisions[0]
    assert decision.stage == STAGE_DO_SILENTLY
    assert decision.run is True
    assert decision.show is False
    events = [e for e in loop.event_memory() if e["type"] == "dial_silent"]
    assert len(events) == 1
    assert events[0]["content"]["stage"] == STAGE_DO_SILENTLY


def test_loop_feeds_a_tool_error_back_as_a_wrong_outcome():
    dial = AutonomyDial()
    _climb(dial, "messaging", 5)
    assert dial.stage("messaging") == STAGE_DO_AND_SHOW

    def failing_registry(name, arguments):
        raise RuntimeError("the send failed")

    result, calls, _loop = _run_loop(dial, "gmail.send", registry=failing_registry)
    assert result.status == "completed"
    assert calls["registry"] == [("gmail.send", {})]
    # One wrong outcome: 0.6131 - 0.15 = 0.4631, back to propose.
    assert dial.score("messaging") == pytest.approx(0.4631, abs=1e-3)
    assert dial.stage("messaging") == STAGE_PROPOSE
    assert dial.state("messaging")["wrong"] == 1


def test_loop_blocks_the_next_call_right_after_a_serious_failure():
    dial = AutonomyDial()
    _climb(dial, "messaging", 24)
    assert dial.stage("messaging") == STAGE_DO_SILENTLY
    # A serious failure drops the area at once; the very next turn
    # must ask first instead of sending silently.
    dial.record_serious_failure("messaging", note="message went to the wrong person")
    assert dial.stage("messaging") == STAGE_PROPOSE
    result, calls, _loop = _run_loop(dial, "gmail.send")
    assert result.status == "completed"
    assert calls["registry"] == [], "the dropped dial must block the send"
    assert result.dial_decisions[0].stage == STAGE_PROPOSE
    payload = json.loads(_tool_results(result)[0])
    assert payload["tool_was_run"] is False


def test_loop_without_a_dial_behaves_exactly_as_before():
    result, calls, _loop = _run_loop(None, "gmail.send")
    assert result.status == "completed"
    assert calls["registry"] == [("gmail.send", {})]
    assert result.dial_decisions == []


def test_loop_never_consults_the_dial_for_unmapped_tools():
    dial = AutonomyDial()
    result, calls, _loop = _run_loop(dial, "search_prices")
    assert result.status == "completed"
    assert calls["registry"] == [("search_prices", {})]
    assert result.dial_decisions == []


def test_loop_fails_open_when_the_dial_raises():
    class BrokenDial:
        def advise(self, area):
            raise RuntimeError("dial is broken")

    calls = {"registry": []}

    def model_call(entry, wire_messages, tools):
        return _tool_reply("gmail.send") if not calls["registry"] else _text_reply("done")

    loop = TurnLoop(
        model_call=model_call,
        tool_call=lambda name, arguments: calls["registry"].append(name) or {"ok": True},
        config=TurnConfig(max_iterations=10),
        autonomy_dial=BrokenDial(),
    )
    result = loop.run_turn([InternalMessage.user("send it")])
    assert result.status == "completed"
    assert calls["registry"] == ["gmail.send"]
    assert result.dial_decisions == []


def test_loop_never_consults_the_dial_for_intercepted_tools():
    dial = AutonomyDial()

    def model_call(entry, wire_messages, tools):
        return _tool_reply("todo", {"action": "list"})

    loop = TurnLoop(
        model_call=model_call,
        config=TurnConfig(max_iterations=3),
        autonomy_dial=dial,
    )
    result = loop.run_turn([InternalMessage.user("list my todos")])
    # The intercepted todo ran (the loop keeps looping on tool
    # results until the budget ends); the dial was never asked,
    # so no dial decision was recorded.
    assert result.dial_decisions == []


def test_intent_ask_wins_over_the_dial_and_the_dial_is_not_consulted():
    advisor = IntentTurnAdvisor(
        profile={"preference": "buy the laptop", "preference_2": "buy a laptop"},
        action_specs={"gmail.send": ActionSpec(reversible=False, stakes="high")},
    )
    dial = AutonomyDial()
    result, calls, _loop = _run_loop(dial, "gmail.send", advisor=advisor, message="buy the laptop")
    assert result.status == "completed"
    assert calls["registry"] == [], "an intent ask must not run the tool"
    assert len(result.intent_decisions) == 1
    assert result.intent_decisions[0].outcome == "ask"
    # The intent ask already stopped the call, so the dial was
    # never consulted for it.
    assert result.dial_decisions == []


def test_loop_honours_a_caller_area_override():
    dial = AutonomyDial()
    _climb(dial, "files", 5)
    assert dial.stage("files") == STAGE_DO_AND_SHOW
    calls = {"count": 0, "registry": []}

    def model_call(entry, wire_messages, tools):
        calls["count"] += 1
        if calls["count"] == 1:
            return _tool_reply("search_prices")
        return _text_reply("done")

    loop = TurnLoop(
        model_call=model_call,
        tool_call=lambda name, arguments: calls["registry"].append(name) or {"ok": True},
        config=TurnConfig(max_iterations=10),
        autonomy_dial=dial,
        dial_area_for_tool={"search_prices": "files"},
    )
    result = loop.run_turn([InternalMessage.user("find it")])
    assert result.status == "completed"
    assert calls["registry"] == ["search_prices"]
    assert result.dial_decisions[0].area == "files"
    assert result.dial_decisions[0].stage == STAGE_DO_AND_SHOW
