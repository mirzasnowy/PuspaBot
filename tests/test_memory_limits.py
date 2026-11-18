"""Tests for narrative size limits (G7). Pure logic, no database."""

from puspa.memory import limits


def test_check_narrative_ok_within_caps():
    text = "\n".join(f"line {i}" for i in range(10))
    result = limits.check_narrative(text)
    assert result["ok"] is True
    assert result["lines"] == 10
    assert result["violations"] == []


def test_check_narrative_flags_too_many_lines():
    text = "\n".join(f"line {i}" for i in range(201))
    result = limits.check_narrative(text)
    assert result["ok"] is False
    assert "201 lines" in result["violations"][0]


def test_check_narrative_flags_too_many_bytes():
    text = "x" * (limits.MEMORY_MAX_BYTES + 1)
    result = limits.check_narrative(text)
    assert result["ok"] is False
    assert "bytes" in result["violations"][0]


def test_truncate_narrative_keeps_newest_lines():
    text = "\n".join(f"line {i}" for i in range(250))
    trimmed = limits.truncate_narrative(text)
    check = limits.check_narrative(trimmed)
    assert check["ok"] is True
    assert trimmed.startswith("[trimmed to memory size limits:")
    assert trimmed.endswith("line 249")
    assert "line 0\n" not in trimmed


def test_truncate_narrative_leaves_fitting_text_alone():
    text = "short note\nsecond line"
    assert limits.truncate_narrative(text) == text


def test_memory_file_cap_is_256k():
    assert limits.MEMORY_FILE_MAX_BYTES == 256 * 1024
    assert limits.check_memory_file("x" * 100)["ok"] is True
    assert limits.check_memory_file("x" * (256 * 1024 + 1))["ok"] is False
