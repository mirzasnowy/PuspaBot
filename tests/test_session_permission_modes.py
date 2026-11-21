"""Tests for COW-13: per-session permission modes (read-only,
ask-first, full-access) laid over the existing gates. No database,
no network, no real desktop: the mode state is pure Python and
the hands run on the fake backend with an in-memory audit sink.
"""

import pytest

from puspa.computer.allowlist import Allowlist
from puspa.computer.audit import InMemoryComputerAudit
from puspa.computer.backend import FakeBackend
from puspa.computer.hands import DesktopHands
from puspa.computer.stop import ComputerStop
from puspa.computer.tiers import Tier
from puspa.localexec import PermissionDecision, PermissionDenied
from puspa.policy.session_modes import (
    DEFAULT_MODE,
    SessionMode,
    SessionModeDenied,
    SessionModeState,
)

pytestmark = pytest.mark.unit

APPROVE_ONCE = PermissionDecision.APPROVE_ONCE


class FakePrompt:
    """A prompt that answers from a queue and records every ask."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.asked: list = []

    def ask(self, command, cwd):
        self.asked.append(command)
        if not self.decisions:
            raise AssertionError(f"prompt asked with no decision queued: {command!r}")
        return self.decisions.pop(0)


def _tree():
    return [
        {
            "role": "application",
            "name": "Notes",
            "children": [
                {
                    "role": "frame",
                    "name": "Note",
                    "children": [
                        {
                            "role": "group",
                            "name": "Toolbar",
                            "children": [
                                {
                                    "role": "check box",
                                    "name": "Pin",
                                    "actions": ["click"],
                                    "after_click": {"states_add": ["checked"]},
                                },
                            ],
                        },
                    ],
                }
            ],
        },
        {
            "role": "application",
            "name": "Mail",
            "children": [
                {
                    "role": "frame",
                    "name": "Inbox",
                    "children": [
                        {
                            "role": "push button",
                            "name": "Reply",
                            "actions": ["click"],
                            "after_click": {"states_add": ["checked"]},
                        }
                    ],
                }
            ],
        },
    ]


def _walk_nodes(nodes):
    for node in nodes:
        yield node
        yield from _walk_nodes(node.children)


def _ref_of(hands, name):
    shot = hands.snapshot(skeleton=False)
    for node in _walk_nodes(shot.nodes):
        if node.name == name:
            return node.ref
    raise AssertionError(f"no node named {name!r}")


def _hands(tmp_path, modes, decisions=()):
    backend = FakeBackend(_tree())
    audit = InMemoryComputerAudit()
    prompt = FakePrompt(list(decisions))
    hands = DesktopHands(
        backend,
        prompt=prompt,
        allowlist=Allowlist(tmp_path / "allowlist.json"),
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=audit,
        session_modes=modes,
    )
    return hands, audit, prompt


# -- mode state ---------------------------------------------------------


def test_new_session_defaults_to_ask_first():
    state = SessionModeState()
    assert state.mode is SessionMode.ASK_FIRST
    assert state.mode == "ask_first"
    assert DEFAULT_MODE == "ask_first"
    assert state.to_dict() == {"mode": "ask_first", "history": []}


def test_set_mode_switches_and_records_history():
    state = SessionModeState()
    previous = state.set_mode("read_only")
    assert previous is SessionMode.ASK_FIRST
    assert state.mode is SessionMode.READ_ONLY
    previous = state.set_mode(SessionMode.FULL_ACCESS)
    assert previous is SessionMode.READ_ONLY
    assert state.mode is SessionMode.FULL_ACCESS
    assert [switch.to_dict() for switch in state.history()] == [
        {"mode_before": "ask_first", "mode_after": "read_only"},
        {"mode_before": "read_only", "mode_after": "full_access"},
    ]
    assert state.to_dict()["history"][-1]["mode_after"] == "full_access"


def test_set_mode_to_same_mode_is_a_noop():
    state = SessionModeState()
    assert state.set_mode("ask_first") is SessionMode.ASK_FIRST
    assert state.history() == []
    state.set_mode("read_only")
    assert state.set_mode(SessionMode.READ_ONLY) is SessionMode.READ_ONLY
    assert len(state.history()) == 1


def test_set_mode_rejects_unknown_mode():
    state = SessionModeState()
    with pytest.raises(ValueError, match="unknown session permission mode"):
        state.set_mode("yolo")
    assert state.mode is SessionMode.ASK_FIRST


def test_decide_rejects_unknown_tier_and_gate_kind():
    state = SessionModeState()
    with pytest.raises(ValueError, match="not a valid Tier"):
        state.decide("click", tier="explode")
    with pytest.raises(ValueError, match="unknown gate kind"):
        state.decide("send", gate_kind="explode")


# -- decision table -----------------------------------------------------


def test_decide_read_is_free_in_every_mode():
    for mode in SessionMode:
        state = SessionModeState(mode)
        decision = state.decide("snapshot", tier=Tier.READ)
        assert decision.verdict == "allow"
        assert decision.destructive is False
        assert decision.mode == mode.value


@pytest.mark.parametrize(
    ("mode", "tier", "verdict"),
    [
        (SessionMode.READ_ONLY, Tier.NORMAL, "deny"),
        (SessionMode.READ_ONLY, Tier.DESTRUCTIVE, "deny"),
        (SessionMode.ASK_FIRST, Tier.NORMAL, "ask"),
        (SessionMode.ASK_FIRST, Tier.DESTRUCTIVE, "ask"),
        (SessionMode.FULL_ACCESS, Tier.NORMAL, "allow"),
        (SessionMode.FULL_ACCESS, Tier.DESTRUCTIVE, "ask"),
    ],
)
def test_decide_write_by_tier_across_modes(mode, tier, verdict):
    decision = SessionModeState(mode).decide("click", tier=tier)
    assert decision.verdict == verdict
    assert decision.destructive is (tier is Tier.DESTRUCTIVE)
    assert decision.to_dict()["verdict"] == verdict


@pytest.mark.parametrize(
    ("gate_kind", "destructive"),
    [
        ("send", False),
        ("create_event", False),
        ("prepare_payment", True),
        ("delete", True),
        ("deploy", True),
    ],
)
def test_decide_write_by_gate_kind(gate_kind, destructive):
    assert (
        SessionModeState(SessionMode.READ_ONLY).decide("x", gate_kind=gate_kind).verdict == "deny"
    )
    assert SessionModeState(SessionMode.ASK_FIRST).decide("x", gate_kind=gate_kind).verdict == "ask"
    decision = SessionModeState(SessionMode.FULL_ACCESS).decide("x", gate_kind=gate_kind)
    assert decision.verdict == ("ask" if destructive else "allow")
    assert decision.destructive is destructive


def test_decide_tool_name_fallback():
    # The G4 taxonomy recognises an outbound tool name as a write.
    assert SessionModeState(SessionMode.ASK_FIRST).decide("gmail.send").verdict == "ask"
    # A read verb is a read.
    assert SessionModeState(SessionMode.READ_ONLY).decide("search").verdict == "allow"
    assert SessionModeState(SessionMode.READ_ONLY).decide("read_code").verdict == "allow"
    # An unrecognised tool is a write, fail-closed to asking.
    assert SessionModeState(SessionMode.ASK_FIRST).decide("do_it").verdict == "ask"
    assert SessionModeState(SessionMode.READ_ONLY).decide("do_it").verdict == "deny"
    assert SessionModeState(SessionMode.FULL_ACCESS).decide("do_it").verdict == "ask"


def test_check_raises_on_deny_and_returns_decision_otherwise():
    state = SessionModeState(SessionMode.READ_ONLY)
    with pytest.raises(SessionModeDenied, match="read-only mode"):
        state.check("click", tier=Tier.NORMAL)
    state.set_mode(SessionMode.ASK_FIRST)
    decision = state.check("click", tier=Tier.NORMAL)
    assert decision.verdict == "ask"
    state.set_mode(SessionMode.FULL_ACCESS)
    assert state.check("click", tier=Tier.NORMAL).verdict == "allow"
    assert state.check("snapshot", tier=Tier.READ).verdict == "allow"


# -- one session, three modes, no human --------------------------------


def test_one_session_switches_modes_mid_session(tmp_path):
    """Done-criterion: one session in read-only (write denied),
    switched to ask-first (write asks), switched to full-access
    (ordinary write runs, destructive still asks) — all without
    a human, through the real desktop-hands gate."""
    modes = SessionModeState()  # ask-first by default
    hands, audit, prompt = _hands(
        tmp_path, modes, decisions=(APPROVE_ONCE, APPROVE_ONCE, APPROVE_ONCE)
    )

    # Read-only: reads run, a write is refused before any prompt.
    modes.set_mode(SessionMode.READ_ONLY)
    hands.snapshot()
    with pytest.raises(PermissionDenied, match="read-only mode"):
        hands.click(_ref_of(hands, "Pin"))
    assert prompt.asked == []
    denied = [e for e in audit.entries if e.get("decision") == "deny"]
    assert len(denied) == 1
    assert denied[0]["computer_action"] == "click"
    assert "read-only" in denied[0]["reason"]

    # Ask-first: the same write asks, and runs once approved.
    modes.set_mode(SessionMode.ASK_FIRST)
    result = hands.click(_ref_of(hands, "Pin"))
    assert result.performed is True
    assert len(prompt.asked) == 1

    # Ask-first still asks inside an allowlisted app: the mode
    # beats the V2-35 allowlist shortcut.
    hands.allowlist.allow("Notes")
    result = hands.click(_ref_of(hands, "Pin"))
    assert result.performed is True
    assert len(prompt.asked) == 2

    # Full-access: ordinary writes run with no prompt, even
    # outside an allowlisted app.
    modes.set_mode(SessionMode.FULL_ACCESS)
    result = hands.click(_ref_of(hands, "Pin"))
    assert result.performed is True
    result = hands.click(_ref_of(hands, "Reply"))
    assert result.performed is True
    assert len(prompt.asked) == 2

    # Full-access still asks for a destructive write: that is
    # the guardrail, not a bug.
    result = hands.clipboard_write("secret")
    assert result.performed is True
    assert len(prompt.asked) == 3

    assert [s.mode_after for s in modes.history()] == [
        "read_only",
        "ask_first",
        "full_access",
    ]


def test_hands_without_session_modes_keeps_old_behaviour(tmp_path):
    """No mode wired in: the V2-35 tier flow decides alone, exactly
    as before — a click outside an allowlisted app asks, a click
    inside one does not."""
    backend = FakeBackend(_tree())
    prompt = FakePrompt([APPROVE_ONCE])
    hands = DesktopHands(
        backend,
        prompt=prompt,
        allowlist=Allowlist(tmp_path / "allowlist.json"),
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=InMemoryComputerAudit(),
    )
    assert hands.session_modes is None
    assert hands.click(_ref_of(hands, "Reply")).performed is True
    assert len(prompt.asked) == 1
    hands.allowlist.allow("Notes")
    assert hands.click(_ref_of(hands, "Pin")).performed is True
    assert len(prompt.asked) == 1
