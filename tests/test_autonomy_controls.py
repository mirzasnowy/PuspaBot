"""Tests for the P-02 control layer (``puspa.autonomy.controls``).

Pure, no database: signals are built from fake ledger events
and fake drafts, and the controls are exercised over a real
``AutonomyDial``. Covers the evidence streaks, the visible
raise suggestion, the money lock, and the ceiling rules
(lower any time, raise one step only after evidence).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from puspa.autonomy.controls import (
    SUGGESTION_THRESHOLD,
    AutonomyControls,
    CeilingRefusedError,
    signals_from_drafts,
    signals_from_events,
    streaks_from_signals,
    suggestion_for,
)
from puspa.autonomy.dial import AutonomyDial

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


def _event(event_type, *, at, kind=None, request_id=None):
    content = {}
    if kind is not None:
        content["kind"] = kind
    if request_id is not None:
        content["request_id"] = request_id
    return {"type": event_type, "created_at": at, "content": content}


def _granted_signals(area_kind, count, *, start=T0):
    """``count`` granted approval events of one Gate kind, one minute apart."""
    return [
        _event("approval_granted", at=start + timedelta(minutes=i), kind=area_kind)
        for i in range(count)
    ]


def _draft(status, *, kind="message", created_at=T0, updated_at=None, sent_at=None):
    return SimpleNamespace(
        kind=kind,
        status=status,
        created_at=created_at,
        updated_at=updated_at if updated_at is not None else created_at,
        sent_at=sent_at,
    )


# ---------------------------------------------------------------------------
# Signals from ledger events
# ---------------------------------------------------------------------------


def test_granted_approval_by_kind_feeds_its_area():
    signals = signals_from_events(_granted_signals("send", 3))
    assert streaks_from_signals(signals)["messaging"] == 3
    assert streaks_from_signals(signals)["money"] == 0


def test_granted_approval_matched_by_request_id():
    events = [
        _event("approval_requested", at=T0, kind="delete", request_id="req-1"),
        _event("approval_granted", at=T0 + timedelta(minutes=1), request_id="req-1"),
    ]
    assert streaks_from_signals(signals_from_events(events))["files"] == 1


def test_rejected_approval_resets_only_its_area():
    events = _granted_signals("send", 4) + _granted_signals("delete", 2)
    events.append(_event("approval_rejected", at=T0 + timedelta(minutes=10), kind="send"))
    streaks = streaks_from_signals(signals_from_events(events))
    assert streaks["messaging"] == 0
    assert streaks["files"] == 2


def test_unattributed_rejection_resets_every_area():
    events = _granted_signals("send", 4) + _granted_signals("delete", 2)
    events.append(_event("approval_rejected", at=T0 + timedelta(minutes=10)))
    streaks = streaks_from_signals(signals_from_events(events))
    assert streaks["messaging"] == 0
    assert streaks["files"] == 0


def test_false_report_resets_every_area():
    events = _granted_signals("send", 9)
    events.append(_event("false_report", at=T0 + timedelta(minutes=10)))
    assert streaks_from_signals(signals_from_events(events))["messaging"] == 0


def test_events_without_a_timestamp_are_ignored():
    events = [{"type": "approval_granted", "created_at": None, "content": {"kind": "send"}}]
    assert signals_from_events(events) == []


def test_deploy_approvals_feed_no_shown_area():
    events = _granted_signals("deploy", 5)
    streaks = streaks_from_signals(signals_from_events(events))
    assert all(value == 0 for value in streaks.values())


# ---------------------------------------------------------------------------
# Signals from drafts
# ---------------------------------------------------------------------------


def test_approved_and_sent_drafts_feed_messaging():
    drafts = [
        _draft("approved", updated_at=T0 + timedelta(minutes=1)),
        _draft("sent", updated_at=T0 + timedelta(minutes=2), sent_at=T0 + timedelta(minutes=3)),
    ]
    assert streaks_from_signals(signals_from_drafts(drafts))["messaging"] == 2


def test_revised_draft_resets_the_messaging_streak():
    drafts = [
        _draft("approved", updated_at=T0 + timedelta(minutes=1)),
        _draft("approved", updated_at=T0 + timedelta(minutes=2)),
        _draft("draft", created_at=T0, updated_at=T0 + timedelta(minutes=3)),
    ]
    assert streaks_from_signals(signals_from_drafts(drafts))["messaging"] == 0


def test_undecided_draft_neither_adds_nor_breaks_the_streak():
    drafts = [
        _draft("approved", updated_at=T0 + timedelta(minutes=1)),
        _draft("draft", created_at=T0 + timedelta(minutes=2)),
        _draft("approved", updated_at=T0 + timedelta(minutes=3)),
    ]
    assert streaks_from_signals(signals_from_drafts(drafts))["messaging"] == 2


def test_unknown_draft_kinds_are_ignored():
    drafts = [_draft("approved", kind="spreadsheet", updated_at=T0 + timedelta(minutes=1))]
    assert streaks_from_signals(signals_from_drafts(drafts))["messaging"] == 0


def test_signals_merge_in_time_order_across_sources():
    events = _granted_signals("send", 2, start=T0)
    drafts = [_draft("approved", updated_at=T0 + timedelta(minutes=5))]
    # The draft lands after the two grants: the streak is three.
    assert (
        streaks_from_signals(signals_from_events(events) + signals_from_drafts(drafts))["messaging"]
        == 3
    )


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def test_no_suggestion_below_the_threshold():
    assert suggestion_for("messaging", SUGGESTION_THRESHOLD - 1, "ask_first") is None


def test_suggestion_at_the_threshold_names_the_next_ceiling():
    suggestion = suggestion_for("messaging", SUGGESTION_THRESHOLD, "ask_first")
    assert suggestion is not None
    assert suggestion["to_ceiling"] == "balanced"
    assert suggestion["threshold"] == SUGGESTION_THRESHOLD
    assert suggestion["threshold_text"] == "10 accepted drafts in a row"
    assert suggestion["streak"] == SUGGESTION_THRESHOLD


def test_second_suggestion_offers_autonomous():
    suggestion = suggestion_for("messaging", SUGGESTION_THRESHOLD, "balanced")
    assert suggestion is not None
    assert suggestion["to_ceiling"] == "autonomous"


def test_no_suggestion_when_already_autonomous_or_locked():
    assert suggestion_for("messaging", 50, "autonomous") is None
    assert suggestion_for("money", 50, "ask_first") is None


def test_suggestion_for_unknown_area_raises():
    with pytest.raises(ValueError, match="unknown display area"):
        suggestion_for("teleportation", 10, "ask_first")


# ---------------------------------------------------------------------------
# Controls over a real dial
# ---------------------------------------------------------------------------


def test_controls_start_every_area_at_ask_first():
    controls = AutonomyControls(AutonomyDial())
    state = controls.state()
    assert [area["area"] for area in state["areas"]] == [
        "money",
        "messaging",
        "files",
        "scheduling",
        "shopping",
    ]
    assert all(area["ceiling"] == "ask_first" for area in state["areas"])
    assert all(area["effective_stage"] == "propose" for area in state["areas"])
    assert state["threshold"] == SUGGESTION_THRESHOLD
    assert state["threshold_text"] == "10 accepted drafts in a row"
    money = state["areas"][0]
    assert money["locked"] is True
    assert money["suggestion"] is None


def test_state_shows_the_suggestion_once_evidence_is_met():
    controls = AutonomyControls(AutonomyDial())
    signals = signals_from_events(_granted_signals("send", SUGGESTION_THRESHOLD))
    state = controls.state(signals)
    messaging = next(area for area in state["areas"] if area["area"] == "messaging")
    assert messaging["streak"] == SUGGESTION_THRESHOLD
    assert messaging["suggestion"] is not None
    assert messaging["suggestion"]["to_ceiling"] == "balanced"


def test_lowering_a_ceiling_is_always_allowed():
    controls = AutonomyControls(AutonomyDial())
    controls.dial.set_ceiling("messaging", "do_silently")
    moved = controls.set_ceiling("messaging", "ask_first")
    assert moved["ceiling"] == "ask_first"
    assert moved["effective_stage"] == "propose"


def test_raising_without_evidence_is_refused():
    controls = AutonomyControls(AutonomyDial())
    with pytest.raises(CeilingRefusedError) as excinfo:
        controls.set_ceiling("messaging", "balanced")
    assert excinfo.value.reason == "needs_evidence"
    assert controls.dial.ceiling("messaging") == "propose"


def test_raising_with_evidence_moves_one_step_at_a_time():
    controls = AutonomyControls(AutonomyDial())
    signals = signals_from_events(_granted_signals("send", SUGGESTION_THRESHOLD))
    moved = controls.set_ceiling("messaging", "balanced", signals)
    assert moved["ceiling"] == "balanced"
    # A second raise, with the same evidence, moves the next step.
    moved = controls.set_ceiling("messaging", "autonomous", signals)
    assert moved["ceiling"] == "autonomous"


def test_a_two_step_jump_in_one_call_is_refused():
    controls = AutonomyControls(AutonomyDial())
    signals = signals_from_events(_granted_signals("send", SUGGESTION_THRESHOLD))
    with pytest.raises(CeilingRefusedError) as excinfo:
        controls.set_ceiling("messaging", "autonomous", signals)
    assert excinfo.value.reason == "needs_evidence"
    assert controls.dial.ceiling("messaging") == "propose"


def test_raising_money_is_refused_even_with_evidence():
    controls = AutonomyControls(AutonomyDial())
    signals = signals_from_events(_granted_signals("prepare_payment", 50))
    with pytest.raises(CeilingRefusedError) as excinfo:
        controls.set_ceiling("money", "balanced", signals)
    assert excinfo.value.reason == "locked"
    assert controls.dial.ceiling("money") == "propose"


def test_effective_stage_follows_the_ceiling_once_evidence_catches_up():
    controls = AutonomyControls(AutonomyDial())
    signals = signals_from_events(_granted_signals("send", SUGGESTION_THRESHOLD))
    controls.set_ceiling("messaging", "balanced", signals)
    assert controls.dial.advise("messaging").stage == "propose"
    # The evidence score is still at propose: five correct outcomes
    # lift the score, and only then does the ceiling let it run.
    for _ in range(5):
        controls.dial.record_outcome("messaging", True)
    assert controls.dial.advise("messaging").stage == "do_and_show"


def test_set_ceiling_rejects_unknown_areas_and_ceilings():
    controls = AutonomyControls(AutonomyDial())
    with pytest.raises(ValueError, match="unknown display area"):
        controls.set_ceiling("teleportation", "balanced")
    with pytest.raises(ValueError, match="unknown ceiling"):
        controls.set_ceiling("messaging", "turbo")
