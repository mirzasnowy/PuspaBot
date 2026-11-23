"""Postgres SQL to SQLite SQL translation (D-SQLite-1).

The desktop backend runs on SQLite while the hosted backend keeps
Postgres, and the application SQL is written once, in the Postgres
dialect. This module translates one statement at a time at execute
time, so the call sites do not need a SQLite dialect of their own:

- ``%s`` placeholders become ``?`` (literal-aware: ``%s`` inside a
  string literal, a comment, or a dollar-quoted block is left alone,
  and ``%%`` becomes a literal ``%``).
- ``::jsonb`` / ``::uuid`` / ``::text`` / ... casts are dropped:
  SQLite is dynamically typed and the column converters in
  :mod:`puspa.db` restore the Postgres value shapes.
- ``now()`` and ``CURRENT_TIMESTAMP`` become a parenthesised
  ``strftime`` expression that yields the canonical timestamp
  (D-SQLite-3: ISO-8601 UTC text, the same format Python writes).
  ``now() +/- interval 'N unit'`` becomes the same expression with
  a ``strftime`` modifier.
- ``gen_random_uuid()`` becomes a SQLite expression that builds a
  version-4 UUID string.
- ``ILIKE`` becomes ``LIKE`` (SQLite ``LIKE`` is already
  case-insensitive for ASCII); ``IS [NOT] DISTINCT FROM`` becomes
  ``IS [NOT]``.
- ``= ANY(%s)`` becomes ``IN (?, ?, ...)`` with the list spliced
  into the parameters; an empty list becomes ``IN (NULL)``.
- ``FOR UPDATE`` / ``FOR UPDATE SKIP LOCKED`` / ``FOR UPDATE NOWAIT``
  are dropped: the caller takes ``BEGIN IMMEDIATE`` instead
  (D-SQLite-7) wherever the select-then-write must stay atomic.
- Postgres advisory locks (``pg_advisory_*``) become ``1`` and
  their parameters are dropped: the caller takes ``BEGIN IMMEDIATE``
  instead, which serialises writers for the single-user desktop.
- ``TRUNCATE t1, t2`` becomes one ``DELETE FROM`` per table.
- ``SET ...``, ``DO $$ ... $$``, role / policy / grant / extension
  statements become a no-op: there is no role system in SQLite
  (D-SQLite-5: ownership is enforced in code, not in the database).
- ``array_agg(x)`` becomes ``group_concat(x)``.
- ``CREATE INDEX ... USING btree`` loses the ``USING`` clause;
  ``USING hnsw`` / ``USING gin`` / ``USING gist`` indexes are
  dropped (no equivalent: vector search is brute-force Python,
  D-SQLite-4); ``CREATE INDEX CONCURRENTLY`` loses ``CONCURRENTLY``.

Statements the translator cannot carry fail loudly instead of
silently changing meaning: an untranslatable construct raises
``ValueError`` from :func:`translate_sql` only when it is actually
executed, never at import time.
"""

from __future__ import annotations

import re
import uuid

#: Canonical timestamp expression (D-SQLite-3): ISO-8601 UTC text
#: with fractional seconds, the same shape ``datetime.now(UTC)``
#: ``.isoformat()`` produces. Parenthesised so it is valid both as
#: a SELECT expression and as a column DEFAULT. NOTE: SQLite
#: ``%f`` already includes the seconds (``SS.SSS``), so the format
#: is ``%H:%M:%f``, not ``%H:%M:%S.%f``.
_NOW_EXPR = "(strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))"


def _now_with_modifier(sign: str, amount: str) -> str:
    """``now() +/- interval`` as one strftime expression."""
    return (
        "(strftime('%Y-%m-%dT%H:%M:%f+00:00','now',"
        f"'{sign}{amount}'))"
    )


#: SQLite expression that builds a version-4 UUID string.
_UUID_EXPR = (
    "(lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2)))"
    " || '-4' || substr(lower(hex(randomblob(2))), 2)"
    " || '-' || substr('ab89', 1 + (abs(random()) % 4), 1)"
    " || substr(lower(hex(randomblob(2))), 2)"
    " || '-' || lower(hex(randomblob(6))))"
)

_RE_NOW_INTERVAL = re.compile(
    r"now\s*\(\s*\)\s*([+-])\s*interval\s*'([^']*)'", re.IGNORECASE
)

#: ``<expr> +/- INTERVAL 'N unit'`` with any left side (a column,
#: a placeholder, ``now()``): SQLite has no INTERVAL, so the whole
#: thing becomes one strftime call in the canonical timestamp
#: format. Only a single ``<number> <unit>`` literal is supported;
#: anything else is left alone so it fails loudly on SQLite.
_RE_INTERVAL = re.compile(
    r"(\bnow\s*\(\s*\)|\bcurrent_timestamp\b|[\w.\"]+|%s)"
    r"\s*([+-])\s*interval\s*'([^']*)'",
    re.IGNORECASE,
)
_INTERVAL_UNITS = {
    "second": "seconds",
    "seconds": "seconds",
    "minute": "minutes",
    "minutes": "minutes",
    "hour": "hours",
    "hours": "hours",
    "day": "days",
    "days": "days",
    "week": "weeks",
    "weeks": "weeks",
    "month": "months",
    "months": "months",
    "year": "years",
    "years": "years",
}
_RE_NOW = re.compile(r"now\s*\(\s*\)", re.IGNORECASE)
_RE_CURRENT_TS_INTERVAL = re.compile(
    r"current_timestamp\s*([+-])\s*interval\s*'([^']*)'", re.IGNORECASE
)
_RE_CURRENT_TS = re.compile(r"current_timestamp\b", re.IGNORECASE)
_RE_CURRENT_DATE = re.compile(r"current_date\b", re.IGNORECASE)
_RE_GEN_UUID = re.compile(r"gen_random_uuid\s*\(\s*\)", re.IGNORECASE)
_RE_CAST = re.compile(
    r"::\s*(?:double\s+precision"
    r"|timestamp\s+with\s+time\s+zone"
    r"|timestamp\s+without\s+time\s+zone"
    r"|time\s+with\s+time\s+zone"
    r"|time\s+without\s+time\s+zone"
    r"|[A-Za-z_][A-Za-z0-9_]*(\s*\([^)]*\))?(\s*\[\])?)",
    re.IGNORECASE,
)
_RE_ANY = re.compile(
    r"ANY\s*\(\s*%s\s*(?:::[\w\[\]]+(\([^)]*\))?)?\s*\)", re.IGNORECASE
)
_RE_ADVISORY = re.compile(
    r"(?:pg_try_advisory_xact_lock|pg_advisory_xact_lock"
    r"|pg_try_advisory_lock|pg_advisory_lock"
    r"|pg_advisory_unlock_all|pg_advisory_unlock)\s*\(",
    re.IGNORECASE,
)
_RE_FOR_UPDATE = re.compile(
    r"FOR\s+UPDATE(\s+SKIP\s+LOCKED|\s+NOWAIT)?", re.IGNORECASE
)
_RE_IS_DISTINCT = re.compile(
    r"IS\s+(NOT\s+)?DISTINCT\s+FROM\b", re.IGNORECASE
)
_RE_ARRAY_AGG = re.compile(r"array_agg\s*\(", re.IGNORECASE)
_RE_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RE_DOLLAR_TAG = re.compile(r"\$[A-Za-z_0-9]*\$")

#: Statements that have no SQLite meaning and are dropped outright.
#: Each entry is matched against the statement's first words.
_DROP_FIRST_WORDS = ("DO", "GRANT", "REVOKE", "LISTEN", "NOTIFY", "UNLISTEN")
_DROP_TWO_WORDS = (
    ("CREATE", "ROLE"),
    ("ALTER", "ROLE"),
    ("DROP", "ROLE"),
    ("CREATE", "POLICY"),
    ("DROP", "POLICY"),
    ("ALTER", "POLICY"),
    ("CREATE", "EXTENSION"),
    ("CREATE", "FUNCTION"),
    ("DROP", "FUNCTION"),
    ("COMMENT", "ON"),
)


def _first_words(sql: str) -> list[str]:
    """The statement's first two words, comments stripped."""
    text = re.sub(r"--[^\n]*", " ", sql)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return [w.upper() for w in re.findall(r"[A-Za-z_]+", text)][:2]


def _drop_statement(sql: str) -> bool:
    """True when the statement has no SQLite meaning at all."""
    words = _first_words(sql)
    if not words:
        return True
    if words[0] in _DROP_FIRST_WORDS:
        return True
    if len(words) == 2 and tuple(words) in _DROP_TWO_WORDS:
        return True
    if words[:2] == ["CREATE", "OR"]:
        # CREATE OR REPLACE FUNCTION ... (plpgsql / sql helpers that
        # only exist to serve RLS policies, which do not exist here).
        rest = re.sub(r"--[^\n]*", " ", sql)
        if re.search(
            r"CREATE\s+OR\s+REPLACE\s+FUNCTION\b", rest, re.IGNORECASE
        ):
            return True
    upper = sql.upper()
    if re.search(
        r"ALTER\s+TABLE\s+\S+\s+(ENABLE|DISABLE)\s+ROW\s+LEVEL\s+SECURITY",
        upper,
    ):
        return True
    if re.search(r"ALTER\s+TABLE\s+\S+\s+DROP\s+CONSTRAINT\b", upper):
        return True
    if re.search(r"ALTER\s+TABLE\s+\S+\s+ADD\s+CONSTRAINT\b", upper):
        # SQLite cannot add a constraint to an existing table. The
        # dropped CHECKs are enforced in Python instead (the ledger
        # validates event types, the stores validate their enums).
        return True
    if re.search(
        r"CREATE\s+(UNIQUE\s+)?INDEX\b.*\bUSING\s+(HNSW|GIN|GIST)\b",
        upper,
        re.DOTALL,
    ):
        return True
    if re.match(r"\s*SET\s+", sql, re.IGNORECASE):
        return True
    if re.match(r"\s*RESET\s+", sql, re.IGNORECASE):
        # RESET ROLE / RESET ALL / RESET <var>: Postgres session
        # commands, no SQLite meaning (same as SET above).
        return True
    return False


def _interval_replacement(match: re.Match) -> str:
    """One ``<expr> +/- INTERVAL 'N unit'`` as a strftime call."""
    left, sign, literal = match.group(1), match.group(2), match.group(3)
    parts = literal.strip().split()
    if len(parts) != 2:
        return match.group(0)
    amount, unit = parts
    unit = _INTERVAL_UNITS.get(unit.lower())
    if unit is None:
        return match.group(0)
    try:
        float(amount)
    except ValueError:
        return match.group(0)
    if left.strip().lower().startswith("now") or left.strip().lower() == "current_timestamp":
        return _now_with_modifier(sign, literal)
    return (
        "(strftime('%Y-%m-%dT%H:%M:%f+00:00', "
        f"{left}, '{sign}{amount} {unit}'))"
    )


def _rewrite_statement_head(sql: str) -> str:
    """Small statement-level rewrites before the token scan."""
    sql = _RE_INTERVAL.sub(_interval_replacement, sql)
    sql = re.sub(
        r"CREATE\s+(UNIQUE\s+)?INDEX\s+CONCURRENTLY\b",
        r"CREATE \1INDEX",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"CREATE\s+(UNIQUE\s+)?INDEX\b(.*?)\bUSING\s+(BTREE|HASH)\b",
        r"CREATE \1INDEX\2",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    sql = re.sub(
        r"DROP\s+TRIGGER\s+IF\s+EXISTS\s+(\S+)\s+ON\s+\S+",
        r"DROP TRIGGER IF EXISTS \1",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


def _truncate_to_deletes(sql: str) -> str | None:
    """``TRUNCATE t1, t2`` as DELETEs, else None."""
    text = sql.strip()
    match = re.match(
        r"TRUNCATE\s+(?:ONLY\s+)?(.+)$", text, re.IGNORECASE | re.DOTALL
    )
    if not match:
        return None
    rest = match.group(1)
    rest = re.split(
        r"\b(?:CASCADE|RESTART\s+IDENTITY|CONTINUE\s+IDENTITY)\b",
        rest,
        flags=re.IGNORECASE,
    )[0]
    tables = []
    for part in rest.split(","):
        name = part.strip().strip('"').strip()
        if not name:
            continue
        name = name.split(".")[-1].strip('"')
        tables.append(name)
    if not tables:
        return None
    return "; ".join(f"DELETE FROM {t}" for t in tables)  # nosec B608 - table names come from the TRUNCATE statement itself, parsed above, never from user input


def _drop_trailing_comparison(out: list[str]) -> str:
    """Remove a trailing ``=`` / ``<>`` / ``!=`` from the output.

    ``col = ANY(%s)`` becomes ``col IN (...)``: the ``=`` must go,
    and ``<> ANY`` becomes ``NOT IN``. Returns ``"NOT "`` for the
    negated forms, else ``""``.
    """
    tail = "".join(out[-4:])
    stripped = tail.rstrip()
    if stripped.endswith("<>") or stripped.endswith("!="):
        _trim_output(out, 2)
        return "NOT "
    if stripped.endswith("="):
        _trim_output(out, 1)
        return ""
    return ""


def _trim_output(out: list[str], count: int) -> None:
    """Remove the last ``count`` non-space characters from ``out``."""
    remaining = count
    while remaining > 0 and out:
        chunk = out[-1]
        trimmed = chunk.rstrip()
        drop = len(chunk) - len(trimmed)
        if drop:
            out[-1] = trimmed
        if not out[-1]:
            out.pop()
            continue
        if len(out[-1]) <= remaining:
            remaining -= len(out[-1])
            out.pop()
        else:
            out[-1] = out[-1][:-remaining]
            remaining = 0


def _matching_paren(sql: str, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at ``open_idx``."""
    depth = 0
    i = open_idx
    while i < len(sql):
        char = sql[i]
        if char == "'":
            i += 1
            while i < len(sql):
                if sql[i] == "'":
                    if sql[i + 1 : i + 2] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif char == '"':
            i += 1
            while i < len(sql) and sql[i] != '"':
                i += 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced parentheses in SQL")


def _scan_dollar_quote(sql: str, start: int) -> int | None:
    """End index (exclusive) of a dollar-quoted block at ``start``."""
    match = _RE_DOLLAR_TAG.match(sql, start)
    if not match:
        return None
    tag = match.group(0)
    end = sql.find(tag, match.end())
    if end < 0:
        raise ValueError(f"unterminated dollar-quoted string {tag!r}")
    return end + len(tag)


def translate_sql(
    sql: str, params: tuple | list | None = None, *, placeholders_only: bool = False
) -> tuple[str, tuple]:
    """Translate one Postgres statement to SQLite.

    Returns ``(new_sql, new_params)``. A statement with no SQLite
    meaning translates to ``("", ())``; the caller treats that as a
    successful no-op. ``params`` may be a tuple, a list, or None.

    With ``placeholders_only`` (for ``executemany``), ``%s`` becomes
    ``?`` without consuming a value: the per-row values are bound
    later by the caller. Constructs that need the value to
    translate (``ANY(%s)`` splicing, advisory-lock parameter
    dropping) raise instead of silently misaligning parameters.
    """
    if params is None:
        params = ()
    else:
        params = tuple(params)
    if _drop_statement(sql):
        return "", ()
    sql = _rewrite_statement_head(sql)
    truncated = _truncate_to_deletes(sql)
    if truncated is not None:
        return truncated, ()
    words = _first_words(sql)
    in_create_table = words[:2] == ["CREATE", "TABLE"]

    out: list[str] = []
    new_params: list = []
    param_idx = 0

    def take_param():
        nonlocal param_idx
        if param_idx >= len(params):
            raise ValueError(
                "not enough parameters for the SQL placeholders"
            )
        value = params[param_idx]
        param_idx += 1
        return value

    i = 0
    n = len(sql)
    while i < n:
        char = sql[i]
        if char == "'":
            # Single-quoted literal ('' is an escaped quote).
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql[j + 1 : j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
            continue
        if char == '"':
            j = sql.find('"', i + 1)
            if j < 0:
                raise ValueError("unterminated quoted identifier")
            out.append(sql[i : j + 1])
            i = j + 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(sql[i:j])
            i = j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                raise ValueError("unterminated block comment")
            out.append(sql[i : j + 2])
            i = j + 2
            continue
        if char == "$":
            end = _scan_dollar_quote(sql, i)
            if end is not None:
                out.append(sql[i:end])
                i = end
                continue
            out.append(char)
            i += 1
            continue
        if char == "%":
            if sql.startswith("%s", i):
                if placeholders_only:
                    out.append("?")
                    i += 2
                    continue
                new_params.append(take_param())
                out.append("?")
                i += 2
                continue
            if sql.startswith("%%", i):
                out.append("%")
                i += 2
                continue
            out.append(char)
            i += 1
            continue
        if char == ":" and sql.startswith("::", i):
            match = _RE_CAST.match(sql, i)
            if match:
                i = match.end()
                continue
            out.append(char)
            i += 1
            continue
        word_match = _RE_WORD.match(sql, i)
        if word_match is not None:
            word = word_match.group(0)
            lower = word.lower()
            if lower == "now":
                interval = _RE_NOW_INTERVAL.match(sql, i)
                if interval:
                    out.append(
                        _now_with_modifier(
                            interval.group(1), interval.group(2)
                        )
                    )
                    i = interval.end()
                    continue
                if _RE_NOW.match(sql, i):
                    out.append(_NOW_EXPR)
                    i = _RE_NOW.match(sql, i).end()
                    continue
            if lower == "current_timestamp":
                interval = _RE_CURRENT_TS_INTERVAL.match(sql, i)
                if interval:
                    out.append(
                        _now_with_modifier(
                            interval.group(1), interval.group(2)
                        )
                    )
                    i = interval.end()
                    continue
                match = _RE_CURRENT_TS.match(sql, i)
                out.append(_NOW_EXPR)
                i = match.end()
                continue
            if lower == "current_date":
                match = _RE_CURRENT_DATE.match(sql, i)
                out.append("(date('now'))")
                i = match.end()
                continue
            if lower == "gen_random_uuid":
                match = _RE_GEN_UUID.match(sql, i)
                if match:
                    out.append(_UUID_EXPR)
                    i = match.end()
                    continue
            if lower == "ilike":
                out.append("LIKE")
                i = word_match.end()
                continue
            if lower == "is":
                match = _RE_IS_DISTINCT.match(sql, i)
                if match:
                    # IS DISTINCT FROM means "not equal" (NULL-safe);
                    # IS NOT DISTINCT FROM means "equal" (NULL-safe).
                    out.append("IS" if match.group(1) else "IS NOT")
                    i = match.end()
                    continue
            if lower == "for":
                match = _RE_FOR_UPDATE.match(sql, i)
                if match:
                    i = match.end()
                    continue
            if lower == "array_agg":
                match = _RE_ARRAY_AGG.match(sql, i)
                if match:
                    out.append("group_concat(")
                    i = match.end()
                    continue
            if lower == "any":
                match = _RE_ANY.match(sql, i)
                if match:
                    if placeholders_only:
                        raise ValueError(
                            "ANY(%s) cannot be translated without its "
                            "value (executemany)"
                        )
                    value = take_param()
                    negated = _drop_trailing_comparison(out)
                    if isinstance(value, (list, tuple, set)):
                        items = list(value)
                        if not items:
                            out.append(f"{negated}IN (NULL)")
                        else:
                            out.append(
                                f"{negated}IN ("
                                + ", ".join("?" for _ in items)
                                + ")"
                            )
                            new_params.extend(items)
                    elif value is None:
                        out.append(f"{negated}IN (NULL)")
                    else:
                        out.append(f"{negated}IN (?)")
                        new_params.append(value)
                    i = match.end()
                    continue
            advisory = _RE_ADVISORY.match(sql, i)
            if advisory:
                open_idx = advisory.end() - 1
                close_idx = _matching_paren(sql, open_idx)
                inner = sql[open_idx + 1 : close_idx]
                if placeholders_only and "%s" in inner:
                    raise ValueError(
                        "advisory lock with parameters cannot be "
                        "translated without its values (executemany)"
                    )
                for _ in range(inner.count("%s")):
                    take_param()
                out.append("1")
                i = close_idx + 1
                continue
            if in_create_table and lower in ("serial", "bigserial"):
                out.append("INTEGER")
                i = word_match.end()
                continue
            if in_create_table and lower == "vector":
                rest = sql[word_match.end() :]
                dim = re.match(r"\s*\(\s*\d+\s*\)", rest)
                out.append("BLOB")
                i = word_match.end() + (dim.end() if dim else 0)
                continue
            out.append(word)
            i = word_match.end()
            continue
        out.append(char)
        i += 1

    translated = "".join(out)
    if in_create_table:
        # A serial primary key is the rowid alias in SQLite only as
        # INTEGER PRIMARY KEY; add AUTOINCREMENT so ids never reuse.
        # The negative lookahead keeps the rule idempotent: the
        # generated SQLite files already carry it, and the runtime
        # translator runs over them again.
        translated = re.sub(
            r"\bINTEGER\s+PRIMARY\s+KEY\b(?!\s+AUTOINCREMENT)",
            "INTEGER PRIMARY KEY AUTOINCREMENT",
            translated,
        )
    if param_idx != len(params):
        raise ValueError(
            f"{len(params) - param_idx} unused parameter(s) for the SQL"
        )
    return translated, tuple(new_params)


def translate_ddl_statement(sql: str) -> str | None:
    """Translate one migration DDL statement, or None to drop it."""
    translated, _ = translate_sql(sql, ())
    translated = translated.strip().rstrip(";")
    if not translated:
        return None
    return translated


def split_statements(sql: str) -> list[str]:
    """Split SQL text into statements on top-level ``;``.

    String literals, quoted identifiers, comments, and
    dollar-quoted blocks never split. A ``CREATE TRIGGER`` body
    (``BEGIN ... END``) never splits either: the semicolons inside
    it belong to the trigger.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_trigger = False
    block_depth = 0
    while i < n:
        char = sql[i]
        if char == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql[j + 1 : j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i : j + 1])
            i = j + 1
            continue
        if char == '"':
            j = sql.find('"', i + 1)
            if j < 0:
                raise ValueError("unterminated quoted identifier")
            buf.append(sql[i : j + 1])
            i = j + 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            buf.append(sql[i:j])
            i = j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                raise ValueError("unterminated block comment")
            buf.append(sql[i : j + 2])
            i = j + 2
            continue
        if char == "$":
            end = _scan_dollar_quote(sql, i)
            if end is not None:
                buf.append(sql[i:end])
                i = end
                continue
            buf.append(char)
            i += 1
            continue
        word_match = _RE_WORD.match(sql, i)
        if word_match is not None:
            word = word_match.group(0)
            lower = word.lower()
            current = "".join(buf)
            if not in_trigger and re.match(
                r"\s*CREATE\s+TRIGGER\b", current, re.IGNORECASE
            ):
                in_trigger = True
            if in_trigger and lower == "begin":
                block_depth += 1
            elif in_trigger and lower == "end":
                block_depth -= 1
                if block_depth <= 0:
                    in_trigger = False
                    block_depth = 0
            buf.append(word)
            i = word_match.end()
            continue
        if char == ";" and not (in_trigger and block_depth > 0):
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            in_trigger = False
            block_depth = 0
            i += 1
            continue
        buf.append(char)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
