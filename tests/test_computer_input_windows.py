"""Tests for full input + window management (V2-33): typing,
shortcuts, scroll, drag, clipboard, windows, and multi-step
tasks. No database, no network, no real desktop: every test
runs on the fake backends sharing one in-memory tree.
"""

import pytest

from puspa.computer.backend import FakeBackend
from puspa.computer.hands import DesktopHands, UnknownRefError
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


def _spec():
    return [
        {
            "role": "application",
            "name": "Notes",
            "children": [
                {
                    "role": "frame",
                    "name": "Note",
                    "states": ["focused"],
                    "children": [
                        {
                            "role": "text",
                            "name": "Body",
                            "editable": True,
                            "value": "",
                            "states": ["focused", "enabled"],
                            "shortcuts": {"ctrl+a": {"states_add": ["selected"]}},
                        },
                        {
                            "role": "group",
                            "name": "List",
                            "scrollable": True,
                            "scroll": 0,
                            "after_scroll": {"states_add": ["scrolled"]},
                            "children": [
                                {
                                    "role": "list item",
                                    "name": "Card A",
                                    "after_drag": {"name": "Card A (filed)"},
                                },
                                {"role": "list item", "name": "Folder"},
                            ],
                        },
                    ],
                }
            ],
        },
        {
            "role": "application",
            "name": "Player",
            "children": [
                {
                    "role": "frame",
                    "name": "Player",
                    "scrollable": True,
                    "scroll": 0,
                    "children": [
                        {
                            "role": "push button",
                            "name": "Play",
                            "actions": ["click"],
                            "after_click": {"name": "Pause"},
                        },
                    ],
                }
            ],
        },
    ]


def _hands(spec=None, decisions=(APPROVE_ONCE,), **kwargs):
    backend = FakeBackend(spec if spec is not None else _spec())
    return DesktopHands(backend, prompt=FakePrompt(list(decisions)), **kwargs)


def _walk_nodes(nodes):
    for node in nodes:
        yield node
        yield from _walk_nodes(node.children)


def _ref_of(hands, name, role=None):
    shot = hands.snapshot(skeleton=False)
    for node in _walk_nodes(shot.nodes):
        if node.name == name and (role is None or node.role == role):
            return node.ref
    raise AssertionError(f"no node named {name!r}")


def _value_of(hands, name):
    shot = hands.snapshot(skeleton=False)
    for node in _walk_nodes(shot.nodes):
        if node.name == name:
            return node.value
    raise AssertionError(f"no node named {name!r}")


# -- typing ---------------------------------------------------------


def test_type_appends_text_and_verifies_by_reread():
    hands = _hands(decisions=(APPROVE_SESSION,))
    ref = _ref_of(hands, "Body")
    result = hands.type_text(ref, "hello", expect={"value_contains": "hello"})
    assert result.performed is True
    assert result.verified is True
    assert _value_of(hands, "Body") == "hello"
    again = hands.type_text(ref, " world")
    assert again.verified is True
    assert _value_of(hands, "Body") == "hello world"


def test_type_into_non_editable_reports_not_performed():
    hands = _hands()
    ref = _ref_of(hands, "Play")
    result = hands.type_text(ref, "hello")
    assert result.performed is False
    assert result.verified is False
    assert "not editable" in result.reason


def test_type_with_unmet_expect_is_not_verified():
    hands = _hands()
    ref = _ref_of(hands, "Body")
    result = hands.type_text(ref, "hello", expect={"value": "goodbye"})
    assert result.performed is True
    assert result.verified is False


def test_type_unknown_ref_raises_before_any_prompt():
    prompt = FakePrompt([APPROVE_ONCE])
    hands = DesktopHands(FakeBackend(_spec()), prompt=prompt)
    with pytest.raises(UnknownRefError):
        hands.type_text("n99", "hello")
    assert prompt.asked == []


def test_type_prompt_never_carries_the_text():
    prompt = FakePrompt([APPROVE_ONCE])
    hands = DesktopHands(FakeBackend(_spec()), prompt=prompt)
    hands.type_text(_ref_of(hands, "Body"), "s3cr3t-password")
    assert len(prompt.asked) == 1
    assert "s3cr3t-password" not in prompt.asked[0]
    assert "15 chars" in prompt.asked[0]


# -- shortcuts ------------------------------------------------------


def test_shortcut_on_a_ref_applies_its_effect():
    hands = _hands()
    ref = _ref_of(hands, "Body")
    result = hands.shortcut("ctrl+a", ref, expect={"state": "selected"})
    assert result.performed is True
    assert result.verified is True


def test_shortcut_without_ref_targets_the_focused_element():
    hands = _hands()
    result = hands.shortcut("ctrl+a", expect={"state": "selected"})
    assert result.performed is True
    assert result.verified is True


def test_shortcut_unknown_chord_reports_not_performed():
    hands = _hands()
    ref = _ref_of(hands, "Body")
    result = hands.shortcut("ctrl+z", ref)
    assert result.performed is False
    assert result.verified is False


def test_shortcut_tuple_keys_work():
    hands = _hands()
    ref = _ref_of(hands, "Body")
    result = hands.shortcut(("ctrl", "a"), ref, expect={"state": "selected"})
    assert result.verified is True


def test_shortcut_with_no_keys_raises():
    hands = _hands()
    with pytest.raises(Exception, match="at least one key"):
        hands.shortcut("")


# -- scroll ---------------------------------------------------------


def test_scroll_with_visible_effect_verifies():
    hands = _hands()
    ref = _ref_of(hands, "List")
    result = hands.scroll(ref, "down", 3, expect={"state": "scrolled"})
    assert result.performed is True
    assert result.verified is True


def test_scroll_without_visible_effect_is_not_verified():
    # The Player frame scrolls but nothing on screen changes.
    hands = _hands()
    ref = _ref_of(hands, "Player", role="frame")
    result = hands.scroll(ref, "down", 2)
    assert result.performed is True
    assert result.verified is False
    assert "unchanged" in result.reason


def test_scroll_non_scrollable_reports_not_performed():
    hands = _hands()
    ref = _ref_of(hands, "Play")
    result = hands.scroll(ref, "down", 1)
    assert result.performed is False


def test_scroll_bad_direction_raises():
    hands = _hands()
    with pytest.raises(Exception, match="unknown scroll direction"):
        hands.scroll(_ref_of(hands, "List"), "diagonal", 1)


# -- drag -----------------------------------------------------------


def test_drag_onto_target_applies_the_drop_effect():
    hands = _hands()
    result = hands.drag(
        _ref_of(hands, "Card A"), _ref_of(hands, "Folder"), expect={"name": "Card A (filed)"}
    )
    assert result.performed is True
    assert result.verified is True
    assert _ref_of(hands, "Card A (filed)") is not None


def test_drag_without_drop_effect_reports_not_performed():
    hands = _hands()
    result = hands.drag(_ref_of(hands, "Folder"), _ref_of(hands, "Card A"))
    assert result.performed is False
    assert "cannot be dragged" in result.reason


# -- clipboard ------------------------------------------------------


def test_clipboard_write_reads_back_equal():
    hands = _hands(decisions=(APPROVE_SESSION,))
    result = hands.clipboard_write("hello")
    assert result.performed is True
    assert result.verified is True
    assert result.length == 5
    assert "content" not in result.to_dict()
    read = hands.clipboard_read()
    assert read.content == "hello"
    assert read.length == 5
    assert "content" not in read.to_dict()


def test_clipboard_write_prompt_never_carries_the_content():
    prompt = FakePrompt([APPROVE_ONCE, APPROVE_ONCE])
    hands = DesktopHands(FakeBackend(_spec()), prompt=prompt)
    hands.clipboard_write("s3cr3t-token")
    assert "s3cr3t-token" not in prompt.asked[0]
    assert "12 chars" in prompt.asked[0]


# -- windows --------------------------------------------------------


def test_list_windows_reports_both_apps():
    hands = _hands()
    windows = hands.list_windows()
    assert [(w.app, w.title) for w in windows] == [("Notes", "Note"), ("Player", "Player")]
    assert windows[0].focused is True
    assert windows[1].focused is False
    assert windows[0].id == "0.0"


def test_open_app_adds_a_focused_window():
    hands = _hands()
    result = hands.open_app("Calculator")
    assert result.performed is True
    assert result.verified is True
    assert result.window_id == "2.0"
    windows = hands.list_windows()
    assert len(windows) == 3
    assert windows[2].app == "Calculator"
    assert windows[2].focused is True
    assert windows[0].focused is False


def test_open_app_empty_name_raises():
    hands = _hands()
    with pytest.raises(Exception, match="non-empty app name"):
        hands.open_app("   ")


def test_focus_window_moves_focus():
    hands = _hands()
    result = hands.focus_window("1.0")
    assert result.performed is True
    assert result.verified is True
    windows = hands.list_windows()
    assert windows[1].focused is True
    assert windows[0].focused is False


def test_focus_window_unknown_id_raises():
    hands = _hands()
    with pytest.raises(Exception, match="no window with id"):
        hands.focus_window("9.9")


def test_switch_window_cycles_and_verifies():
    hands = _hands(decisions=(APPROVE_SESSION,))
    first = hands.switch_window()
    assert first.performed is True
    assert first.verified is True
    assert first.window_id == "1.0"
    second = hands.switch_window()
    assert second.window_id == "0.0"


def test_switch_window_with_one_window_reports_not_performed():
    spec = _spec()[:1]
    hands = _hands(spec=spec)
    result = hands.switch_window()
    assert result.performed is False
    assert "only one window" in result.reason


# -- permission gate (V2-33 tools) ----------------------------------


def test_type_denied_by_prompt_touches_nothing():
    prompt = FakePrompt([DENY])
    hands = DesktopHands(FakeBackend(_spec()), prompt=prompt)
    with pytest.raises(PermissionDenied):
        hands.type_text(_ref_of(hands, "Body"), "hello")
    assert _value_of(hands, "Body") == ""


def test_token_denying_type_refuses():
    gate = Gate()
    payload = {"tool_permissions": {"computer.type": "deny"}}
    hands = _hands(gate=gate, token_payload=payload)
    with pytest.raises(PermissionDenied):
        hands.type_text(_ref_of(hands, "Body"), "hello")


def test_token_allowing_only_click_denies_type():
    gate = Gate()
    payload = {"tool_permissions": {"computer.click": "allow"}}
    hands = _hands(gate=gate, token_payload=payload)
    with pytest.raises(PermissionDenied):
        hands.type_text(_ref_of(hands, "Body"), "hello")


def test_token_allowing_type_allows_after_prompt():
    gate = Gate()
    payload = {"tool_permissions": {"computer.type": "allow"}}
    prompt = FakePrompt([APPROVE_ONCE])
    hands = DesktopHands(FakeBackend(_spec()), prompt=prompt, gate=gate, token_payload=payload)
    result = hands.type_text(_ref_of(hands, "Body"), "hi")
    assert result.verified is True


def test_open_app_denied_by_prompt_opens_nothing():
    prompt = FakePrompt([DENY])
    hands = DesktopHands(FakeBackend(_spec()), prompt=prompt)
    with pytest.raises(PermissionDenied):
        hands.open_app("Calculator")
    assert len(hands.list_windows()) == 2


def test_session_approval_covers_type_and_windows():
    prompt = FakePrompt([APPROVE_SESSION])
    hands = DesktopHands(FakeBackend(_spec()), prompt=prompt)
    hands.type_text(_ref_of(hands, "Body"), "hi")
    hands.open_app("Calculator")
    hands.switch_window()
    assert len(prompt.asked) == 1


# -- multi-step tasks -----------------------------------------------


def test_run_task_type_shortcut_scroll_switch_end_to_end():
    hands = _hands(decisions=(APPROVE_SESSION,))
    body = _ref_of(hands, "Body")
    task_list = _ref_of(hands, "List")
    outcome = hands.run_task(
        [
            {"action": "type", "ref": body, "text": "hello", "expect": {"value_contains": "hello"}},
            {"action": "shortcut", "keys": "ctrl+a", "ref": body, "expect": {"state": "selected"}},
            {
                "action": "scroll",
                "ref": task_list,
                "direction": "down",
                "amount": 2,
                "expect": {"state": "scrolled"},
            },
            {"action": "switch_window"},
            {"action": "open_app", "name": "Calculator"},
            {"action": "focus_window", "window_id": "0.0"},
        ]
    )
    assert outcome["completed"] is True
    assert len(outcome["steps"]) == 6
    assert all(step["verified"] for step in outcome["steps"])
    assert _value_of(hands, "Body") == "hello"
    windows = hands.list_windows()
    assert len(windows) == 3
    assert windows[0].focused is True


def test_run_task_stops_at_the_first_unverified_step():
    hands = _hands(decisions=(APPROVE_SESSION,))
    outcome = hands.run_task(
        [
            {
                "action": "type",
                "ref": _ref_of(hands, "Body"),
                "text": "hi",
                "expect": {"value": "wrong"},
            },
            {"action": "open_app", "name": "Calculator"},
        ]
    )
    assert outcome["completed"] is False
    assert len(outcome["steps"]) == 1
    assert outcome["steps"][0]["verified"] is False
    assert len(hands.list_windows()) == 2


def test_run_task_unknown_action_stops_with_reason():
    hands = _hands(decisions=(APPROVE_SESSION,))
    outcome = hands.run_task([{"action": "teleport"}])
    assert outcome["completed"] is False
    assert "unknown task action" in outcome["steps"][0]["reason"]


def test_run_task_clipboard_steps_never_carry_content():
    hands = _hands(decisions=(APPROVE_SESSION,))
    outcome = hands.run_task(
        [
            {"action": "clipboard_write", "text": "s3cr3t"},
            {"action": "clipboard_read"},
        ]
    )
    assert outcome["completed"] is True
    for step in outcome["steps"]:
        assert "content" not in step
        assert "s3cr3t" not in str(step)
