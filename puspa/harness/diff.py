"""Unified diff generation and strict application (Aider pattern).

Pattern studied, then rewritten as Puspa's own code (see
``docs/brain-depth-triage.md``, Tier 1 Aider, and the REUSE fragment
for this work):

- Aider asks the model for edits as a unified diff (or an equivalent
  search/replace block) and applies the diff to the file, then lints
  and tests the result. The diff is the unit of change: a surgical
  edit, not a whole-file rewrite, so the change stays reviewable.

This module is the Puspa version of that mechanism, built on the
standard library ``difflib``:

- :func:`generate_diff` builds a unified diff from old and new text.
- :func:`apply_diff` applies a unified diff to a text. It validates
  EVERY hunk against the original before changing anything: a hunk
  whose context does not match is reported in
  :class:`DiffApplyError` and NOTHING is applied, not even the hunks
  that did match. A partial apply that silently drops a hunk is
  never produced.
- :func:`is_diff` detects whether a piece of generated text is a
  diff at all, so the harness loop can accept either a full program
  or a diff from the generator.

Only unified diffs are supported. Hunk headers carry the usual
``@@ -old_start,old_count +new_start,new_count @@`` form; the
``\\ No newline at end of file`` marker is accepted and ignored.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

__all__ = [
    "DiffApplyError",
    "DiffHunk",
    "ParsedDiff",
    "apply_diff",
    "generate_diff",
    "is_diff",
    "parse_diff",
]

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class DiffApplyError(Exception):
    """A diff could not be applied cleanly.

    Carries ``failed_hunks`` (1-based hunk numbers that did not
    match) and ``detail`` (one human-readable line per failed hunk).
    When this is raised, the original text is untouched: the caller
    still holds the old text and nothing was applied, not even
    partially.
    """

    def __init__(
        self, message: str, *, failed_hunks: list[int] | None = None, detail: str = ""
    ) -> None:
        super().__init__(message)
        self.failed_hunks = list(failed_hunks or [])
        self.detail = detail


@dataclass
class DiffHunk:
    """One hunk of a unified diff: old lines and new lines."""

    number: int
    old_start: int  # 1-based line number in the original
    old_count: int
    new_start: int
    new_count: int
    # Lines without their leading marker: context lines first appear
    # in both lists; "-" lines only in old_lines; "+" lines only in
    # new_lines. The two lists interleave in file order.
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)


@dataclass
class ParsedDiff:
    """A parsed unified diff: target file plus its hunks."""

    filename: str | None
    hunks: list[DiffHunk] = field(default_factory=list)


def generate_diff(old_text: str, new_text: str, *, filename: str = "file.py") -> str:
    """Build a unified diff from old text to new text.

    An empty diff (identical texts) is an empty string: there is
    nothing to apply.
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff_lines)


def is_diff(text: str) -> bool:
    """True when ``text`` looks like a unified diff.

    A diff starts with an optional ``diff --git`` line, then
    ``---`` / ``+++`` headers, then at least one ``@@`` hunk header.
    Plain program text never matches: a program cannot start with a
    hunk header.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    for line in lines:
        if line.startswith(("diff --git ", "--- ", "+++ ")):
            continue
        return bool(line.startswith("@@ "))
    return False


def parse_diff(diff_text: str) -> ParsedDiff:
    """Parse a unified diff into hunks.

    Raises :class:`DiffApplyError` when the diff is malformed
    (bad hunk header, hunk line counts that do not match the header,
    content outside a hunk). A diff with no hunks parses to an empty
    hunk list.
    """
    if not isinstance(diff_text, str):
        raise DiffApplyError("diff must be str")
    filename: str | None = None
    hunks: list[DiffHunk] = []
    current: DiffHunk | None = None
    for raw_line in diff_text.splitlines():
        line = raw_line
        if line.startswith("+++ "):
            name = line[4:].strip()
            name = name.removeprefix("b/")
            filename = name
            continue
        if line.startswith("--- "):
            continue
        if line.startswith("diff --git "):
            continue
        if line.startswith(("Index: ", "====")):
            continue
        match = _HUNK_HEADER.match(line)
        if match:
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) is not None else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) is not None else 1
            current = DiffHunk(
                number=len(hunks) + 1,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
            hunks.append(current)
            continue
        if line.startswith("\\ "):
            # "\ No newline at end of file": informational only.
            continue
        if current is None:
            if line.strip() == "":
                continue
            raise DiffApplyError(f"diff content outside any hunk: {line[:60]!r}")
        if line.startswith(" "):
            current.old_lines.append(line[1:])
            current.new_lines.append(line[1:])
        elif line.startswith("-"):
            current.old_lines.append(line[1:])
        elif line.startswith("+"):
            current.new_lines.append(line[1:])
        elif line == "":
            # A bare empty line inside a hunk is a context line for
            # an empty source line (difflib emits " " for it, but
            # some generators emit a bare line).
            current.old_lines.append("")
            current.new_lines.append("")
        else:
            raise DiffApplyError(f"unrecognized diff line in hunk {current.number}: {line[:60]!r}")
    for hunk in hunks:
        if len(hunk.old_lines) != hunk.old_count:
            raise DiffApplyError(
                f"hunk {hunk.number} declares {hunk.old_count} old lines "
                f"but carries {len(hunk.old_lines)}",
                failed_hunks=[hunk.number],
            )
        if len(hunk.new_lines) != hunk.new_count:
            raise DiffApplyError(
                f"hunk {hunk.number} declares {hunk.new_count} new lines "
                f"but carries {len(hunk.new_lines)}",
                failed_hunks=[hunk.number],
            )
    return ParsedDiff(filename=filename, hunks=hunks)


def _find_hunk(original: list[str], hunk: DiffHunk, start_at: int) -> int | None:
    """Find where a hunk's old lines match the original.

    Tries the declared position first (adjusted by ``start_at``, the
    line offset from already applied hunks), then searches the whole
    file for a unique match. Returns the 0-based start index, or
    None when there is no match or the match is ambiguous. An empty
    old block (pure insertion) matches at its declared position.
    """
    if not hunk.old_lines:
        return max(0, min(hunk.old_start - 1 + start_at, len(original)))
    expected = max(0, hunk.old_start - 1 + start_at)
    width = len(hunk.old_lines)
    if (
        expected + width <= len(original)
        and original[expected : expected + width] == hunk.old_lines
    ):
        return expected
    matches = [
        index
        for index in range(len(original) - width + 1)
        if original[index : index + width] == hunk.old_lines
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def apply_diff(original_text: str, diff_text: str) -> tuple[str, list[int]]:
    """Apply a unified diff to text, all hunks or none.

    Returns ``(new_text, applied_hunk_numbers)``. Every hunk is
    validated against the original BEFORE any change is made: if
    any hunk fails to match, :class:`DiffApplyError` is raised with
    the failed hunk numbers and the original text is returned
    unchanged by the caller (this function never returns a partial
    result). A diff with no hunks returns the original unchanged.
    """
    if not isinstance(original_text, str):
        raise DiffApplyError("original text must be str")
    parsed = parse_diff(diff_text)
    if not parsed.hunks:
        return original_text, []
    original = original_text.splitlines()
    # Phase 1: validate all hunks and record their positions.
    positions: list[tuple[DiffHunk, int]] = []
    offset = 0
    failed: list[int] = []
    details: list[str] = []
    for hunk in parsed.hunks:
        found = _find_hunk(original, hunk, offset)
        if found is None:
            failed.append(hunk.number)
            details.append(
                f"hunk {hunk.number} (old lines {hunk.old_start}-{hunk.old_start + max(hunk.old_count - 1, 0)}): "
                "context does not match the original text"
            )
            continue
        positions.append((hunk, found))
        offset += len(hunk.new_lines) - len(hunk.old_lines)
    if failed:
        raise DiffApplyError(
            f"diff did not apply: {len(failed)} of {len(parsed.hunks)} hunk(s) failed to match; nothing was applied",
            failed_hunks=failed,
            detail="\n".join(details),
        )
    # Phase 2: all hunks validated, apply from last to first so
    # earlier positions stay valid.
    result = list(original)
    for hunk, found in reversed(positions):
        result[found : found + len(hunk.old_lines)] = hunk.new_lines
    new_text = "\n".join(result)
    if original_text.endswith("\n"):
        # Preserve the original trailing newline; splitlines/join
        # drops it otherwise.
        new_text += "\n"
    return new_text, [hunk.number for hunk, _ in positions]
