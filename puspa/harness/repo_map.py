"""Repository map builder for the coding harness (Aider pattern).

Pattern studied, then rewritten as Puspa's own code (see
``docs/brain-depth-triage.md``, Tier 1 Aider, and the REUSE fragment
for this work):

- Aider sends the model a concise map of the whole repository: the
  most important classes and functions along with their types and
  call signatures, so the model can see how the code it edits relates
  to the rest of the codebase.
- Aider optimizes the map to fit a token budget (default 1k tokens):
  it selects the most important parts of the codebase that fit the
  active budget, ranked by how often each symbol is referenced.

This module is the Puspa version of that mechanism, written from
scratch on the standard library ``ast`` module:

- :func:`build_repo_map` walks a directory of Python files and
  extracts top-level classes, functions, and methods with their
  signatures (argument names, defaults, annotations, return
  annotation, base classes, decorators).
- :func:`render_repo_map` formats the map in the compact Aider-like
  layout (``path:`` header, one indented line per symbol) and stops
  at a token budget, reporting which files were dropped.
- Token counting is an estimate (characters / 4), the same rough
  estimator Aider-style maps use when no tokenizer is available.

No network calls are made from this module. Only ``.py`` files are
read; anything that fails to parse is recorded as skipped, never
raised, so one broken file cannot hide the rest of the map.
"""

from __future__ import annotations

import ast
import contextlib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "RepoMap",
    "RepoMapEntry",
    "build_repo_map",
    "estimate_tokens",
    "render_repo_map",
]

# Rough token estimate: about 4 characters per token.
CHARS_PER_TOKEN = 4

# Default token budget for a rendered map, matching Aider's 1k
# default for ``--map-tokens``.
DEFAULT_MAP_TOKENS = 1000


def estimate_tokens(text: str) -> int:
    """Rough token estimate for a piece of text (chars / 4)."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


@dataclass(frozen=True)
class RepoMapEntry:
    """One symbol found in one file: class, function, or method."""

    path: str
    kind: str  # "class", "function", or "method"
    name: str
    signature: str
    line: int

    def format(self) -> str:
        """One compact line for the rendered map."""
        if self.kind == "class":
            return f"class {self.name}{self.signature}:"
        return f"def {self.name}{self.signature}:"


@dataclass
class RepoMap:
    """A built map: entries grouped by file, plus build metadata."""

    root: str
    entries: list[RepoMapEntry] = field(default_factory=list)
    files_scanned: int = 0
    files_skipped: list[str] = field(default_factory=list)

    def files(self) -> list[str]:
        """File paths in the map, in first-seen order."""
        seen: list[str] = []
        for entry in self.entries:
            if entry.path not in seen:
                seen.append(entry.path)
        return seen

    def entries_for(self, path: str) -> list[RepoMapEntry]:
        """Entries defined in one file, in source order."""
        return [entry for entry in self.entries if entry.path == path]


def _format_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render a function signature: args with defaults and annotations."""
    parts: list[str] = []
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults):
        text = arg.arg
        if arg.annotation is not None:
            with contextlib.suppress(Exception):
                text += ": " + ast.unparse(arg.annotation)
        if default is not None:
            try:
                text += " = " + ast.unparse(default)
            except Exception:
                text += " = ..."
        parts.append(text)
    if args.vararg is not None:
        parts.append("*" + args.vararg.arg)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        text = arg.arg
        if arg.annotation is not None:
            with contextlib.suppress(Exception):
                text += ": " + ast.unparse(arg.annotation)
        if default is not None:
            try:
                text += " = " + ast.unparse(default)
            except Exception:
                text += " = ..."
        parts.append(text)
    if args.kwarg is not None:
        parts.append("**" + args.kwarg.arg)
    rendered = "(" + ", ".join(parts) + ")"
    if node.returns is not None:
        with contextlib.suppress(Exception):
            rendered += " -> " + ast.unparse(node.returns)
    return rendered


def _format_bases(node: ast.ClassDef) -> str:
    """Render a class base list, or an empty string when there is none."""
    if not node.bases:
        return ""
    bases: list[str] = []
    for base in node.bases:
        try:
            bases.append(ast.unparse(base))
        except Exception:
            bases.append("?")
    return "(" + ", ".join(bases) + ")"


def _read_and_parse(path: Path, rel_path: str) -> ast.Module | None:
    """Parse one file, or return None when it cannot be read/parsed."""
    try:
        source = path.read_text(encoding="utf-8")
        return ast.parse(source, filename=rel_path)
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None


def _entries_for_file(path: Path, rel_path: str) -> tuple[list[RepoMapEntry], bool]:
    """Extract top-level symbols from one Python file.

    Returns ``(entries, parsed)``; ``parsed`` is False when the file
    could not be read or parsed at all.
    """
    tree = _read_and_parse(path, rel_path)
    if tree is None:
        return [], False
    entries: list[RepoMapEntry] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            entries.append(
                RepoMapEntry(
                    path=rel_path,
                    kind="class",
                    name=node.name,
                    signature=_format_bases(node),
                    line=node.lineno,
                )
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    entries.append(
                        RepoMapEntry(
                            path=rel_path,
                            kind="method",
                            name=f"{node.name}.{child.name}",
                            signature=_format_args(child),
                            line=child.lineno,
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            entries.append(
                RepoMapEntry(
                    path=rel_path,
                    kind="function",
                    name=node.name,
                    signature=_format_args(node),
                    line=node.lineno,
                )
            )
    return entries, True


def build_repo_map(
    root: str | Path,
    *,
    max_files: int = 500,
) -> RepoMap:
    """Walk ``root`` and build a repo map of its Python files.

    Files are visited in sorted path order so the map is
    deterministic. Files that cannot be read or parsed are listed
    in ``files_skipped`` and contribute no entries. ``__pycache__``
    directories and hidden directories are skipped.
    """
    root_path = Path(root)
    repo_map = RepoMap(root=str(root_path))
    if not root_path.is_dir():
        return repo_map
    py_files = sorted(
        path
        for path in root_path.rglob("*.py")
        if "__pycache__" not in path.parts
        and not any(part.startswith(".") for part in path.relative_to(root_path).parts)
    )[:max_files]
    for path in py_files:
        rel_path = str(path.relative_to(root_path))
        repo_map.files_scanned += 1
        entries, parsed = _entries_for_file(path, rel_path)
        if not parsed:
            repo_map.files_skipped.append(rel_path)
            continue
        # A parseable file with no top-level symbols still counts as
        # scanned; it simply has nothing to show.
        repo_map.entries.extend(entries)
    return repo_map


def render_repo_map(repo_map: RepoMap, *, max_tokens: int = DEFAULT_MAP_TOKENS) -> tuple[str, dict]:
    """Render the map as compact text within a token budget.

    Returns ``(text, report)``. ``report`` carries the token estimate
    of the rendered text, whether the map was truncated, and which
    files were included or dropped for budget reasons. Files are
    emitted in map order; when the budget runs out, whole files are
    dropped (never half a file), so the rendered map never shows a
    file with only some of its symbols.
    """
    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    lines: list[str] = []
    included: list[str] = []
    dropped: list[str] = []
    used_tokens = 0
    truncated = False
    for path in repo_map.files():
        block_lines = [f"{path}:"]
        for entry in repo_map.entries_for(path):
            block_lines.append(f"  {entry.format()}")
        block = "\n".join(block_lines)
        block_tokens = estimate_tokens(block + "\n")
        if lines and used_tokens + block_tokens > max_tokens:
            dropped.append(path)
            truncated = True
            continue
        lines.append(block)
        included.append(path)
        used_tokens += block_tokens
    text = "\n".join(lines)
    if text:
        text += "\n"
    report = {
        "tokens": estimate_tokens(text),
        "max_tokens": max_tokens,
        "truncated": truncated,
        "files_included": included,
        "files_dropped": dropped,
    }
    return text, report
