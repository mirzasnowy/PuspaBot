"""Tests for the MCP tool definitions and the `puspa computer`
CLI (V2-31). No database, no network, no real desktop: the MCP
handlers run on the fake backend, the CLI runs with a fake
hands factory, and the auto-backend path is checked for its
fail-closed error on this headless VM.
"""

import json

import pytest

from puspa.cli import build_parser, main
from puspa.computer.backend import FakeBackend
from puspa.computer.hands import DesktopHands
from puspa.computer.mcp import handle_tool, mcp_server_definition, tool_definitions
from puspa.localexec import PermissionDecision
from puspa.plugins.mcp import validate_mcp_definition

pytestmark = pytest.mark.unit

APPROVE_ONCE = PermissionDecision.APPROVE_ONCE
DENY = PermissionDecision.DENY


class FakePrompt:
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
                    "children": [
                        {
                            "role": "check box",
                            "name": "Pin",
                            "actions": ["click"],
                            "after_click": {"states_add": ["checked"]},
                        }
                    ],
                }
            ],
        }
    ]


def _hands(decisions=(APPROVE_ONCE,)):
    return DesktopHands(FakeBackend(_spec()), prompt=FakePrompt(list(decisions)))


def _ref(hands, name):
    shot = hands.snapshot(skeleton=False)

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.children)

    for node in walk(shot.nodes):
        if node.name == name:
            return node.ref
    raise AssertionError(f"no node named {name!r}")


# -- MCP definitions ------------------------------------------------


def test_nineteen_tools_with_names_descriptions_and_schemas():
    tools = tool_definitions()
    assert [tool["name"] for tool in tools] == [
        "computer_snapshot",
        "computer_click",
        "computer_verify",
        "computer_type",
        "computer_shortcut",
        "computer_scroll",
        "computer_drag",
        "computer_clipboard_read",
        "computer_clipboard_write",
        "computer_list_windows",
        "computer_open_app",
        "computer_focus_window",
        "computer_switch_window",
        "computer_run_task",
        "computer_wait_settle",
        "computer_visual_locate",
        "computer_visual_click",
        "computer_web_open",
        "computer_web_click",
    ]
    for tool in tools:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"
        assert "properties" in tool["inputSchema"]
    assert tool_definitions()[1]["inputSchema"]["required"] == ["ref"]
    assert tool_definitions()[2]["inputSchema"]["required"] == ["ref"]
    assert tool_definitions()[3]["inputSchema"]["required"] == ["ref", "text"]
    assert tool_definitions()[13]["inputSchema"]["required"] == ["steps"]
    # The V2-34 wait tool takes no required arguments.
    assert tool_definitions()[14]["inputSchema"]["required"] == []
    # The V2-32 tools keep the same shape: one required string each.
    assert tool_definitions()[15]["inputSchema"]["required"] == ["text"]
    assert tool_definitions()[16]["inputSchema"]["required"] == ["text"]
    assert tool_definitions()[17]["inputSchema"]["required"] == ["url"]
    assert tool_definitions()[18]["inputSchema"]["required"] == ["text"]


def test_server_definition_validates():
    definition = mcp_server_definition()
    assert validate_mcp_definition(definition) == []
    assert definition["server"]["command"] == "puspa"
    assert definition["server"]["args"] == ["mcp"]


def test_handle_snapshot_returns_json_tree():
    hands = _hands()
    data = handle_tool("computer_snapshot", {"skeleton": True}, hands)
    assert data["backend"] == "fake"
    assert data["nodes"][0]["name"] == "Notes"


def test_handle_click_runs_the_gated_loop():
    hands = _hands()
    ref = _ref(hands, "Pin")
    result = handle_tool("computer_click", {"ref": ref, "expect": {"state": "checked"}}, hands)
    assert result["performed"] is True
    assert result["verified"] is True


def test_handle_verify_reads_without_clicking():
    hands = _hands()
    ref = _ref(hands, "Pin")
    result = handle_tool("computer_verify", {"ref": ref, "expect": {"state": "checked"}}, hands)
    assert result["found"] is True
    assert result["verified"] is False


def test_handle_unknown_tool_raises():
    with pytest.raises(ValueError, match="unknown computer tool"):
        handle_tool("computer_nope", {}, _hands())


def test_handle_type_into_non_editable_reports_not_performed():
    hands = _hands()
    ref = _ref(hands, "Pin")
    result = handle_tool("computer_type", {"ref": ref, "text": "hi"}, hands)
    assert result["performed"] is False
    assert "not editable" in result["reason"]


def test_handle_type_without_text_raises():
    with pytest.raises(ValueError, match="needs a string 'text'"):
        handle_tool("computer_type", {"ref": "n1"}, _hands())


def test_handle_clipboard_write_and_read_round_trip():
    hands = _hands(decisions=(APPROVE_ONCE, APPROVE_ONCE))
    written = handle_tool("computer_clipboard_write", {"text": "hello"}, hands)
    assert written["performed"] is True
    assert written["verified"] is True
    assert written["length"] == 5
    assert "content" not in written
    read = handle_tool("computer_clipboard_read", {}, hands)
    assert read["content"] == "hello"
    assert read["length"] == 5


def test_handle_list_windows_returns_fake_windows():
    hands = _hands()
    data = handle_tool("computer_list_windows", {}, hands)
    assert data["windows"][0]["app"] == "Notes"


def test_handle_run_task_runs_steps_end_to_end():
    hands = _hands(decisions=(APPROVE_ONCE, APPROVE_ONCE))
    outcome = handle_tool(
        "computer_run_task",
        {"steps": [{"action": "open_app", "name": "Calculator"}, {"action": "list_windows"}]},
        hands,
    )
    assert outcome["completed"] is True
    assert len(outcome["steps"]) == 2


def test_handle_click_without_ref_raises():
    with pytest.raises(ValueError, match="needs a string 'ref'"):
        handle_tool("computer_click", {}, _hands())


def test_handle_click_denied_by_prompt_raises():
    hands = _hands(decisions=(DENY,))
    ref = _ref(hands, "Pin")
    with pytest.raises(Exception, match="denied by the user"):
        handle_tool("computer_click", {"ref": ref}, hands)


# -- CLI ------------------------------------------------------------


def test_cli_snapshot_fake_prints_json(capsys):
    assert main(["computer", "--backend", "fake", "snapshot"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["backend"] == "fake"
    assert data["nodes"][0]["name"] == "Demo Notes"


def test_cli_snapshot_auto_fails_closed_on_headless_vm(monkeypatch, capsys):
    for key in ("AT_SPI_BUS_ADDRESS", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.setenv(key, "")
    assert main(["computer", "snapshot"]) == 1
    assert "no accessibility bus" in capsys.readouterr().out


def test_cli_click_fake_approves_and_verifies(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["computer", "--backend", "fake", "click", "n4", "--expect-state", "checked"]) == 0
    out = capsys.readouterr().out
    # The permission prompt prints its question first; the JSON result follows it.
    data = json.loads(out[out.index("{") :])
    assert data["performed"] is True
    assert data["verified"] is True


def test_cli_click_fake_denied_exits_1(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert main(["computer", "--backend", "fake", "click", "n4"]) == 1
    assert "refused" in capsys.readouterr().out


def test_cli_verify_fake_unmet_expectation_exits_1(capsys):
    assert main(["computer", "--backend", "fake", "verify", "n4", "--expect-state", "checked"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["verified"] is False


def test_cli_parser_knows_computer():
    parser = build_parser()
    args = parser.parse_args(["computer", "--backend", "fake", "snapshot", "--full"])
    assert args.command == "computer"
    assert args.computer_command == "snapshot"
    assert args.full is True


def test_cli_backend_flag_works_after_the_subcommand_too(capsys):
    assert main(["computer", "snapshot", "--backend", "fake"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["backend"] == "fake"


def test_cli_type_fake_approves_and_verifies(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert (
        main(
            [
                "computer",
                "--backend",
                "fake",
                "type",
                "n5",
                "hello",
                "--expect-value-contains",
                "hello",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    data = json.loads(out[out.index("{") :])
    assert data["action"] == "type"
    assert data["performed"] is True
    assert data["verified"] is True


def test_cli_type_into_non_editable_exits_1(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["computer", "--backend", "fake", "type", "n3", "hello"]) == 1


def test_cli_shortcut_fake_approves_and_verifies(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert (
        main(
            [
                "computer",
                "--backend",
                "fake",
                "shortcut",
                "ctrl+a",
                "--ref",
                "n5",
                "--expect-state",
                "selected",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    data = json.loads(out[out.index("{") :])
    assert data["action"] == "shortcut"
    assert data["verified"] is True


def test_cli_windows_fake_lists_both_apps(capsys):
    assert main(["computer", "--backend", "fake", "windows"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [window["app"] for window in data] == ["Demo Notes", "Demo Player"]


def test_cli_clipboard_write_reports_length_only(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["computer", "--backend", "fake", "clipboard", "write", "abc"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out[out.index("{") :])
    assert data["length"] == 3
    assert "abc" not in out


def test_cli_clipboard_read_reports_length_only(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["computer", "--backend", "fake", "clipboard", "read"]) == 0
    out = capsys.readouterr().out
    # The permission prompt prints its question first; the JSON result follows it.
    data = json.loads(out[out.index("{") :])
    assert data["length"] == 0


def test_cli_task_fake_runs_steps_end_to_end(capsys, monkeypatch, tmp_path):
    steps_file = tmp_path / "steps.json"
    steps_file.write_text(
        json.dumps(
            [
                {
                    "action": "type",
                    "ref": "n5",
                    "text": "hello",
                    "expect": {"value_contains": "hello"},
                },
                {
                    "action": "shortcut",
                    "keys": "ctrl+a",
                    "ref": "n5",
                    "expect": {"state": "selected"},
                },
                {"action": "switch_window"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["computer", "--backend", "fake", "task", str(steps_file)]) == 0
    out = capsys.readouterr().out
    data = json.loads(out[out.index("{") :])
    assert data["completed"] is True
    assert len(data["steps"]) == 3


def test_cli_task_stops_at_unverified_step(capsys, monkeypatch, tmp_path):
    steps_file = tmp_path / "steps.json"
    steps_file.write_text(
        json.dumps(
            [
                {"action": "type", "ref": "n5", "text": "hi", "expect": {"value": "wrong"}},
                {"action": "open_app", "name": "Calculator"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["computer", "--backend", "fake", "task", str(steps_file)]) == 1
    out = capsys.readouterr().out
    data = json.loads(out[out.index("{") :])
    assert data["completed"] is False
    assert len(data["steps"]) == 1
