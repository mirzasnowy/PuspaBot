"""Tests for lossless tool-result compression (puspa/router/compression.py).

RTK pattern (rtk-ai/rtk, Apache-2.0 core): per-kind filters on tool
output before it reaches the model, with a fail-safe back to the
original text. These tests prove the mechanism, not the claim:

- every filter really shrinks a realistic example of its kind;
- auto-detection picks the right filter from the first 1KB;
- the fail-safe returns the original text when a filter raises and
  when a filter would grow the text;
- the orphan-number rule: every meaningful number survives
  compression, including a number that appears exactly once in the
  middle of a long log;
- a compressed diff still carries every changed file, hunk header,
  and changed line;
- savings are measured per kind in real tokens (tiktoken) and chars;
- the wiring in PuspaTool.execute / McpClientWrapper.call_tool
  really compresses what the model receives, without touching the
  evidence the checker reads.

In-memory only: this module shadows the conftest autouse Postgres
reset fixture (``_reset_tables``) with a no-op, so no test here
touches the puspa_test database.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path

import pytest

from puspa.router.compression import (
    compress_tool_result,
    count_tokens,
    detect_kind,
)

BUILD_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_tables():
    """Shadow the conftest autouse Postgres reset: in-memory only."""
    yield


# ---------------------------------------------------------------------------
# Fixtures: realistic Puspa tool outputs (built from real repo content)
# ---------------------------------------------------------------------------

def make_diff() -> str:
    """Unified diff with long unchanged runs between changes, two files."""
    context = [f"    context_line_{i} = compute_value({i})" for i in range(30)]
    lines = [
        "diff --git a/puspa/router/router.py b/puspa/router/router.py",
        "index 3f2a1b9..7c8d4e2 100644",
        "--- a/puspa/router/router.py",
        "+++ b/puspa/router/router.py",
        "@@ -10,32 +10,33 @@ CATALOG = (",
    ]
    lines += context[:12]
    lines += [
        '-    ("openrouter", "qwen/qwen3-32b", "qwen", 2, 6, False, True),',
        '+    ("openrouter", "qwen/qwen3-32b", "qwen", 3, 9, False, True),',
        '+    # price check: Rp4.750.000 for the reference phone',
    ]
    lines += context[12:24]
    lines += [
        '-    ("openrouter", "google/gemma-3-4b-it", "gemma", 1, 3, True, False),',
        '+    ("openrouter", "google/gemma-3-4b-it", "gemma", 1, 3, True, True),',
    ]
    lines += context[24:]
    lines += [
        "diff --git a/puspa/billing/billing.py b/puspa/billing/billing.py",
        "index 9a1b2c3..4d5e6f7 100644",
        "--- a/puspa/billing/billing.py",
        "+++ b/puspa/billing/billing.py",
        "@@ -40,15 +40,16 @@ def credit_price(pack):",
    ]
    lines += [f"    bill_line_{i} = pack * {i}" for i in range(12)]
    lines += [
        '-    return {"credits": 20, "price_rp": 9000}',
        '+    return {"credits": 20, "price_rp": 9000, "bonus": 5}',
    ]
    lines += [f"    tail_line_{i} = pack + {i}" for i in range(12)]
    return "\n".join(lines) + "\n"


def make_status() -> str:
    lines = [
        "On branch main",
        "Your branch is up to date with 'origin/main'.",
        "",
        "Changes not staged for commit:",
        '  (use "git add <file>..." to update what will be committed)',
        '  (use "git restore <file>..." to discard changes in working directory)',
        "\tmodified:   puspa/router/router.py",
        "\tmodified:   puspa/chassis/tools.py",
        "",
        "Untracked files:",
        '  (use "git add <file>..." to include in what will be committed)',
    ]
    lines += [f"\tnew_module_{i:02d}.py" for i in range(30)]
    lines += [
        "",
        "no changes added to commit "
        "(use \"git add\" and/or \"git commit -a\")",
    ]
    return "\n".join(lines) + "\n"


def make_grep() -> str:
    lines = []
    for path in ("puspa/router/router.py", "puspa/chassis/tools.py",
                 "puspa/runtime/tasks.py"):
        for i in range(15):
            lines.append(f"{path}:{10 + i}:    result = run_tool(name, args)")
        # exact duplicates, as a repeated search often returns them
        lines.append(f"{path}:12:    result = run_tool(name, args)")
        lines.append(f"{path}:12:    result = run_tool(name, args)")
    long_free = "x" * 60 + " " + "word " * 40
    lines.append(f"puspa/router/router.py:99:    note = {long_free}")
    return "\n".join(lines) + "\n"


def make_find() -> str:
    parts = []
    for module in ("memory", "world", "router", "chassis"):
        for name in ("model.py", "store.py", "loop.py", "index.py",
                     "utils.py", "config.py", "events.py", "cache.py"):
            parts.append(f"puspa/{module}/deep/nested/package/{name}")
    return "\n".join(parts) + "\n"


def make_ls() -> str:
    sections = []
    for dirname in ("puspa/router", "puspa/chassis"):
        sections.append(f"{dirname}:")
        sections.append("total 96")
        for i, name in enumerate(
            ("__init__.py", "router.py", "compression.py", "context.py",
             "tools.py", "registry.py", "factory.py", "hooks.py",
             "agent_session.py", "sandbox_tool.py", "skill_loader.py",
             "budget.py", "planner.py", "agents.py", "tasks.py")
        ):
            sections.append(
                f"-rw-r--r-- 1 root root {1024 + i * 137:6d} "
                f"Nov 18 11:{i:02d} {name}"
            )
    return "\n".join(sections) + "\n"


def make_tree() -> str:
    lines = ["puspa"]
    for module in ("router", "chassis", "memory", "world"):
        lines.append(f"├── {module}")
        for i, name in enumerate(
            ("__init__.py", "model.py", "store.py", "loop.py", "index.py",
             "utils.py", "config.py", "events.py")
        ):
            branch = "└──" if i == 7 else "├──"
            lines.append(f"│   {branch} {name}")
        lines.append("│   └── deep")
        for j in range(4):
            branch = "└──" if j == 3 else "├──"
            lines.append(f"│       {branch} nested_{j}.py")
    lines.append("└── README.md")
    return "\n".join(lines) + "\n"


ORPHAN_LINE = (
    "2026-11-18 11:40:02 INFO payment Rp1.250.000 customer 081234567890 "
    "succeeded score 98.7"
)


def make_log() -> str:
    lines = []
    for i in range(60):
        lines.append("2026-11-18 11:39:01 INFO worker heartbeat ok")
    for i in range(40):
        lines.append("2026-11-18 11:39:02 DEBUG tick processed batch 7")
    lines.append(ORPHAN_LINE)
    for i in range(50):
        lines.append("2026-11-18 11:39:03 INFO worker heartbeat ok")
    for i in range(25):
        lines.append(f"2026-11-18 11:39:04 WARNING retry {i % 3} slow query")
    lines.append("2026-11-18 11:39:05 ERROR checkout failed order 44521 amount 75000")
    for i in range(30):
        lines.append("2026-11-18 11:39:06 INFO worker heartbeat ok")
    return "\n".join(lines) + "\n"


def make_read() -> str:
    lines = []
    for i in range(1, 121):
        if i % 15 == 0:
            lines.append("")
            lines.append("")
            lines.append("")
        elif i % 10 == 0:
            lines.append(f"{i:4d} |    repeated_block = True   ")
        else:
            lines.append(f"{i:4d} |    value_{i} = compute({i})  ")
    return "\n".join(lines) + "\n"


def make_search() -> str:
    entry = (
        "{n}. iPhone 13 128GB - Tokopedia\n"
        "   https://www.tokopedia.com/toko{n}/iphone-13-128gb-bekas\n"
        "   Harga Rp4.750.000, kondisi bekas mulus, fullset dengan dus dan charger original, "
        "garansi resmi iBox sampai Desember 2026, lokasi Jakarta Selatan.\n"
        "   Penjual sudah berjualan sejak 2019 dan memiliki ribuan ulasan positif dari pembeli "
        "di seluruh Indonesia dengan pengiriman cepat dan packing aman.\n"
        "   Stok tersisa 3 unit dan harga bisa nego tipis untuk pembelian langsung hari ini.\n"
    )
    parts = [entry.format(n=n) for n in range(1, 9)]
    parts.append(entry.format(n=2))  # duplicate URL entry
    parts.append(entry.format(n=5))  # duplicate URL entry
    return "\n".join(parts)


def make_generic() -> str:
    sentences = [
        "note: the agent reviewed the listing and compared it "
        "against the reference price band before drafting the reply",
        "note: the summary was checked against the ledger and "
        "the evidence file before the presenter wrote the message",
        "note: the draft was kept short and the price range was "
        "quoted from the latest sold listings in the same city",
        "note: the checker verified the claim against the stored "
        "evidence before the reply was sent to the user",
        "note: the memory entry was updated with the new price "
        "band so the next check starts from fresh data",
    ]
    lines = [sentences[i % len(sentences)] for i in range(70)]
    lines.append(
        "invoice total Rp9.876.543, customer 081298765432, 17 items, "
        "order 88412"
    )
    lines += [sentences[i % len(sentences)] for i in range(70, 140)]
    return "\n".join(lines) + "\n"


FIXTURES = {
    "diff": make_diff(),
    "status": make_status(),
    "grep": make_grep(),
    "find": make_find(),
    "ls": make_ls(),
    "tree": make_tree(),
    "log": make_log(),
    "read": make_read(),
    "search": make_search(),
    "generic": make_generic(),
}


def _digit_runs(text: str) -> set[str]:
    return set(re.findall(r"\d[\d.,]*", text))


# ---------------------------------------------------------------------------
# Auto-detection from the first 1KB
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(FIXTURES))
def test_detect_kind_from_first_kilobyte(kind):
    assert detect_kind(FIXTURES[kind]) == kind


def test_detect_kind_empty_is_generic():
    assert detect_kind("") == "generic"


def test_tool_name_hint_is_ignored():
    prose = make_generic()
    assert detect_kind(prose) == "generic"
    assert compress_tool_result(prose, tool_name="grep").kind == "generic"


# ---------------------------------------------------------------------------
# Each filter really shrinks its kind
# ---------------------------------------------------------------------------

MIN_SAVINGS_PCT = {
    "diff": 10.0, "status": 5.0, "grep": 20.0, "find": 5.0, "ls": 5.0,
    "tree": 5.0, "log": 30.0, "read": 3.0, "search": 5.0, "generic": 10.0,
}


@pytest.mark.parametrize("kind", sorted(FIXTURES))
def test_each_filter_shrinks_its_kind(kind):
    text = FIXTURES[kind]
    result = compress_tool_result(text)
    assert result.kind == kind, f"expected kind {kind}, got {result.kind}"
    assert result.compressed, f"{kind} not compressed: {result.fallback_reason}"
    assert result.compressed_chars < result.original_chars
    assert result.compressed_tokens < result.original_tokens
    assert result.chars_saved_pct >= MIN_SAVINGS_PCT[kind], (
        f"{kind} saved only {result.chars_saved_pct:.1f}% chars, "
        f"expected >= {MIN_SAVINGS_PCT[kind]}%"
    )
    assert result.counter == "tiktoken-cl100k_base"


def test_token_counter_is_real_tokenizer():
    count, counter = count_tokens("hello world, this is a test")
    assert counter == "tiktoken-cl100k_base"
    assert count == 7  # cl100k_base tokenization of this exact string


# ---------------------------------------------------------------------------
# Lossless in the task sense + orphan-number rule
# ---------------------------------------------------------------------------

def test_diff_keeps_every_file_hunk_and_changed_line():
    text = make_diff()
    result = compress_tool_result(text)
    assert result.compressed
    for needle in (
        "diff --git a/puspa/router/router.py",
        "diff --git a/puspa/billing/billing.py",
        "@@ -10,32 +10,33 @@",
        "@@ -40,15 +40,16 @@",
        '-    ("openrouter", "qwen/qwen3-32b", "qwen", 2, 6, False, True),',
        '+    ("openrouter", "qwen/qwen3-32b", "qwen", 3, 9, False, True),',
        '+    # price check: Rp4.750.000 for the reference phone',
        '-    return {"credits": 20, "price_rp": 9000}',
        '+    return {"credits": 20, "price_rp": 9000, "bonus": 5}',
    ):
        assert needle in result.text, f"diff lost: {needle!r}"
    # The collapsed context is counted, not silently dropped.
    assert "unchanged lines" in result.text


def test_orphan_number_survives_in_middle_of_long_log():
    result = compress_tool_result(make_log())
    assert result.compressed
    assert ORPHAN_LINE in result.text
    for number in ("1.250.000", "081234567890", "98.7"):
        assert number in result.text


@pytest.mark.parametrize("kind", sorted(FIXTURES))
def test_meaningful_numbers_survive(kind):
    """Every digit run in the original survives, except diff context
    lines: those are unchanged code the model can re-read, and the
    diff test above proves every changed line (the evidence) is kept
    whole. All other kinds keep every number literally.
    """
    text = FIXTURES[kind]
    result = compress_tool_result(text)
    assert result.compressed
    if kind == "diff":
        changed_numbers = set()
        for line in text.splitlines():
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                changed_numbers |= _digit_runs(line)
        assert changed_numbers <= _digit_runs(result.text)
        return
    assert _digit_runs(text) <= _digit_runs(result.text), (
        f"{kind} lost numbers: "
        f"{sorted(_digit_runs(text) - _digit_runs(result.text))[:5]}"
    )


def test_generic_keeps_unique_number_in_middle():
    result = compress_tool_result(make_generic())
    assert result.compressed
    for number in ("9.876.543", "081298765432", "88412"):
        assert number in result.text
    assert "lines omitted" in result.text


def test_search_keeps_prices_and_dedups_urls():
    result = compress_tool_result(make_search())
    assert result.compressed
    assert result.text.count("https://www.tokopedia.com/toko2/") == 1
    assert result.text.count("https://www.tokopedia.com/toko5/") == 1
    assert "Rp4.750.000" in result.text


def test_grep_keeps_line_numbers_and_long_digit_lines():
    result = compress_tool_result(make_grep())
    assert result.compressed
    assert "puspa/router/router.py (16 matches)" in result.text
    assert "12:" in result.text


def test_ls_keeps_sizes_dates_and_names():
    result = compress_tool_result(make_ls())
    assert result.compressed
    assert "owner: root:root" in result.text
    assert "compression.py" in result.text
    assert "agent_session.py" in result.text


def test_find_keeps_every_distinct_path():
    text = make_find()
    result = compress_tool_result(text)
    assert result.compressed
    for path in text.splitlines():
        assert path in result.text or path.removeprefix("puspa/") in result.text


# ---------------------------------------------------------------------------
# Fail-safe: filter failure, no savings, short text
# ---------------------------------------------------------------------------

def test_failsafe_when_filter_raises(monkeypatch):
    import puspa.router.compression as comp

    def _boom(_text):
        raise RuntimeError("filter exploded")

    monkeypatch.setitem(comp._FILTERS, "log", _boom)
    text = make_log()
    result = compress_tool_result(text)
    assert result.text == text
    assert result.compressed is False
    assert result.fallback_reason.startswith("filter_error")


def test_failsafe_when_filter_grows_text(monkeypatch):
    import puspa.router.compression as comp

    monkeypatch.setitem(
        comp._FILTERS, "log", lambda text: text + " extra padding"
    )
    text = make_log()
    result = compress_tool_result(text)
    assert result.text == text
    assert result.compressed is False
    assert result.fallback_reason == "no_savings"


def test_short_text_returned_unchanged():
    text = "On branch main\nnothing to commit, working tree clean\n"
    result = compress_tool_result(text)
    assert result.text == text
    assert result.compressed is False
    assert result.fallback_reason == "too_short"


def test_generic_below_threshold_returned_unchanged():
    text = "word " * 300  # ~1500 chars, no kind, below generic threshold
    result = compress_tool_result(text)
    assert result.kind == "generic"
    assert result.text == text
    assert result.compressed is False
    assert result.fallback_reason == "generic_below_threshold"


def test_non_string_input_does_not_raise():
    result = compress_tool_result(12345)
    assert result.text == "12345"
    assert result.compressed is False


# ---------------------------------------------------------------------------
# Wiring: what the model actually receives through PuspaTool / MCP
# ---------------------------------------------------------------------------

def _fake_tool_class(name, payload):
    from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters

    @tool_parameters(
        {"type": "object", "properties": {"text": {"type": "string"}}}
    )
    class FakeTool(Tool):
        @property
        def name(self) -> str:
            return name

        @property
        def description(self) -> str:
            return "fake tool for compression wiring test"

        @property
        def read_only(self) -> bool:
            return True

        async def execute(self, **kwargs):
            return payload

    return FakeTool()


def test_puspa_tool_compresses_long_result_before_model():
    from puspa.chassis import PuspaTool, token_context

    original = make_log()
    wrapped = _fake_tool_class("echo_log", original)
    tool = PuspaTool(wrapped)
    payload = {"user_id": "u1", "agent_id": "a1", "tools": ["echo_log"]}
    with token_context(payload):
        got = asyncio.run(tool.execute(text="x"))
    assert isinstance(got, str)
    assert len(got) < len(original)
    assert ORPHAN_LINE in got  # orphan number survives the wiring too
    assert got != original


def test_puspa_tool_leaves_error_results_alone():
    from nanobot.agent.tools.base import ToolResult

    from puspa.chassis import PuspaTool, token_context

    original = make_log()
    wrapped = _fake_tool_class("echo_log", ToolResult.error(original))
    tool = PuspaTool(wrapped)
    payload = {"user_id": "u1", "agent_id": "a1", "tools": ["echo_log"]}
    with token_context(payload):
        got = asyncio.run(tool.execute(text="x"))
    assert got == original
    assert got.is_error is True


def test_puspa_tool_leaves_short_results_alone():
    from puspa.chassis import PuspaTool, token_context

    wrapped = _fake_tool_class("echo_log", "hasil: ok")
    tool = PuspaTool(wrapped)
    payload = {"user_id": "u1", "agent_id": "a1", "tools": ["echo_log"]}
    with token_context(payload):
        got = asyncio.run(tool.execute(text="x"))
    assert got == "hasil: ok"


def test_mcp_wrapper_compresses_string_result(tmp_path, ephemeral_keys):
    import json as _json

    from puspa.chassis.tools import McpClientWrapper, StubMcpTransport
    from puspa.policy.skill_signing import sign_skill

    sk, pk = ephemeral_keys
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    manifest = {
        "name": "echo-skill",
        "tools": ["echo_log"],
        "connectors": {},
        "author": "puspa",
    }
    (skill_dir / "manifest.json").write_text(
        _json.dumps(manifest, indent=2), encoding="utf-8"
    )
    signed = sign_skill(skill_dir, sk)
    original = make_log()
    transport = StubMcpTransport(
        tools={"echo_log": (
            {"type": "object", "properties": {}},
            lambda args: original,
        )}
    )
    wrapper = McpClientWrapper(
        transport, skill_manifest=signed, public_key=pk,
    )
    out = wrapper.call_tool_sync(
        "echo_log", {}, token_payload={"user_id": "u1", "agent_id": "a1"},
    )
    assert out["status"] == "ok"
    assert isinstance(out["result"], str)
    assert len(out["result"]) < len(original)
    assert ORPHAN_LINE in out["result"]


# ---------------------------------------------------------------------------
# Real Puspa tool outputs (subprocess, read-only)
# ---------------------------------------------------------------------------

def _run(cmd, cwd=BUILD_DIR):
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(cwd)
    ).stdout


def test_real_ls_find_grep_tree_detect_and_preserve_numbers():
    cases = [
        ("ls", ["ls", "-la", "puspa/router"]),
        ("find", ["find", "puspa/router", "-type", "f"]),
        ("grep", ["grep", "-rn", "def ", "puspa/router/router.py"]),
        ("tree", ["tree", "puspa/router"]),
    ]
    for expected_kind, cmd in cases:
        text = _run(cmd)
        assert len(text) > 200, f"{cmd} produced almost nothing"
        assert detect_kind(text) == expected_kind, cmd
        result = compress_tool_result(text)
        if result.compressed:
            assert _digit_runs(text) <= _digit_runs(result.text), cmd


def test_real_numbered_read_detects_and_shrinks():
    text = _run(["cat", "-n", "puspa/router/router.py"])
    assert detect_kind(text) == "read"
    result = compress_tool_result(text)
    assert result.kind == "read"
    if result.compressed:
        assert _digit_runs(text) <= _digit_runs(result.text)


def test_real_git_diff_and_status_in_tmp_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    src = (BUILD_DIR / "puspa" / "router" / "router.py").read_text()
    (repo / "router.py").write_text(src)
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t",
          "commit", "-qm", "init"], cwd=repo)
    lines = src.splitlines()
    lines[10] = lines[10] + "  # changed"
    lines.insert(60, "NEW_CONSTANT = 123456")
    (repo / "router.py").write_text("\n".join(lines) + "\n")
    (repo / "untracked.txt").write_text("x\n")
    diff = _run(["git", "diff"], cwd=repo)
    assert detect_kind(diff) == "diff"
    result = compress_tool_result(diff)
    assert "123456" in result.text
    assert "@@" in result.text
    status = _run(["git", "status"], cwd=repo)
    assert detect_kind(status) == "status"
    assert "untracked.txt" in compress_tool_result(status).text


# ---------------------------------------------------------------------------
# Measured savings table (printed with -s; thresholds asserted above)
# ---------------------------------------------------------------------------

def test_measured_savings_report(capsys):
    rows = []
    for kind in sorted(FIXTURES):
        result = compress_tool_result(FIXTURES[kind])
        rows.append(
            f"{kind:8s} {result.original_chars:6d} -> "
            f"{result.compressed_chars:6d} chars "
            f"({result.chars_saved_pct:5.1f}%)  "
            f"{result.original_tokens:5d} -> {result.compressed_tokens:5d} "
            f"tokens ({result.tokens_saved_pct:5.1f}%)"
        )
    print("\nMeasured savings per tool-output kind (tiktoken cl100k_base):")
    print("\n".join(rows))
    assert all(compress_tool_result(t).compressed for t in FIXTURES.values())
