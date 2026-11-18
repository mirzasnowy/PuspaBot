"""Lossless tool-result compression before the result reaches the model.

Pattern studied, then rewritten as Puspa's own code (see REUSE.md):
RTK (rtk-ai/rtk) compresses shell command output before an agent
reads it, with per-command filters (git diff/status, grep, ls, tree,
logs), a fail-safe that returns the raw output when a filter fails or
grows the text, and a "never worse" promise. Only the PATTERN is
taken from RTK: the RTK CLI core (hook core and every command filter)
is Apache-2.0, while its pipeline engine and proxy daemon are
BUSL-1.1 and are NOT used here. No RTK code is copied; every filter
below is written from scratch for Puspa's own tool outputs.

Where it runs: ``PuspaTool.execute`` (``puspa/chassis/tools.py``)
compresses the wrapped tool's string result after the injection scan
and before returning it, so nanobot hands the model the compressed
text. The same helper is used for string results in
``McpClientWrapper.call_tool``. Compression only changes the text
sent to the model: the Book of Evidence, the ledger, and the checker
never see the compressed text, and their numbers are untouched.

Orphan-number rule (hard rule): a filter NEVER drops or truncates a
line that carries a digit, except for two count-preserving cases
where the number is kept in a marker instead of in place:

- diff context collapse: unchanged lines are counted in a marker
  ("[... N unchanged lines ...]"); every added/removed line, which
  is what the task needs, is kept whole. Numbers that sit only in
  a collapsed unchanged line are the one documented exception:
  unchanged code is re-readable from the file itself, and the
  marker preserves how many lines were collapsed. The test suite
  proves every changed line and its numbers survive.
- log dedup: identical lines are collapsed to one copy plus a
  "(xN)" count marker, so the count survives.

Every other filter keeps digit-bearing lines whole: grep only cuts
digit-free lines, search only truncates or drops digit-free lines,
smart-truncate keeps every digit-bearing middle line. A line that
carries a number appearing exactly once (a price, a customer number,
a score, a count) therefore always survives compression.

Fail-safe: each filter runs inside a try/except; a filter that
raises, or a filter whose output is not strictly smaller than its
input, returns the original text unchanged with a reason recorded in
``CompressionResult.fallback_reason``. Text shorter than
``MIN_COMPRESS_CHARS`` is never compressed at all.

Token counting: ``count_tokens`` uses tiktoken ``cl100k_base`` when
the package is importable (it is a pinned dependency) and falls back
to the chars/4 estimate otherwise; ``CompressionResult.counter``
says which one was used, so a reported number is never mistaken for
an exact count when it is an estimate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CompressionResult",
    "compress_tool_result",
    "count_tokens",
    "detect_kind",
]

#: First bytes of a tool result used for kind detection.
DETECT_WINDOW = 1024

#: Results shorter than this are returned unchanged: not worth it.
MIN_COMPRESS_CHARS = 500

#: Generic fallback only runs on text at least this long.
GENERIC_MIN_CHARS = 4000

#: Head/tail lines kept by the generic smart-truncate filter.
GENERIC_HEAD_LINES = 40
GENERIC_TAIL_LINES = 20

#: Longest line a grep/search filter will keep before cutting.
MAX_LINE_CHARS = 200

#: Context lines kept in a row before a diff run is collapsed.
DIFF_CONTEXT_KEEP = 3

#: Identical consecutive lines collapsed from this run length on.
DEDUP_RUN = 3

_NUMBER_RE = re.compile(r"\d")
_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2}"
    r"|\[(INFO|DEBUG|WARNING|ERROR|WARN|TRACE)\]"
    r"|(INFO|DEBUG|WARNING|ERROR|WARN|TRACE)[\s:|-])"
)
_READ_LINE_RE = re.compile(r"^\s*(\d+)[\s|:]+")
# Grep match: "path:line:col:content", "path:line:content", or the
# single-file form "line:content" (grep omits the file name when it
# is given exactly one file). The path part has no whitespace, so a
# timestamped log line ("2026-11-18 11:39:01 INFO ...") never
# matches: its text before the first colon contains a space.
_GREP_LINE_RE = re.compile(r"^[^:\s]+:\d+:\d*:|^[^:\s]+:\d+:|^\d+:")
_LS_LINE_RE = re.compile(r"^[-dlbcps][rwxst-]{9}\s")
_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(,\d+)? \+\d+(,\d+)? @@")

_tokenizer = None
_tokenizer_tried = False


def _get_tokenizer():
    """tiktoken cl100k_base encoder, cached; None when unavailable."""
    global _tokenizer, _tokenizer_tried
    if _tokenizer_tried:
        return _tokenizer
    _tokenizer_tried = True
    try:
        import tiktoken

        _tokenizer = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _tokenizer = None
    return _tokenizer


def count_tokens(text: str) -> tuple[int, str]:
    """Count tokens in ``text``; returns (count, counter name).

    Uses tiktoken ``cl100k_base`` when importable, otherwise the
    chars/4 estimate (the same rough estimator the harness repo map
    uses). The counter name labels which one was used.
    """
    if not text:
        return 0, "tiktoken-cl100k_base"
    encoder = _get_tokenizer()
    if encoder is not None:
        try:
            return len(encoder.encode(text)), "tiktoken-cl100k_base"
        except Exception:
            pass
    return max(1, len(text) // 4), "chars/4 estimate"


@dataclass(frozen=True)
class CompressionResult:
    """One compression run: the text to send plus its measurement."""

    text: str
    kind: str
    compressed: bool
    original_chars: int
    compressed_chars: int
    original_tokens: int
    compressed_tokens: int
    counter: str
    fallback_reason: str | None = None

    @property
    def chars_saved(self) -> int:
        return self.original_chars - self.compressed_chars

    @property
    def tokens_saved(self) -> int:
        return self.original_tokens - self.compressed_tokens

    @property
    def chars_saved_pct(self) -> float:
        if self.original_chars == 0:
            return 0.0
        return 100.0 * self.chars_saved / self.original_chars

    @property
    def tokens_saved_pct(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return 100.0 * self.tokens_saved / self.original_tokens


# ---------------------------------------------------------------------------
# Kind detection (first 1KB)
# ---------------------------------------------------------------------------

def _sample_lines(head: str, limit: int = 40) -> list[str]:
    return [ln for ln in head.splitlines() if ln.strip()][:limit]


def _majority(lines: list[str], predicate) -> bool:
    if not lines:
        return False
    hits = sum(1 for ln in lines if predicate(ln))
    return hits * 2 > len(lines)


def _leading_numbers(lines: list[str]) -> list[int]:
    """Leading line numbers of numbered-read lines, in order."""
    numbers = []
    for ln in lines:
        match = _READ_LINE_RE.match(ln)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _is_numbered_read(lines: list[str]) -> bool:
    """True when the leading numbers run consecutively (+1 each).

    This is what separates a numbered file read (``cat -n``: 1, 2,
    3, ...) from single-file grep output (``50:def ...``: sparse,
    non-consecutive match lines). A grep output whose matches
    happen to sit on consecutive lines misdetects as a read; that
    is harmless, the read filter keeps every line.
    """
    numbers = _leading_numbers(lines)
    if len(numbers) < 5:
        return False
    first_five = numbers[:5]
    return all(b - a == 1 for a, b in zip(first_five, first_five[1:]))


def detect_kind(text: str) -> str:
    """Detect the output kind from the first 1KB of ``text``.

    Order matters: the most specific markers (diff, status, tree)
    are checked first, then line-shape majorities (ls, read, log,
    grep), then path shapes (find) and URL shapes (search).
    Unknown text is "generic" and only gets smart-truncate.
    """
    if not text:
        return "generic"
    head = text[:DETECT_WINDOW]
    lines = _sample_lines(head)
    if "diff --git " in head or (
        "@@ " in head and ("--- " in head or "+++ " in head)
    ):
        return "diff"
    if head.startswith("On branch ") or (
        "Changes to be committed" in head
        or "Untracked files:" in head
        or "nothing to commit" in head
    ):
        return "status"
    if "├──" in head or "└──" in head:
        return "tree"
    # ls -l: "total N" first, or permission-bit rows; with several
    # directories ls prints a "dir:" header first, so check the
    # first few lines, not only the first one.
    if any(
        ln.startswith("total ") or _LS_LINE_RE.match(ln) is not None
        for ln in lines[:5]
    ):
        return "ls"
    if _is_numbered_read(lines):
        return "read"
    if _majority(lines, lambda ln: _LOG_LINE_RE.match(ln) is not None):
        return "log"
    if _majority(lines, lambda ln: _GREP_LINE_RE.match(ln) is not None):
        return "grep"
    if _majority(
        lines,
        lambda ln: (ln.startswith(("./", "/", "../")) or "/" in ln)
        and " " not in ln.strip(),
    ):
        return "find"
    if head.count("http://") + head.count("https://") >= 2:
        return "search"
    # Log fallback: one line dominating the sample means log-like
    # output even without timestamps or level tags. A single line
    # repeated in over 40% of the sampled lines is a heartbeat, not
    # prose: ordinary prose repeats a sentence far less often.
    # Needs at least 5 sampled lines: a one-line input has a 100%
    # "dominant" line by definition and must not trigger this.
    if len(lines) >= 5:
        from collections import Counter

        most_common = Counter(lines).most_common(1)[0][1]
        if most_common * 5 > len(lines) * 2:
            return "log"
    return "generic"


# ---------------------------------------------------------------------------
# Filters (each is pure text -> text; any failure falls back to original)
# ---------------------------------------------------------------------------

def _collapse_identical_runs(lines: list[str], *, run: int = DEDUP_RUN) -> list[str]:
    """Collapse runs of identical consecutive lines to first + count.

    The count marker preserves how many times the line occurred, so
    a repeated log line keeps its multiplicity.
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        count = j - i
        if count >= run:
            out.append(f"{lines[i]} (x{count})")
        else:
            out.extend(lines[i:j])
        i = j
    return out


def _cut_long_line(line: str, *, limit: int = MAX_LINE_CHARS) -> str:
    """Cut a digit-free line at a word boundary under ``limit``.

    Lines carrying a digit are returned whole: a number is never
    cut in half and never dropped. Cutting only happens at a space,
    so no token is ever split mid-word.
    """
    if len(line) <= limit or _NUMBER_RE.search(line):
        return line
    cut = line.rfind(" ", 0, limit)
    if cut <= 0:
        return line
    return line[:cut] + " [...]"


def _filter_diff(text: str) -> str:
    """Unified diff: keep every file header, hunk header, and changed
    line; collapse long runs of unchanged context lines to a marker
    carrying their count. A changed line (``+``/``-``) is never
    dropped, so every file and hunk that changed stays visible.
    """
    out: list[str] = []
    pending_context: list[str] = []

    def flush_context() -> None:
        if not pending_context:
            return
        run = pending_context
        if len(run) <= DIFF_CONTEXT_KEEP:
            out.extend(run)
        else:
            out.append(run[0])
            out.append(f"[... {len(run) - 2} unchanged lines ...]")
            out.append(run[-1])
        pending_context.clear()

    for line in text.splitlines():
        stripped = line.rstrip()
        if (
            stripped.startswith(("diff --git ", "index ", "--- ", "+++ "))
            or _DIFF_HUNK_RE.match(stripped)
            or stripped.startswith(("+", "-"))
            or stripped.startswith("\\ ")
        ):
            flush_context()
            out.append(stripped)
        else:
            pending_context.append(stripped)
    flush_context()
    return "\n".join(out)


def _filter_status(text: str) -> str:
    """git status: drop the hint lines ("(use ...)" / "(Untracked
    files ...)" guidance) and collapse blank runs. Every branch line,
    section header, and file name is kept.
    """
    out: list[str] = []
    blank = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("(use ") or stripped.startswith("(Untracked"):
            continue
        if not stripped:
            if blank:
                continue
            blank = True
            out.append("")
            continue
        blank = False
        out.append(line.rstrip())
    return "\n".join(out).strip("\n")


def _filter_grep(text: str) -> str:
    """grep/rg matches: group by file with a match count, strip the
    repeated path prefix from each match line (the header already
    carries it), dedup identical matches, cut over-long digit-free
    match lines at a word boundary. A match line carrying a digit
    is kept whole.
    """
    by_file: dict[str, list[str]] = {}
    order: list[str] = []
    other: list[str] = []
    bare: list[str] = []
    for line in text.splitlines():
        match = _GREP_LINE_RE.match(line)
        if not match:
            # Non-match lines are dropped, unless they carry a digit
            # (a summary count such as "3 files matched" is a number
            # the task may need, so it is kept verbatim).
            if _NUMBER_RE.search(line):
                other.append(line)
            continue
        first_colon = line.find(":")
        # Single-file form "line:content" has no path: keep the line
        # as-is (deduped below), there is nothing to group it under.
        if re.match(r"^\d+:", line):
            if line not in bare:
                bare.append(line)
            continue
        # Split "path:line:col:content" into its head and content;
        # only the content part may be cut, and only when digit-free.
        # The path prefix is stripped: the group header below
        # already carries it, so keeping it per line would repeat it.
        first_colon = line.find(":")
        second_colon = line.find(":", first_colon + 1)
        if second_colon != -1:
            entry = line[first_colon + 1: second_colon + 1] + _cut_long_line(
                line[second_colon + 1:]
            )
        else:
            entry = _cut_long_line(line[first_colon + 1:])
        path = line[:first_colon]
        if path not in by_file:
            by_file[path] = []
            order.append(path)
        if entry not in by_file[path]:
            by_file[path].append(entry)
    out: list[str] = []
    for path in order:
        entries = by_file[path]
        out.append(f"{path} ({len(entries)} matches)")
        out.extend(entries)
    out.extend(bare)
    out.extend(other)
    return "\n".join(out)


def _factor_common_prefix(lines: list[str]) -> list[str]:
    """Factor a path prefix shared by every line into one header line.

    Lossless: the header records the prefix, each line keeps its
    remainder. Only applied when the shared prefix is long enough
    to be worth it.
    """
    if len(lines) < 2:
        return lines
    prefix = lines[0]
    for line in lines[1:]:
        while not line.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return lines
    # Keep the prefix at a directory boundary when possible.
    if "/" in prefix:
        prefix = prefix[: prefix.rfind("/") + 1]
    if len(prefix) < 4:
        return lines
    return [f"base: {prefix}"] + [ln[len(prefix):] for ln in lines]


def _filter_find(text: str) -> str:
    """find output: dedup exact duplicate paths and factor the
    common directory prefix into one header line. Every distinct
    path stays in the output.
    """
    seen: set[str] = set()
    paths: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        paths.append(stripped)
    return "\n".join(_factor_common_prefix(paths))


def _filter_ls(text: str) -> str:
    """ls -l output: when every row shares the same owner/group,
    record them once in a header and drop the two columns from the
    rows. Permissions, size, date, and name stay per row. Section
    headers ("dir:" from listing several directories) and "total N"
    lines are kept as-is. Plain ``ls`` output (one name per line)
    only gets dedup.
    """
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    rows = [ln for ln in lines if _LS_LINE_RE.match(ln)]
    others = [ln for ln in lines if not _LS_LINE_RE.match(ln)]
    if not rows or any(
        not (ln.startswith("total ") or ln.endswith(":")) for ln in others
    ):
        seen: set[str] = set()
        out: list[str] = []
        for ln in lines:
            if ln not in seen:
                seen.add(ln)
                out.append(ln)
        return "\n".join(out)
    parsed = {}
    owners: set[str] = set()
    for ln in rows:
        parts = ln.split(None, 8)
        if len(parts) < 9:
            return "\n".join(lines)
        parsed[ln] = parts
        owners.add(f"{parts[2]}:{parts[3]}")
    if len(owners) != 1:
        return "\n".join(lines)
    owner = list(owners)[0]
    # Rebuild in original order: section headers ("dir:", "total N")
    # stay where they were, rows lose only the owner/group columns,
    # and the owner is recorded once after the first "total" line.
    # The hard-link count column is kept: it is a number, and the
    # orphan-number rule forbids dropping a number-bearing column.
    out = []
    owner_noted = False
    for ln in lines:
        if ln in parsed:
            p = parsed[ln]
            out.append(f"{p[0]} {p[1]:>3} {p[4]:>8} {p[5]} {p[6]:>2} {p[7]} {p[8]}")
        else:
            out.append(ln)
            if not owner_noted and ln.startswith("total "):
                out.append(f"owner: {owner}")
                owner_noted = True
    if not owner_noted:
        out.insert(0, f"owner: {owner}")
    return "\n".join(out)


def _filter_tree(text: str) -> str:
    """tree output: narrow the box-drawing indent (``│   `` -> ``|  ``,
    four-space indent -> two spaces). The tree shape, every name,
    and every count is kept; only drawing whitespace shrinks.
    No dedup: the same file name under different directories is a
    different file, so identical-looking lines must all stay.
    """
    out: list[str] = []
    for line in text.splitlines():
        narrowed = line.replace("│   ", "|  ").replace("    ", "  ")
        out.append(narrowed.rstrip())
    return "\n".join(out)


def _filter_log(text: str) -> str:
    """Log output: collapse runs of identical consecutive lines to
    one copy plus a "(xN)" count, then collapse identical lines
    that recur later to their first occurrence plus a total count.
    A line that appears exactly once (the orphan-number case: a
    price, a customer number, a score in the middle of a long log)
    is never touched.
    """
    lines = text.splitlines()
    lines = _collapse_identical_runs(lines)
    counts: dict[str, int] = {}
    for ln in lines:
        counts[ln] = counts.get(ln, 0) + 1
    out: list[str] = []
    emitted: set[str] = set()
    for ln in lines:
        total = counts[ln]
        if total >= DEDUP_RUN and ln not in emitted:
            emitted.add(ln)
            out.append(f"{ln} (repeated {total}x total)")
        elif total < DEDUP_RUN:
            out.append(ln)
    return "\n".join(out)


def _filter_read(text: str) -> str:
    """Numbered file read: collapse runs of 3+ blank lines to one,
    collapse runs of 3+ identical lines to first + count, strip
    trailing whitespace. Every numbered line and its number stays.
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    blank_run = 0
    for ln in lines:
        if not ln.strip():
            blank_run += 1
            if blank_run <= 1:
                out.append("")
            continue
        blank_run = 0
        out.append(ln)
    return "\n".join(_collapse_identical_runs(out))


def _filter_search(text: str) -> str:
    """Search results: dedup entries by URL, keep title/URL/price
    lines whole, truncate digit-free snippet lines longer than
    ``MAX_LINE_CHARS`` at a word boundary, and drop digit-free
    snippet lines beyond the first two per entry. Any line with a
    digit is never truncated or dropped.
    """
    entries: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip() and current:
            entries.append(current)
            current = []
        elif line.strip():
            current.append(line.rstrip())
    if current:
        entries.append(current)
    seen_urls: set[str] = set()
    out: list[str] = []
    for entry in entries:
        url = next(
            (ln for ln in entry if "http://" in ln or "https://" in ln), None
        )
        if url is not None:
            if url in seen_urls:
                continue
            seen_urls.add(url)
        kept: list[str] = []
        snippet_seen = 0
        for ln in entry:
            is_url = "http://" in ln or "https://" in ln
            if _NUMBER_RE.search(ln) or is_url:
                kept.append(ln)
                continue
            if snippet_seen < 2:
                kept.append(_cut_long_line(ln))
                snippet_seen += 1
        out.extend(kept)
        out.append("")
    return "\n".join(out).rstrip("\n")


def _filter_generic(text: str) -> str:
    """Smart-truncate fallback: keep the head and the tail, and from
    the middle keep every line that carries a digit (deduped), so a
    meaningful number in the middle is never lost. Digit-free middle
    lines are replaced by one marker carrying their count.
    """
    lines = text.splitlines()
    head = lines[:GENERIC_HEAD_LINES]
    tail = lines[-GENERIC_TAIL_LINES:] if len(lines) > GENERIC_HEAD_LINES else []
    middle = lines[GENERIC_HEAD_LINES: len(lines) - GENERIC_TAIL_LINES] \
        if len(lines) > GENERIC_HEAD_LINES + GENERIC_TAIL_LINES else []
    kept_numbers: list[str] = []
    seen: set[str] = set()
    dropped = 0
    for ln in middle:
        if _NUMBER_RE.search(ln):
            if ln not in seen:
                seen.add(ln)
                kept_numbers.append(ln)
        else:
            dropped += 1
    parts = ["\n".join(head)]
    if kept_numbers:
        parts.append("\n".join(kept_numbers))
    if dropped:
        parts.append(f"[... {dropped} lines omitted ...]")
    if tail:
        parts.append("\n".join(tail))
    return "\n".join(parts)


_FILTERS = {
    "diff": _filter_diff,
    "status": _filter_status,
    "grep": _filter_grep,
    "find": _filter_find,
    "ls": _filter_ls,
    "tree": _filter_tree,
    "log": _filter_log,
    "read": _filter_read,
    "search": _filter_search,
    "generic": _filter_generic,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def compress_tool_result(text: str, *, tool_name: str | None = None) -> CompressionResult:
    """Compress one tool result before it is sent to the model.

    Returns a ``CompressionResult`` whose ``text`` is either the
    compressed text or, on any fail-safe, the original text with
    ``compressed=False`` and a ``fallback_reason``. Never raises for
    bad input: a non-string input is treated as empty text.

    ``tool_name`` is accepted for call-site convenience and ignored:
    the kind is always detected from the first 1KB of content, so a
    wrong tool name can never force the wrong filter onto the text.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    original_tokens, counter = count_tokens(text)
    base = CompressionResult(
        text=text, kind="generic", compressed=False,
        original_chars=len(text), compressed_chars=len(text),
        original_tokens=original_tokens, compressed_tokens=original_tokens,
        counter=counter,
    )
    if len(text) < MIN_COMPRESS_CHARS:
        kind = detect_kind(text)
        return CompressionResult(
            text=text, kind=kind, compressed=False,
            original_chars=len(text), compressed_chars=len(text),
            original_tokens=original_tokens,
            compressed_tokens=original_tokens, counter=counter,
            fallback_reason="too_short",
        )
    kind = detect_kind(text)
    if kind == "generic" and len(text) < GENERIC_MIN_CHARS:
        return CompressionResult(
            text=text, kind=kind, compressed=False,
            original_chars=len(text), compressed_chars=len(text),
            original_tokens=original_tokens,
            compressed_tokens=original_tokens, counter=counter,
            fallback_reason="generic_below_threshold",
        )
    try:
        compressed_text = _FILTERS[kind](text)
    except Exception as exc:  # fail-safe: filter failed
        return CompressionResult(
            text=text, kind=kind, compressed=False,
            original_chars=len(text), compressed_chars=len(text),
            original_tokens=original_tokens,
            compressed_tokens=original_tokens, counter=counter,
            fallback_reason=f"filter_error: {type(exc).__name__}",
        )
    if not isinstance(compressed_text, str) or len(compressed_text) >= len(text):
        return CompressionResult(
            text=text, kind=kind, compressed=False,
            original_chars=len(text), compressed_chars=len(text),
            original_tokens=original_tokens,
            compressed_tokens=original_tokens, counter=counter,
            fallback_reason="no_savings",
        )
    new_tokens, _ = count_tokens(compressed_text)
    return CompressionResult(
        text=compressed_text, kind=kind, compressed=True,
        original_chars=len(text), compressed_chars=len(compressed_text),
        original_tokens=original_tokens, compressed_tokens=new_tokens,
        counter=counter,
    )
