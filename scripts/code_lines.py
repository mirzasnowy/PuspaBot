"""Code-line counting for the size ratchet (``scripts/code_size.py``).

One function per file kind. A code line is a line holding code: blank
lines and comment / docstring lines do not count. The counters are
deliberately approximate where exactness would need a parser (the
JS/TS scanner notes its one known shortcut in its docstring); a
ratchet that miscounts by a line is still a ratchet, and the budgets
were measured with these same counters, so the comparison is
like-for-like.

Standard library only.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

#: Token types that make a Python line a code line. Comments, blank
#: lines and docstrings never produce one of these on their own line.
_CODE_TOKEN_TYPES = frozenset({tokenize.NAME, tokenize.NUMBER, tokenize.STRING, tokenize.OP})


def _docstring_lines(source: str) -> set[int]:
    """Line numbers belonging only to a module/class/function docstring."""
    lines: set[int] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            end = first.end_lineno or first.lineno
            lines.update(range(first.lineno, end + 1))
    return lines


def _count_python(data: bytes) -> int:
    """Code lines of a Python file: lines holding a real token.

    A docstring line counts only if it also holds other code (``def f():
    \"\"\"doc\"\"\"``). A multi-line string that is not a docstring is a
    value, so its lines count. Files tokenize cannot read fall back to
    the comment-line rule below.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(data).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return _count_hash_comment(text)
    doc_lines = _docstring_lines(text)
    code: set[int] = set()
    for tok in tokens:
        if tok.type not in _CODE_TOKEN_TYPES:
            continue
        span = set(range(tok.start[0], tok.end[0] + 1))
        if tok.type == tokenize.STRING and span <= doc_lines:
            continue
        code.update(line for line in span if line not in doc_lines)
    return len(code)


def _count_hash_comment(text: str) -> int:
    """Code lines of a ``#``-comment file (YAML, or Python fallback)."""
    return sum(1 for line in text.splitlines() if (s := line.strip()) and not s.startswith("#"))


def _count_js_like(text: str) -> int:
    """Code lines of a JS/TS file: lines holding code outside comments and strings.

    A small scanner, not a parser: it tracks line comments, block
    comments, single- and double-quoted strings, and template literals
    (with ``${...}`` expressions, where the code inside counts). A
    ``//`` inside a regex literal reads as a comment start; that only
    ever swallows the rest of its own line, which already holds code.
    """
    count = 0
    i = 0
    length = len(text)
    line_has_code = False
    # Stack of states: "code", "line_comment", "block_comment", "sq",
    # "dq", "tpl", or ("tpl_expr", brace depth).
    state: list[object] = ["code"]
    while i < length:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < length else ""
        top = state[-1]
        if ch == "\n":
            if line_has_code:
                count += 1
            line_has_code = False
            if top == "line_comment":
                state.pop()
            i += 1
            continue
        if top == "line_comment":
            i += 1
            continue
        if top == "block_comment":
            if ch == "*" and nxt == "/":
                state.pop()
                i += 2
            else:
                i += 1
            continue
        if top == "sq":
            i += 2 if ch == "\\" else 1
            if ch == "'":
                state.pop()
            continue
        if top == "dq":
            i += 2 if ch == "\\" else 1
            if ch == '"':
                state.pop()
            continue
        if top == "tpl":
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                state.pop()
                i += 1
                continue
            if ch == "$" and nxt == "{":
                state.append(("tpl_expr", 0))
                line_has_code = True
                i += 2
                continue
            i += 1
            continue
        if isinstance(top, tuple):
            depth = top[1]
            if ch == "{":
                state[-1] = ("tpl_expr", depth + 1)
                line_has_code = True
                i += 1
                continue
            if ch == "}":
                if depth == 0:
                    state.pop()
                else:
                    state[-1] = ("tpl_expr", depth - 1)
                line_has_code = True
                i += 1
                continue
            if ch in ("'", '"', "`"):
                state.append({"'": "sq", '"': "dq", "`": "tpl"}[ch])
                line_has_code = True
                i += 1
                continue
            if ch == "/" and nxt in ("/", "*"):
                state.append("line_comment" if nxt == "/" else "block_comment")
                i += 2
                continue
            if not ch.isspace():
                line_has_code = True
            i += 1
            continue
        if ch == "/" and nxt in ("/", "*"):
            state.append("line_comment" if nxt == "/" else "block_comment")
            i += 2
            continue
        if ch in ("'", '"', "`"):
            state.append({"'": "sq", '"': "dq", "`": "tpl"}[ch])
            i += 1
            continue
        if not ch.isspace():
            line_has_code = True
        i += 1
    if line_has_code:
        count += 1
    return count


def _count_sql(text: str) -> int:
    """Code lines of a SQL file: blank, ``--`` and ``/* */`` lines out."""
    count = 0
    in_block = False
    for line in text.splitlines():
        s = line.strip()
        if in_block:
            end = s.find("*/")
            if end == -1:
                continue
            in_block = False
            s = s[end + 2 :].strip()
            if not s or s.startswith("--"):
                continue
            count += 1
            continue
        if not s or s.startswith("--"):
            continue
        start = s.find("/*")
        if start != -1:
            before = s[:start].strip()
            if s.find("*/", start + 2) == -1:
                in_block = True
                if before:
                    count += 1
                continue
        count += 1
    return count


def _count_nonblank(text: str) -> int:
    """Code lines of a file with no comments (JSON): every blank-free line."""
    return sum(1 for line in text.splitlines() if line.strip())


def _count_markdown(text: str) -> int:
    """Code lines of a Markdown file: non-blank lines outside HTML comments."""
    count = 0
    in_comment = False
    for line in text.splitlines():
        s = line
        while True:
            if in_comment:
                end = s.find("-->")
                if end == -1:
                    s = ""
                    break
                in_comment = False
                s = s[end + 3 :]
            else:
                start = s.find("<!--")
                if start == -1:
                    break
                end = s.find("-->", start + 4)
                if end == -1:
                    s = s[:start]
                    in_comment = True
                    break
                s = s[:start] + s[end + 3 :]
        if s.strip():
            count += 1
    return count


def count_code_lines(path: Path) -> int:
    """Code lines of one file, by its extension."""
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _count_python(data)
    text = data.decode("utf-8", errors="replace")
    if suffix in {".ts", ".tsx", ".js"}:
        return _count_js_like(text)
    if suffix in {".yaml", ".yml"}:
        return _count_hash_comment(text)
    if suffix == ".sql":
        return _count_sql(text)
    if suffix == ".json":
        return _count_nonblank(text)
    if suffix == ".md":
        return _count_markdown(text)
    return _count_nonblank(text)
