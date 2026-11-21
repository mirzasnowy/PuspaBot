"""Tests for desktop hands core (V2-31): tree, fake backend, the
act + re-read loop, and the permission gate. No database, no
network, no real desktop: every test runs on the fake backend.
"""

import pytest

from puspa.computer.backend import FakeBackend, demo_tree
from puspa.computer.hands import DesktopHands, UnknownRefError
from puspa.computer.tree import Snapshot, node_to_dict, skeletonize, snapshot_to_dict
from puspa.localexec import PermissionDecision, PermissionDenied
from puspa.policy.gate import Gate

pytestmark = pytest.mark.unit

APPROVE_ONCE = PermissionDecision.APPROVE_ONCE
APPROVE_SESSION = PermissionDecision.APPROVE_SESSION
DENY = PermissionDecision.DENY


class FakePrompt:
    """A prompt that answers from a queue and records every ask."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.asked = []

    def ask(self, command, cwd):
        self.asked.append(command)
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
                                {"role": "push button", "name": "Save", "actions": ["click"]},
                                {
                                    "role": "check box",
                                    "name": "Pin",
                                    "actions": ["click"],
                                    "after_click": {"states_add": ["checked"]},
                                },
                                {
                                    "role": "push button",
                                    "name": "Close",
                                    "actions": ["click"],
                                    "after_click": {"removes_path": [0, 0, 0, 2]},
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ]


def _hands(spec=None, decisions=(APPROVE_ONCE,), **kwargs):
    backend = FakeBackend(spec if spec is not None else _tree())
    return DesktopHands(backend, prompt=FakePrompt(list(decisions)), **kwargs)


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


# -- tree -----------------------------------------------------------


def test_refs_are_stable_within_one_snapshot():
    hands = _hands()
    shot = hands.snapshot(skeleton=False)
    refs = [node.ref for node in shot.nodes[0].children[0].children[0].children]
    assert refs == ["n4", "n5", "n6"]
    again = hands.snapshot(skeleton=False)
    assert [node.ref for node in again.nodes[0].children[0].children[0].children] == refs


def test_skeleton_collapses_deep_subtrees_but_keeps_counts():
    hands = _hands()
    shot = hands.snapshot(skeleton=True)
    toolbar = shot.nodes[0].children[0].children[0]
    assert toolbar.collapsed is True
    assert toolbar.child_count == 3
    assert toolbar.children == ()
    full = hands.snapshot(skeleton=False)
    assert len(full.nodes[0].children[0].children[0].children) == 3


def test_skeletonize_keeps_total_nodes():
    hands = _hands()
    full = hands.snapshot(skeleton=False)
    assert skeletonize(full).total_nodes == full.total_nodes == 6


def test_snapshot_to_dict_is_json_shaped():
    hands = _hands()
    data = snapshot_to_dict(hands.snapshot(skeleton=False))
    assert data["backend"] == "fake"
    assert data["total_nodes"] == 6
    button = data["nodes"][0]["children"][0]["children"][0]["children"][0]
    assert button["role"] == "push button" and button["name"] == "Save"
    assert node_to_dict(hands.snapshot(skeleton=False).nodes[0])["child_count"] == 1


def test_drill_returns_the_expanded_node():
    hands = _hands()
    ref = hands.snapshot(skeleton=True).nodes[0].children[0].children[0].ref
    node = hands.drill(ref)
    assert node.role == "group"
    assert len(node.children) == 3


def test_drill_unknown_ref_raises():
    with pytest.raises(UnknownRefError):
        _hands().drill("n99")


# -- fake backend ---------------------------------------------------


def test_fake_click_without_action_returns_false():
    backend = FakeBackend(_tree())
    hands = DesktopHands(backend, prompt=FakePrompt([APPROVE_ONCE]))
    ref = _ref_of(hands, "Note")
    result = hands.click(ref)
    assert result.performed is False
    assert result.verified is False
    assert "no click action" in result.reason


# -- click + verify -------------------------------------------------


def test_click_with_expect_verified_by_reread():
    hands = _hands()
    ref = _ref_of(hands, "Pin")
    result = hands.click(ref, expect={"state": "checked"})
    assert result.performed is True
    assert result.verified is True
    assert "checked" in result.after


def test_click_without_expect_verifies_on_visible_change():
    hands = _hands()
    ref = _ref_of(hands, "Pin")
    result = hands.click(ref)
    assert result.performed is True
    assert result.verified is True
    assert "changed" in result.reason


def test_click_reports_unverified_when_screen_unchanged():
    hands = _hands()
    ref = _ref_of(hands, "Save")
    result = hands.click(ref)
    assert result.performed is True
    assert result.verified is False
    assert "unchanged" in result.reason


def test_click_with_unmet_expect_is_not_verified():
    hands = _hands()
    ref = _ref_of(hands, "Pin")
    result = hands.click(ref, expect={"state": "selected"})
    assert result.performed is True
    assert result.verified is False
    assert "selected" in result.reason


def test_click_unknown_ref_raises_before_any_prompt():
    prompt = FakePrompt([APPROVE_ONCE])
    hands = DesktopHands(FakeBackend(_tree()), prompt=prompt)
    with pytest.raises(UnknownRefError):
        hands.click("n99")
    assert prompt.asked == []


def test_click_that_removes_itself_verifies_absent():
    hands = _hands()
    ref = _ref_of(hands, "Close")
    result = hands.click(ref, expect={"absent": True})
    assert result.performed is True
    assert result.verified is True
    assert "gone" in result.reason


def test_verify_reads_without_clicking():
    hands = _hands()
    ref = _ref_of(hands, "Pin")
    check = hands.verify(ref, expect={"state": "checked"})
    assert check.found is True
    assert check.verified is False
    assert "checked" in check.reason


def test_verify_absent_for_a_missing_ref():
    hands = _hands()
    check = hands.verify("n99", expect={"absent": True})
    assert check.found is False
    assert check.verified is True
    check2 = hands.verify("n99")
    assert check2.verified is False


# -- permission gate ------------------------------------------------


def test_click_denied_by_prompt_touches_nothing():
    spec = _tree()
    prompt = FakePrompt([DENY])
    hands = DesktopHands(FakeBackend(spec), prompt=prompt)
    ref = _ref_of(hands, "Pin")
    with pytest.raises(PermissionDenied):
        hands.click(ref)
    assert len(prompt.asked) == 1
    assert spec[0]["children"][0]["children"][0]["children"][1].get("states", []) == []


def test_session_approval_prompts_only_once():
    prompt = FakePrompt([APPROVE_SESSION])
    hands = DesktopHands(FakeBackend(_tree()), prompt=prompt)
    hands.click(_ref_of(hands, "Pin"))
    hands.click(_ref_of(hands, "Save"))
    assert len(prompt.asked) == 1


def test_token_without_computer_permission_refuses():
    gate = Gate()
    payload = {"tool_permissions": {"computer.click": "deny"}}
    hands = _hands(decisions=(APPROVE_ONCE,), gate=gate, token_payload=payload)
    with pytest.raises(PermissionDenied):
        hands.click(_ref_of(hands, "Pin"))


def test_token_with_computer_permission_allows_after_prompt():
    gate = Gate()
    payload = {"tool_permissions": {"computer.click": "allow"}}
    prompt = FakePrompt([APPROVE_ONCE])
    hands = DesktopHands(FakeBackend(_tree()), prompt=prompt, gate=gate, token_payload=payload)
    result = hands.click(_ref_of(hands, "Pin"))
    assert result.verified is True
    assert len(prompt.asked) == 1


def test_token_that_never_mentions_computer_denies():
    gate = Gate()
    payload = {"tool_permissions": {"gmail.send": "allow"}}
    hands = _hands(decisions=(APPROVE_ONCE,), gate=gate, token_payload=payload)
    with pytest.raises(PermissionDenied):
        hands.click(_ref_of(hands, "Pin"))


def test_demo_tree_snapshot_has_two_apps():
    shot = FakeBackend(demo_tree()).snapshot()
    assert len(shot.nodes) == 2
    assert isinstance(shot, Snapshot)
