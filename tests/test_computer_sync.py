"""Tests for synchronization + non-stale refs (V2-34): settle
waits, identity fingerprints, the stale-ref refusal in
DesktopHands.click, and the stale-target refusal in the visual
fallback. No database, no network, no real desktop: every test
runs on the fake backends, whose snapshot/read hooks change the
screen between the snapshot and the action so the refusal is
genuinely exercised, not assumed.
"""

import argparse
import io
import json
import time

import pytest

from puspa.computer.backend import FakeBackend
from puspa.computer.cli import add_computer_parser, run_computer
from puspa.computer.fallback import (
    FakeCoordinateClicker,
    StaleVisualTargetError,
    VisualFallback,
)
from puspa.computer.hands import DesktopHands, StaleRefError
from puspa.computer.mcp import handle_tool
from puspa.computer.screenshot import FakeScreenshotBackend
from puspa.computer.sync import (
    SettleTimeoutError,
    fingerprint,
    tree_signature,
    visual_fingerprint,
    wait_for_settle,
    words_signature,
)
from puspa.computer.vision import FakeOcrBackend, OcrWord, VisualElement
from puspa.localexec import PermissionDecision, PermissionDenied
from puspa.policy.gate import Gate

pytestmark = pytest.mark.unit

APPROVE_ONCE = PermissionDecision.APPROVE_ONCE


class FakePrompt:
    """A prompt that answers from a queue and records every ask."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.asked = []

    def ask(self, command, cwd):
        self.asked.append(command)
        return self.decisions.pop(0)


def _rows_tree():
    """One app with a three-row list: Alpha, Beta, Gamma. Each
    row is clickable and gains the 'selected' state when it is
    really clicked, so a test can prove which row (if any) got
    the click."""
    return [
        {
            "role": "application",
            "name": "Lister",
            "children": [
                {
                    "role": "frame",
                    "name": "Rows",
                    "children": [
                        {
                            "role": "row",
                            "name": "Alpha",
                            "actions": ["click"],
                            "after_click": {"states_add": ["selected"]},
                        },
                        {
                            "role": "row",
                            "name": "Beta",
                            "actions": ["click"],
                            "after_click": {"states_add": ["selected"]},
                        },
                        {
                            "role": "row",
                            "name": "Gamma",
                            "actions": ["click"],
                            "after_click": {"states_add": ["selected"]},
                        },
                    ],
                }
            ],
        }
    ]


def _ref_of(hands, name):
    shot = hands.snapshot(skeleton=False)

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.children)

    for node in walk(shot.nodes):
        if node.name == name:
            return node.ref
    raise AssertionError(f"no node named {name!r}")


def _row_states(spec, index):
    return spec[0]["children"][0]["children"][index].get("states", [])


def _png_bytes() -> bytes:
    from PIL import Image

    picture = Image.new("RGB", (800, 600), "white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def _words():
    return [
        OcrWord(text="Save", x=100, y=200, width=60, height=24, confidence=0.9),
        OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.8),
    ]


def _fallback(words=None, *, decisions=(APPROVE_ONCE,), on_click=None, on_read=None, **kwargs):
    ocr = FakeOcrBackend(words if words is not None else _words(), on_read=on_read)
    clicker = FakeCoordinateClicker(on_click=on_click)
    fallback = VisualFallback(
        screenshot_backend=FakeScreenshotBackend(_png_bytes()),
        ocr_backend=ocr,
        clicker=clicker,
        prompt=FakePrompt(list(decisions)),
        **kwargs,
    )
    return fallback, ocr, clicker


# ------------------------------------------------------------------
# fingerprints + signatures
# ------------------------------------------------------------------


def test_fingerprint_is_stable_across_identical_snapshots():
    hands = DesktopHands(FakeBackend(_rows_tree()), prompt=FakePrompt([APPROVE_ONCE]))
    first = hands.snapshot(skeleton=False).find("n4")
    second = hands.snapshot(skeleton=False).find("n4")
    assert first is not None and second is not None
    assert fingerprint(first) == fingerprint(second)


def test_fingerprint_changes_when_the_row_above_is_deleted():
    backend = FakeBackend(_rows_tree())
    hands = DesktopHands(backend, prompt=FakePrompt([APPROVE_ONCE]))
    before = hands.snapshot(skeleton=False).find("n4")
    assert before is not None and before.name == "Beta"
    del backend._spec[0]["children"][0]["children"][0]
    after = hands.snapshot(skeleton=False).find("n4")
    assert after is not None and after.name == "Gamma"
    assert fingerprint(after) != fingerprint(before)


def test_tree_signature_stable_when_unchanged_and_moves_on_rename():
    hands = DesktopHands(FakeBackend(_rows_tree()), prompt=FakePrompt([APPROVE_ONCE]))
    first = tree_signature(hands.snapshot(skeleton=False))
    assert tree_signature(hands.snapshot(skeleton=False)) == first
    hands.backend._spec[0]["children"][0]["children"][1]["name"] = "Beta 2"
    assert tree_signature(hands.snapshot(skeleton=False)) != first


def test_visual_fingerprint_changes_when_the_box_moves():
    element = VisualElement(label="Save", x=100, y=200, width=60, height=24)
    moved = VisualElement(label="Save", x=400, y=200, width=60, height=24)
    assert visual_fingerprint(moved) != visual_fingerprint(element)
    assert visual_fingerprint(element) == visual_fingerprint(element)


def test_words_signature_ignores_confidence_wobble():
    words = [OcrWord(text="Save", x=100, y=200, width=60, height=24, confidence=0.9)]
    same = [OcrWord(text="Save", x=100, y=200, width=60, height=24, confidence=0.5)]
    assert words_signature(words) == words_signature(same)


# ------------------------------------------------------------------
# wait_for_settle
# ------------------------------------------------------------------


def test_wait_for_settle_returns_once_reads_agree():
    reads = iter(["changing", "settled", "settled", "settled"])
    value, report = wait_for_settle(lambda: next(reads), poll_interval_s=0)
    assert value == "settled"
    assert report.settled is True
    assert report.snapshots == 3


def test_wait_for_settle_times_out_when_the_screen_never_quiets():
    count = {"n": 0}

    def toggle():
        count["n"] += 1
        return count["n"] % 2

    started = time.monotonic()
    with pytest.raises(SettleTimeoutError) as excinfo:
        wait_for_settle(toggle, timeout_s=0.2, poll_interval_s=0.02)
    assert time.monotonic() - started < 2
    assert excinfo.value.report.settled is False
    assert excinfo.value.report.snapshots >= 2


def test_wait_for_settle_rejects_zero_stable_rounds():
    with pytest.raises(ValueError, match="stable_rounds"):
        wait_for_settle(lambda: 1, stable_rounds=0)


def test_wait_settle_returns_the_settled_snapshot_and_report():
    hands = DesktopHands(FakeBackend(_rows_tree()), prompt=FakePrompt([APPROVE_ONCE]))
    shot, report = hands.wait_settle()
    assert report.settled is True
    assert report.snapshots == 2
    assert shot.total_nodes == 5


# ------------------------------------------------------------------
# tree refs: the stale-ref refusal
# ------------------------------------------------------------------


def test_click_refuses_when_a_row_above_is_deleted_mid_action():
    """The done-when proof for V2-34: the screen changes between
    the snapshot and the action, and the ref that pointed at Beta
    must be refused, never silently clicked onto Gamma."""
    spec = _rows_tree()
    backend = FakeBackend(spec)
    prompt = FakePrompt([APPROVE_ONCE])
    hands = DesktopHands(backend, prompt=prompt)
    ref = _ref_of(hands, "Beta")
    assert ref == "n4"

    calls = {"n": 0}

    def delete_alpha_on_third_snapshot(count):
        calls["n"] += 1
        if calls["n"] == 3:
            del spec[0]["children"][0]["children"][0]

    backend.on_snapshot = delete_alpha_on_third_snapshot
    with pytest.raises(StaleRefError, match="different element"):
        hands.click(ref)
    # The user approved once (the gate passed), then the screen
    # changed: a stale refusal, not a permission denial.
    assert len(prompt.asked) == 1
    assert backend.clicks == []
    assert _row_states(spec, 0) == []  # Beta, now first row, untouched
    assert _row_states(spec, 1) == []  # Gamma, never clicked by mistake


def test_click_refuses_when_the_target_row_itself_is_deleted():
    spec = _rows_tree()
    backend = FakeBackend(spec)
    hands = DesktopHands(backend, prompt=FakePrompt([APPROVE_ONCE]))
    ref = _ref_of(hands, "Gamma")
    assert ref == "n5"

    calls = {"n": 0}

    def delete_gamma_on_third_snapshot(count):
        calls["n"] += 1
        if calls["n"] == 3:
            del spec[0]["children"][0]["children"][2]

    backend.on_snapshot = delete_gamma_on_third_snapshot
    with pytest.raises(StaleRefError, match="gone"):
        hands.click(ref)
    assert backend.clicks == []


def test_click_still_works_when_the_screen_stays_stable():
    spec = _rows_tree()
    backend = FakeBackend(spec)
    hands = DesktopHands(backend, prompt=FakePrompt([APPROVE_ONCE]))
    result = hands.click(_ref_of(hands, "Beta"), expect={"state": "selected"})
    assert result.performed is True
    assert result.verified is True
    assert backend.clicks == [(0, 0, 1)]
    assert _row_states(spec, 1) == ["selected"]


def test_click_refuses_when_the_screen_never_settles():
    spec = _rows_tree()
    backend = FakeBackend(spec)

    def toggle_on_every_snapshot(count):
        rows = spec[0]["children"][0]["children"]
        states = set(rows[0].get("states", ()))
        if "checked" in states:
            states.discard("checked")
        else:
            states.add("checked")
        rows[0]["states"] = sorted(states)

    backend.on_snapshot = toggle_on_every_snapshot
    prompt = FakePrompt([APPROVE_ONCE])
    hands = DesktopHands(backend, prompt=prompt, settle_timeout=0.3, settle_poll_interval=0.02)
    with pytest.raises(SettleTimeoutError):
        hands.click(_ref_of(hands, "Beta"))
    # Settle runs before the prompt: the user was never asked,
    # and nothing was clicked.
    assert prompt.asked == []
    assert backend.clicks == []


def test_stale_refusal_is_not_a_permission_denial():
    """A stale ref raises StaleRefError, which is not a
    PermissionDenied, and a denied token still raises
    PermissionDenied first (the gate runs before the re-resolve)."""
    assert not issubclass(StaleRefError, PermissionDenied)
    gate = Gate()
    payload = {"tool_permissions": {"computer.click": "deny"}}
    hands = DesktopHands(
        FakeBackend(_rows_tree()),
        prompt=FakePrompt([APPROVE_ONCE]),
        gate=gate,
        token_payload=payload,
    )
    with pytest.raises(PermissionDenied):
        hands.click(_ref_of(hands, "Beta"))


# ------------------------------------------------------------------
# visual fallback: the stale-target refusal
# ------------------------------------------------------------------


def test_visual_click_refuses_when_the_target_moves():
    """The OCR proof for V2-34: Save slides to a new box between
    the locate and the press, and the old pixel must never be
    pressed."""
    calls = {"n": 0}

    def move_save_on_third_read(count):
        calls["n"] += 1
        if calls["n"] == 3:
            ocr.set_words(
                [
                    OcrWord(text="Save", x=400, y=200, width=60, height=24, confidence=0.9),
                    OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.8),
                ]
            )

    fallback, ocr, clicker = _fallback(on_read=move_save_on_third_read)
    prompt = fallback._prompt
    with pytest.raises(StaleVisualTargetError, match="moved or changed"):
        fallback.click_text("Save")
    assert len(prompt.asked) == 1
    assert clicker.clicks == []


def test_visual_click_refuses_when_the_target_vanishes():
    def remove_save_on_third_read(count):
        if count == 3:
            ocr.set_words(
                [OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.8)]
            )

    fallback, ocr, clicker = _fallback(on_read=remove_save_on_third_read)
    with pytest.raises(StaleVisualTargetError, match="gone"):
        fallback.click_text("Save")
    assert clicker.clicks == []


def test_visual_click_refuses_when_the_words_never_settle():
    def jiggle_on_every_read(count):
        ocr.set_words(
            [
                OcrWord(
                    text="Save",
                    x=100 + (count % 2),
                    y=200,
                    width=60,
                    height=24,
                    confidence=0.9,
                ),
                OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.8),
            ]
        )

    fallback, ocr, clicker = _fallback(
        on_read=jiggle_on_every_read, settle_timeout=0.3, settle_poll_interval=0.02
    )
    with pytest.raises(SettleTimeoutError):
        fallback.click_text("Save")
    assert clicker.clicks == []


def test_visual_click_still_verifies_when_the_screen_stays_stable():
    def on_click(x, y):
        if 100 <= x < 160 and 200 <= y < 224:
            ocr.set_words(
                [
                    OcrWord(text="Saved", x=100, y=200, width=60, height=24, confidence=0.9),
                    OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.8),
                ]
            )

    fallback, ocr, clicker = _fallback(on_click=on_click)
    result = fallback.click_text("Save", expect={"text": "Saved"})
    assert result.performed is True
    assert result.verified is True
    assert clicker.clicks == [(130, 212)]


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------


def _parse(argv):
    parser = argparse.ArgumentParser(prog="puspa")
    subparsers = parser.add_subparsers(dest="command")
    add_computer_parser(subparsers)
    args = parser.parse_args(argv)
    args.backend = getattr(args, "backend", "auto")
    return args


def _run(argv, **factories):
    out: list[str] = []
    code = run_computer(_parse(argv), print_fn=out.append, **factories)
    return code, out


def _cli_hands(decisions=(APPROVE_ONCE,)):
    return DesktopHands(FakeBackend(_rows_tree()), prompt=FakePrompt(list(decisions)))


def test_cli_wait_reports_settled():
    code, out = _run(["computer", "wait"], hands_factory=lambda kind: _cli_hands())
    assert code == 0
    data = json.loads(out[0])
    assert data["settled"] is True
    assert data["snapshots"] == 2
    assert data["total_nodes"] == 5


def test_cli_wait_timeout_reports_unsettled():
    spec = _rows_tree()
    backend = FakeBackend(spec)

    def toggle(count):
        rows = spec[0]["children"][0]["children"]
        states = set(rows[0].get("states", ()))
        states.symmetric_difference_update({"checked"})
        rows[0]["states"] = sorted(states)

    backend.on_snapshot = toggle
    hands = DesktopHands(
        backend, prompt=FakePrompt([APPROVE_ONCE]), settle_timeout=0.2, settle_poll_interval=0.02
    )
    code, out = _run(["computer", "wait"], hands_factory=lambda kind: hands)
    assert code == 1
    data = json.loads(out[0])
    assert data["settled"] is False


def test_cli_click_stale_ref_reports_refused_not_failed():
    spec = _rows_tree()
    backend = FakeBackend(spec)
    calls = {"n": 0}

    def delete_alpha_on_third_snapshot(count):
        calls["n"] += 1
        if calls["n"] == 3:
            del spec[0]["children"][0]["children"][0]

    backend.on_snapshot = delete_alpha_on_third_snapshot
    hands = DesktopHands(backend, prompt=FakePrompt([APPROVE_ONCE]))
    code, out = _run(["computer", "click", "n4"], hands_factory=lambda kind: hands)
    assert code == 1
    assert "refused: stale ref" in out[0]
    assert "denied by the user" not in out[0]
    assert backend.clicks == []


# ------------------------------------------------------------------
# MCP
# ------------------------------------------------------------------


def test_handle_wait_settle_returns_report():
    hands = DesktopHands(FakeBackend(_rows_tree()), prompt=FakePrompt([APPROVE_ONCE]))
    data = handle_tool("computer_wait_settle", {}, hands)
    assert data["settled"] is True
    assert data["snapshots"] == 2
    assert data["total_nodes"] == 5


def test_handle_wait_settle_timeout_returns_unsettled_not_raise():
    spec = _rows_tree()
    backend = FakeBackend(spec)

    def toggle(count):
        rows = spec[0]["children"][0]["children"]
        states = set(rows[0].get("states", ()))
        states.symmetric_difference_update({"checked"})
        rows[0]["states"] = sorted(states)

    backend.on_snapshot = toggle
    hands = DesktopHands(
        backend, prompt=FakePrompt([APPROVE_ONCE]), settle_timeout=0.2, settle_poll_interval=0.02
    )
    data = handle_tool("computer_wait_settle", {"timeout": 0.2}, hands)
    assert data["settled"] is False


def test_handle_click_raises_stale_ref():
    spec = _rows_tree()
    backend = FakeBackend(spec)
    calls = {"n": 0}

    def delete_alpha_on_third_snapshot(count):
        calls["n"] += 1
        if calls["n"] == 3:
            del spec[0]["children"][0]["children"][0]

    backend.on_snapshot = delete_alpha_on_third_snapshot
    hands = DesktopHands(backend, prompt=FakePrompt([APPROVE_ONCE]))
    with pytest.raises(StaleRefError, match="different element"):
        handle_tool("computer_click", {"ref": "n4"}, hands)
    assert backend.clicks == []
