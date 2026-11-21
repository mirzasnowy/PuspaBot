"""Tests for V2-35: permission tiers, per-app allowlist,
emergency stop, ledger audit, close-window, and the new MCP
tools and CLI commands. No database, no network, no real
desktop: the hands run on the fake backend, the audit runs on
the in-memory sink, and the allowlist/stop files live under
tmp_path.
"""

import json

import pytest

from puspa.cli import main
from puspa.computer.allowlist import Allowlist
from puspa.computer.audit import InMemoryComputerAudit, LedgerComputerAudit, audit_entry
from puspa.computer.backend import FakeBackend
from puspa.computer.hands import ComputerStopped, DesktopHands
from puspa.computer.mcp import handle_tool, tool_definitions
from puspa.computer.stop import ComputerStop
from puspa.computer.tiers import Tier, classify, is_submit_shortcut
from puspa.ledger import Ledger
from puspa.localexec import PermissionDecision, PermissionDenied

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
                                    "name": "Flag",
                                    "actions": ["click"],
                                    "after_click": {"states_add": ["selected"]},
                                },
                            ],
                        },
                        {
                            "role": "entry",
                            "name": "Title",
                            "editable": True,
                            "value": "",
                        },
                        {
                            "role": "list",
                            "name": "Items",
                            "scrollable": True,
                            "after_drag": {"name": "Dropped"},
                            "children": [
                                {"role": "list item", "name": "Item one"},
                                {"role": "list item", "name": "Item two"},
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


def _hands(tmp_path, decisions=(APPROVE_ONCE,), **kwargs):
    backend = FakeBackend(_tree())
    audit = InMemoryComputerAudit()
    hands = DesktopHands(
        backend,
        prompt=FakePrompt(list(decisions)),
        allowlist=Allowlist(tmp_path / "allowlist.json"),
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=audit,
        **kwargs,
    )
    return hands, audit


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


# -- tiers ------------------------------------------------------------


def test_classify_reads_are_free():
    for action in ("snapshot", "verify", "list_windows", "visual_locate", "web_open", "web_find"):
        assert classify(action) is Tier.READ


def test_classify_normal_actions():
    for action in (
        "click",
        "visual_click",
        "web_click",
        "type",
        "shortcut",
        "scroll",
        "focus_window",
        "switch_window",
        "open_app",
        "clipboard_read",
    ):
        assert classify(action) is Tier.NORMAL


def test_classify_destructive_actions_always_ask():
    for action in ("clipboard_write", "drag", "close_window"):
        assert classify(action) is Tier.DESTRUCTIVE


def test_classify_submitting_type_and_shortcut_are_destructive():
    assert classify("type", submits=True) is Tier.DESTRUCTIVE
    assert classify("type", submits=False) is Tier.NORMAL
    assert classify("shortcut", submits=True) is Tier.DESTRUCTIVE
    # A drag is destructive already; submits changes nothing.
    assert classify("drag", submits=True) is Tier.DESTRUCTIVE


def test_classify_unknown_action_asks_instead_of_running_free():
    assert classify("explode") is Tier.NORMAL


def test_is_submit_shortcut_spots_enter_and_return():
    assert is_submit_shortcut("enter") is True
    assert is_submit_shortcut("ctrl+return") is True
    assert is_submit_shortcut(("ctrl", "a")) is False
    assert is_submit_shortcut("ctrl+shift+s") is False


# -- allowlist ----------------------------------------------------------


def test_allowlist_add_list_revoke_persists(tmp_path):
    path = tmp_path / "allowlist.json"
    allowlist = Allowlist(path)
    assert allowlist.apps() == []
    assert allowlist.is_allowed("Notes") is False
    assert allowlist.allow("Notes") is True
    assert allowlist.allow("notes") is False  # already listed, case-insensitive
    assert allowlist.apps() == ["Notes"]
    assert Allowlist(path).apps() == ["Notes"]  # a fresh instance reads the file
    assert Allowlist(path).is_allowed("  NOTES ") is True
    assert allowlist.revoke("notes") is True
    assert allowlist.revoke("Notes") is False
    assert allowlist.apps() == []


def test_allowlist_empty_name_rejected(tmp_path):
    with pytest.raises(ValueError):
        Allowlist(tmp_path / "allowlist.json").allow("   ")


def test_allowlist_missing_or_broken_file_reads_empty(tmp_path):
    assert Allowlist(tmp_path / "nope.json").apps() == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert Allowlist(broken).apps() == []


# -- emergency stop -----------------------------------------------------


def test_stop_request_clear_and_state(tmp_path):
    path = tmp_path / "stop.json"
    stop = ComputerStop(path)
    assert stop.is_active() is False
    state = stop.request(reason="too fast")
    assert state["active"] is True
    assert state["reason"] == "too fast"
    assert ComputerStop(path).is_active() is True  # a fresh instance sees the file
    with pytest.raises(ComputerStopped):
        stop.check()
    cleared = stop.clear(reason="ok now")
    assert cleared["active"] is False
    assert ComputerStop(path).is_active() is False
    stop.check()  # no raise when clear


def test_stop_corrupt_file_reads_active(tmp_path):
    path = tmp_path / "stop.json"
    path.write_text("{broken", encoding="utf-8")
    stop = ComputerStop(path)
    assert stop.is_active() is True  # fail closed, never silently off
    assert stop.clear()["active"] is False  # explicit clear recovers


# -- tiers in the hands ---------------------------------------------------


def test_allowlisted_app_click_runs_without_asking(tmp_path):
    hands, audit = _hands(tmp_path, decisions=())
    hands.allowlist.allow("Notes")
    result = hands.click(_ref_of(hands, "Pin"))
    assert result.performed is True
    assert result.verified is True
    # No prompt was asked: an empty decision queue would have raised.
    entry = audit.entries[-1]
    assert entry["computer_action"] == "click"
    assert entry["tier"] == "normal"
    assert entry["decision"] == "allow"
    assert entry["app"] == "Notes"


def test_non_allowlisted_app_click_still_asks(tmp_path):
    prompt = FakePrompt([APPROVE_ONCE])
    backend = FakeBackend(_tree())
    hands = DesktopHands(
        backend,
        prompt=prompt,
        allowlist=Allowlist(tmp_path / "allowlist.json"),
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=InMemoryComputerAudit(),
    )
    result = hands.click(_ref_of(hands, "Reply"))  # Reply lives in Mail, not allowlisted
    assert result.performed is True
    assert len(prompt.asked) == 1


def test_destructive_drag_asks_even_in_allowlisted_app(tmp_path):
    prompt = FakePrompt([APPROVE_ONCE])
    backend = FakeBackend(_tree())
    allowlist = Allowlist(tmp_path / "allowlist.json")
    allowlist.allow("Notes")
    hands = DesktopHands(
        backend,
        prompt=prompt,
        allowlist=allowlist,
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=InMemoryComputerAudit(),
    )
    ref_from = _ref_of(hands, "Items")
    ref_to = _ref_of(hands, "Item two")
    result = hands.drag(ref_from, ref_to)
    assert result.performed is True
    assert len(prompt.asked) == 1  # asked, even though Notes is allowlisted


def test_submitting_type_asks_even_in_allowlisted_app(tmp_path):
    prompt = FakePrompt([APPROVE_ONCE])
    backend = FakeBackend(_tree())
    allowlist = Allowlist(tmp_path / "allowlist.json")
    allowlist.allow("Notes")
    hands = DesktopHands(
        backend,
        prompt=prompt,
        allowlist=allowlist,
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=InMemoryComputerAudit(),
    )
    result = hands.type_text(_ref_of(hands, "Title"), "hello", submits=True)
    assert result.performed is True
    assert len(prompt.asked) == 1


def test_plain_type_is_free_in_allowlisted_app(tmp_path):
    hands, _ = _hands(tmp_path, decisions=())
    hands.allowlist.allow("Notes")
    result = hands.type_text(_ref_of(hands, "Title"), "hello")
    assert result.performed is True


def test_enter_shortcut_asks_even_in_allowlisted_app(tmp_path):
    prompt = FakePrompt([APPROVE_ONCE])
    backend = FakeBackend(_tree())
    allowlist = Allowlist(tmp_path / "allowlist.json")
    allowlist.allow("Notes")
    hands = DesktopHands(
        backend,
        prompt=prompt,
        allowlist=allowlist,
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=InMemoryComputerAudit(),
    )
    # The fake backend knows no "enter" chord, so it reports not
    # performed, but the prompt must still have been asked first.
    hands.shortcut("enter", _ref_of(hands, "Title"))
    assert len(prompt.asked) == 1


def test_denied_action_is_audited_as_deny(tmp_path):
    prompt = FakePrompt([DENY])
    backend = FakeBackend(_tree())
    audit = InMemoryComputerAudit()
    hands = DesktopHands(
        backend,
        prompt=prompt,
        allowlist=Allowlist(tmp_path / "allowlist.json"),
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=audit,
    )
    with pytest.raises(PermissionDenied):
        hands.click(_ref_of(hands, "Pin"))
    entry = audit.entries[-1]
    assert entry["computer_action"] == "click"
    assert entry["decision"] == "deny"
    assert entry["outcome"] == "denied"


def test_stop_refuses_a_single_action(tmp_path):
    hands, audit = _hands(tmp_path, decisions=(APPROVE_ONCE,))
    hands.stop.request(reason="halt")
    with pytest.raises(ComputerStopped):
        hands.click(_ref_of(hands, "Pin"))
    entry = audit.entries[-1]
    assert entry["decision"] == "stop"
    assert entry["outcome"] == "stopped"


def test_stop_halts_a_task_at_the_next_step(tmp_path):
    prompt = FakePrompt([APPROVE_ONCE])

    def stop_after_first_ask(command, cwd):
        prompt.asked.append(command)
        # The user hits stop while the first step runs.
        hands.stop.request(reason="halt mid-task")
        return APPROVE_ONCE

    backend = FakeBackend(_tree())
    audit = InMemoryComputerAudit()
    hands = DesktopHands(
        backend,
        prompt=stop_after_first_ask,
        allowlist=Allowlist(tmp_path / "allowlist.json"),
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=audit,
    )
    outcome = hands.run_task(
        [
            {"action": "click", "ref": _ref_of(hands, "Pin")},
            {"action": "click", "ref": _ref_of(hands, "Flag")},
        ]
    )
    assert outcome["completed"] is False
    assert outcome["stopped"] is True
    assert len(outcome["steps"]) == 2
    assert outcome["steps"][0]["performed"] is True  # the first step ran
    assert outcome["steps"][1]["performed"] is False
    assert outcome["steps"][1]["stopped"] is True  # the second never ran


def test_stop_already_set_before_a_task_runs_no_step(tmp_path):
    hands, _ = _hands(tmp_path, decisions=(APPROVE_ONCE,))
    hands.stop.request(reason="halt")
    outcome = hands.run_task([{"action": "click", "ref": _ref_of(hands, "Pin")}])
    assert outcome["completed"] is False
    assert outcome["stopped"] is True
    assert outcome["steps"][0]["performed"] is False


def test_run_task_supports_close_window_step(tmp_path):
    hands, _ = _hands(tmp_path, decisions=(APPROVE_ONCE, APPROVE_ONCE))
    outcome = hands.run_task(
        [
            {"action": "list_windows"},
            {"action": "close_window", "window_id": "1.0"},
        ]
    )
    assert outcome["completed"] is True
    assert [w.app for w in hands.list_windows()] == ["Notes"]


# -- audit: never content, only lengths -----------------------------------


def test_audit_never_carries_typed_text_or_clipboard_content(tmp_path):
    hands, audit = _hands(tmp_path, decisions=(APPROVE_ONCE, APPROVE_ONCE, APPROVE_ONCE))
    secret = "s3cr3t-password"
    hands.type_text(_ref_of(hands, "Title"), secret)
    hands.clipboard_write(secret)
    hands.clipboard_read()
    blob = json.dumps(audit.entries)
    assert secret not in blob
    typed = next(e for e in audit.entries if e["computer_action"] == "type")
    assert typed["length"] == len(secret)
    assert typed["target"] == _ref_of(hands, "Title")
    written = next(e for e in audit.entries if e["computer_action"] == "clipboard_write")
    assert written["length"] == len(secret)
    assert written["target"] == "clipboard"


def test_audit_entry_shape():
    entry = audit_entry(
        "click", target="n3", tier="normal", decision="allow", outcome="performed", app="Notes"
    )
    assert entry["computer_action"] == "click"
    assert entry["target"] == "n3"
    assert entry["tier"] == "normal"
    assert entry["decision"] == "allow"
    assert entry["outcome"] == "performed"
    assert entry["app"] == "Notes"
    assert "content" not in entry


def test_ledger_audit_round_trip(test_dsn, ledger):
    """The ledger sink writes real tool_called events and reads
    them back newest first (uses the shared test database, on
    its own task stream so other tests' rows cannot leak in:
    this file is marked unit, so conftest does not truncate
    tables around these tests)."""
    sink = LedgerComputerAudit(ledger, task_id="computer-tiers-test")
    assert sink.record(
        audit_entry(
            "click", target="n3", tier="normal", decision="allow", outcome="performed", app="Notes"
        )
    )
    assert sink.record_stop_requested(reason="halt")
    entries = sink.recent(10)
    assert entries[0]["computer_stop"] == "requested"
    assert entries[1]["computer_action"] == "click"
    assert entries[1]["tier"] == "normal"
    assert entries[1]["target"] == "n3"
    stored = Ledger(test_dsn).events("computer-tiers-test", event_type="tool_called")
    assert len(stored) == 1
    assert stored[0]["content"]["computer_action"] == "click"


# -- close-window ---------------------------------------------------------


def test_close_window_fake_closes_and_verifies(tmp_path):
    hands, audit = _hands(tmp_path, decisions=(APPROVE_ONCE,))
    assert len(hands.list_windows()) == 2
    result = hands.close_window("1.0")  # the Mail window
    assert result.performed is True
    assert result.verified is True
    assert [w.app for w in hands.list_windows()] == ["Notes"]
    entry = next(e for e in audit.entries if e["computer_action"] == "close_window")
    assert entry["tier"] == "destructive"
    assert entry["target"] == "1.0"


def test_close_window_unknown_id_raises(tmp_path):
    hands, _ = _hands(tmp_path, decisions=(APPROVE_ONCE,))
    with pytest.raises(Exception, match="no window with id"):
        hands.close_window("9.9")


def test_close_window_asks_even_in_allowlisted_app(tmp_path):
    prompt = FakePrompt([APPROVE_ONCE])
    backend = FakeBackend(_tree())
    allowlist = Allowlist(tmp_path / "allowlist.json")
    allowlist.allow("Mail")
    hands = DesktopHands(
        backend,
        prompt=prompt,
        allowlist=allowlist,
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=InMemoryComputerAudit(),
    )
    hands.close_window("1.0")
    assert len(prompt.asked) == 1


# -- MCP tools --------------------------------------------------------------


def test_handle_allowlist_stop_audit_and_close_window(tmp_path):
    hands, _audit = _hands(tmp_path, decisions=(APPROVE_ONCE, APPROVE_ONCE))
    assert handle_tool("computer_allowlist", {"op": "list"}, hands) == {"apps": []}
    assert handle_tool("computer_allowlist", {"op": "add", "app": "Notes"}, hands) == {
        "app": "Notes",
        "allowed": True,
    }
    assert handle_tool("computer_allowlist", {"op": "list"}, hands) == {"apps": ["Notes"]}
    assert handle_tool("computer_allowlist", {"op": "remove", "app": "Notes"}, hands) == {
        "app": "Notes",
        "allowed": False,
    }
    state = handle_tool("computer_stop", {"op": "request", "reason": "halt"}, hands)
    assert state["active"] is True
    assert handle_tool("computer_stop", {"op": "status"}, hands)["active"] is True
    with pytest.raises(ComputerStopped):
        handle_tool("computer_click", {"ref": _ref_of(hands, "Pin")}, hands)
    state = handle_tool("computer_stop", {"op": "clear", "reason": "go"}, hands)
    assert state["active"] is False
    clicked = handle_tool("computer_click", {"ref": _ref_of(hands, "Pin")}, hands)
    assert clicked["performed"] is True
    listed = handle_tool("computer_audit", {"limit": 10}, hands)
    assert listed["ledger_available"] is True
    actions = [e["computer_action"] for e in listed["entries"] if "computer_action" in e]
    assert "click" in actions
    stops = [e["computer_stop"] for e in listed["entries"] if "computer_stop" in e]
    assert stops == ["cleared", "requested"]
    closed = handle_tool("computer_close_window", {"window_id": "1.0"}, hands)
    assert closed["performed"] is True
    assert closed["verified"] is True
    assert [w.app for w in hands.list_windows()] == ["Notes"]


def test_handle_allowlist_and_stop_reject_bad_args(tmp_path):
    hands, _ = _hands(tmp_path, decisions=())
    with pytest.raises(ValueError):
        handle_tool("computer_allowlist", {"op": "explode"}, hands)
    with pytest.raises(ValueError):
        handle_tool("computer_allowlist", {"op": "add", "app": "  "}, hands)
    with pytest.raises(ValueError):
        handle_tool("computer_stop", {"op": "explode"}, hands)
    with pytest.raises(ValueError):
        handle_tool("computer_close_window", {"window_id": ""}, hands)
    with pytest.raises(ValueError):
        handle_tool("computer_audit", {"limit": -1}, hands)


def test_handle_audit_without_a_sink_reports_unavailable(tmp_path):
    backend = FakeBackend(_tree())
    hands = DesktopHands(
        backend,
        prompt=FakePrompt([]),
        allowlist=Allowlist(tmp_path / "allowlist.json"),
        stop=ComputerStop(tmp_path / "stop.json"),
        audit=None,
    )
    assert handle_tool("computer_audit", {}, hands) == {"entries": [], "ledger_available": False}


def test_tool_definitions_cover_the_new_tools():
    by_name = {tool["name"]: tool for tool in tool_definitions()}
    for name in ("computer_close_window", "computer_allowlist", "computer_stop", "computer_audit"):
        assert name in by_name
        assert by_name[name]["description"].strip()


# -- CLI --------------------------------------------------------------------


def test_cli_allow_list_add_revoke(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PUSPA_COMPUTER_STATE_DIR", str(tmp_path))
    assert main(["computer", "allow"]) == 0
    assert json.loads(capsys.readouterr().out) == {"apps": []}
    assert main(["computer", "allow", "Notes"]) == 0
    assert json.loads(capsys.readouterr().out) == {"app": "Notes", "allowed": True, "changed": True}
    assert main(["computer", "allow"]) == 0
    assert json.loads(capsys.readouterr().out) == {"apps": ["Notes"]}
    assert main(["computer", "allow", "--revoke", "Notes"]) == 0
    assert json.loads(capsys.readouterr().out)["allowed"] is False


def test_cli_stop_request_status_clear(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PUSPA_COMPUTER_STATE_DIR", str(tmp_path))
    assert main(["computer", "stop", "--reason", "halt"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is True
    assert main(["computer", "stop", "--status"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is True
    assert main(["computer", "stop", "--clear", "--reason", "go"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is False


def test_cli_audit_lists_ledger_entries(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PUSPA_COMPUTER_STATE_DIR", str(tmp_path))
    assert main(["computer", "--backend", "fake", "snapshot"]) == 0
    capsys.readouterr()
    assert main(["computer", "audit", "--limit", "5"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ledger_available"] is True
    actions = [e["computer_action"] for e in data["entries"] if "computer_action" in e]
    assert "snapshot" in actions


def test_cli_close_window_fake(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PUSPA_COMPUTER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["computer", "--backend", "fake", "close-window", "0.0"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out[out.index("{") :])
    assert data["action"] == "close_window"
    assert data["performed"] is True
    assert data["verified"] is True
