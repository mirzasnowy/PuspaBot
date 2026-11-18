"""Narrative memory size limits (G7, DuskBot pattern rewritten for Puspa).

DuskBot caps on-disk narrative memory at 200 lines / 24,000 bytes for
``MEMORY.md`` and 256 KB per memory file. Puspa has no on-disk narrative
memory file: the narrative layer lives in Postgres (``summaries`` +
``episodic`` rows; see ``puspa/memory/consolidate.py``). The same caps
therefore live here as pure functions and are enforced at the only
narrative writers:

- :func:`truncate_narrative` is applied by
  ``consolidate._extractive_summary`` before a summary is stored.
- The DREAM byte budget in ``puspa/memory/dream.py`` is the same
  family of cap (40,000 bytes per dream pass, not a file cap).

Constants are the DuskBot values verbatim because they are the
specification, not copied code.
"""

from __future__ import annotations

#: Maximum lines in one narrative memory document (MEMORY.md cap).
MEMORY_MAX_LINES = 200

#: Maximum bytes in one narrative memory document (MEMORY.md cap).
MEMORY_MAX_BYTES = 24_000

#: Maximum bytes in any single memory file.
MEMORY_FILE_MAX_BYTES = 256 * 1024

#: Maximum bytes of narrative text a single summary may hold.
SUMMARY_MAX_BYTES = MEMORY_MAX_BYTES


def byte_len(text: str) -> int:
    """UTF-8 byte length of ``text``."""
    return len(text.encode("utf-8"))


def check_narrative(
    text: str, *, max_lines: int = MEMORY_MAX_LINES, max_bytes: int = MEMORY_MAX_BYTES
) -> dict:
    """Check narrative text against the size caps.

    Returns ``{"ok": bool, "lines": int, "bytes": int,
    "violations": [str, ...]}``. ``violations`` is empty when the
    text fits both caps.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    lines = text.count("\n") + (1 if text else 0)
    size = byte_len(text)
    violations = []
    if lines > max_lines:
        violations.append(f"narrative has {lines} lines, cap is {max_lines}")
    if size > max_bytes:
        violations.append(f"narrative has {size} bytes, cap is {max_bytes}")
    return {"ok": not violations, "lines": lines, "bytes": size, "violations": violations}


def check_memory_file(text: str) -> dict:
    """Check one memory file against the 256 KB per-file cap."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    size = byte_len(text)
    violations = []
    if size > MEMORY_FILE_MAX_BYTES:
        violations.append(f"memory file has {size} bytes, cap is {MEMORY_FILE_MAX_BYTES}")
    return {"ok": not violations, "bytes": size, "violations": violations}


def truncate_narrative(
    text: str, *, max_lines: int = MEMORY_MAX_LINES, max_bytes: int = MEMORY_MAX_BYTES
) -> str:
    """Trim narrative text to the caps, keeping the newest lines.

    Lines are dropped from the top (oldest first) until both caps
    hold. A short header line records that trimming happened so a
    reader can tell the document was cut. Returns the trimmed text
    (unchanged when it already fits).
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if check_narrative(text, max_lines=max_lines, max_bytes=max_bytes)["ok"]:
        return text
    header = f"[trimmed to memory size limits: max {max_lines} lines / {max_bytes} bytes]"
    # Reserve one line for the header so the result still fits.
    lines = text.split("\n")
    while len(lines) > max_lines - 1:
        lines.pop(0)
    lines.insert(0, header)
    while byte_len("\n".join(lines)) > max_bytes and len(lines) > 1:
        lines.pop(1)
    return "\n".join(lines)
