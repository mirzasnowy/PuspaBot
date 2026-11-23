"""Database access: one factory, two backends (D-SQLite-1).

The desktop backend runs on SQLite (D-SQLite-2: the DSN is a file
path, default ``~/.puspa/puspa.db``) while the hosted backend keeps
Postgres. :func:`connect` picks the backend from the DSN string,
so every call site keeps one code path and Postgres behaviour is
unchanged: a Postgres DSN still opens a real psycopg connection,
with the SQL passed through untouched.

The SQLite connection mimics the psycopg surface the codebase
uses: ``connect(dsn, row_factory=..., autocommit=...)``,
``conn.cursor()``, ``conn.execute(...)``, ``commit`` / ``rollback``,
``autocommit`` as a settable attribute, and the connection as a
context manager (commit on clean exit, rollback on error). Rows
follow ``row_factory`` exactly like psycopg: tuples by default,
dicts when ``dict_row`` is requested (on the connection or on one
``cursor()`` call). Every statement goes through
:func:`puspa.db_translate.translate_sql`, so the Postgres dialect
in the call sites (``%s``, ``::jsonb``, ``now()``, ``ANY(%s)``,
``FOR UPDATE``, advisory locks) runs on SQLite.

Value shapes match Postgres on the way back (D-SQLite-3):

- ``TIMESTAMPTZ`` / ``TIMESTAMP`` columns come back as
  :class:`datetime.datetime` (canonical ISO-8601 UTC text in the
  database, parsed on read); ``DATE`` as :class:`datetime.date`;
  ``UUID`` as :class:`uuid.UUID`; ``JSONB`` / ``JSON`` as the
  parsed value; ``BOOLEAN`` as :class:`bool`. Values that do not
  parse come back as plain text instead of failing the read.
- Parameters adapt the other way: :class:`datetime.datetime` and
  :class:`datetime.date` become ISO-8601 text, :class:`uuid.UUID`
  becomes text, :class:`dict` / :class:`list` / :class:`Jsonb`
  become JSON text, :class:`decimal.Decimal` becomes a float.
  Naive datetimes are stored as written; callers must pass
  timezone-aware UTC datetimes for timestamp columns.

Connection setup (D-SQLite-7): WAL mode, ``busy_timeout`` 5000,
``PRAGMA foreign_keys = ON`` on every connection. Writers take
``BEGIN IMMEDIATE`` through :meth:`Connection.begin_immediate`
where a select-then-write must stay atomic.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .db_translate import split_statements, translate_sql

try:
    # Eager on purpose: Jsonb subclasses psycopg's Jsonb below,
    # so a raw psycopg connection on the Postgres path adapts it
    # natively. A psycopg-less install still works: the class
    # stands alone and only the SQLite path is usable then.
    from psycopg.types.json import Jsonb as _PgJsonb
except ImportError:  # pragma: no cover - psycopg is installed here
    _PgJsonb = None

#: Environment variable holding the desktop database file path.
SQLITE_PATH_ENV = "PUSPA_SQLITE_PATH"

#: How long (milliseconds) a contended SQLite write waits.
_BUSY_TIMEOUT_MS = 5000


def default_sqlite_path() -> str:
    """The desktop database file (D-SQLite-2)."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", "~")).expanduser()
        return str(base / "Puspa" / "puspa.db")
    return str(Path.home() / ".puspa" / "puspa.db")


def is_sqlite_dsn(dsn) -> bool:
    """True when ``dsn`` addresses a SQLite file (or nothing)."""
    if dsn is None:
        return True
    if isinstance(dsn, Path):
        return True
    text = str(dsn)
    if text == ":memory:":
        return True
    if text.startswith(("sqlite://", "sqlite:")):
        return True
    lowered = text.lower()
    if lowered.endswith((".db", ".sqlite", ".sqlite3")):
        return True
    if lowered.startswith(("postgres://", "postgresql://")):
        return False
    # A keyword/value DSN (dbname=... user=... host=...) is
    # Postgres; a bare file path never contains '='.
    return "=" not in text


def resolve_dsn(dsn=None) -> str:
    """One canonical DSN resolution (D-SQLite-2).

    Explicit argument, then ``PUSPA_SQLITE_PATH``, then ``PUSPA_DSN``
    / ``PUSPA_TEST_DSN``, then the default SQLite file. A Postgres
    DSN passed explicitly or through the environment is returned
    unchanged, so the hosted backend keeps working.
    """
    if dsn is not None:
        return str(dsn)
    sqlite_path = os.environ.get(SQLITE_PATH_ENV)
    if sqlite_path:
        return sqlite_path
    env_dsn = os.environ.get("PUSPA_DSN") or os.environ.get("PUSPA_TEST_DSN")
    if env_dsn:
        return env_dsn
    return default_sqlite_path()


def now_iso() -> str:
    """The canonical timestamp (D-SQLite-3): ISO-8601 UTC text."""
    return datetime.now(UTC).isoformat()


def parse_timestamp(value):
    """A stored timestamp value as a datetime, or None when empty.

    Accepts the datetime objects the SQLite converters return, ISO
    text, and anything else (returned unchanged when unparseable).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


class Jsonb(_PgJsonb if _PgJsonb is not None else object):
    """A JSON value bound to a JSONB column (psycopg's Jsonb).

    Subclasses psycopg's Jsonb so a raw psycopg connection on the
    Postgres path adapts it natively (its dumper reads ``.obj``);
    the SQLite path reads ``.value`` instead. ``.value`` is kept
    as an alias because the SQLite adapter uses it. When psycopg
    is not installed the class stands alone (SQLite-only install).
    """

    __slots__ = ("dumps", "value")

    def __init__(self, value, dumps=None):
        if _PgJsonb is not None:
            super().__init__(value, dumps=dumps)
        self.value = value
        self.dumps = dumps


class _DictRow:
    """Marker for ``psycopg.rows.dict_row``: rows are always dicts.

    It is also callable with a psycopg cursor, exactly like
    ``psycopg.rows.dict_row``: psycopg calls the row factory with
    the cursor and uses the returned row maker (``cursor.py`` in
    psycopg). The Postgres path hands this marker to real psycopg
    connections — ``connect(row_factory=dict_row)`` and
    ``conn.cursor(row_factory=dict_row)`` — so it must behave
    like the real factory there; the SQLite path only tests
    identity and never calls it.
    """

    def __call__(self, cursor):
        from psycopg.rows import dict_row as _pg_dict_row

        return _pg_dict_row(cursor)


dict_row = _DictRow()

#: psycopg-compatible error aliases for both backends.
#:
#: Every alias catches the error on EITHER backend: the SQLite
#: error and the matching psycopg error, as a tuple. An ``except``
#: clause, ``pytest.raises`` and ``isinstance`` all accept tuples.
#: A single-class alias would silently miss the other backend's
#: errors: ``except pg_errors.UndefinedColumn`` must catch
#: psycopg's ``UndefinedColumn`` on Postgres just as it catches
#: the SQLite ``OperationalError`` the translator maps it to.
#:
#: Two rules, both load-bearing. First, the aliases are ALWAYS
#: tuples (a one-tuple when psycopg is not installed), so a call
#: site can splat one into a bigger except clause. Second, NEVER
#: nest an alias inside another except tuple — ``except
#: (ValueError, _db_Error)`` raises ``TypeError`` at runtime,
#: because the nested tuple is not an exception class. Splat it
#: (``except (ValueError, *_db_Error)``) or give it its own
#: clause. And never raise an alias: a tuple is not callable;
#: raise the concrete error the backend would really raise.
try:
    import psycopg as _psycopg
    import psycopg.errors as _pg_errors
except ImportError:  # pragma: no cover - psycopg is a hard dependency
    _psycopg = None
    _pg_errors = None


def _both(sqlite_error, pg_error):
    """One alias catching ``sqlite_error`` or ``pg_error``.

    Always a tuple, even with one member (see the rules above).
    """
    if pg_error is None:
        return (sqlite_error,)
    return (sqlite_error, pg_error)


def _pg(name):
    return getattr(_pg_errors, name, None) if _pg_errors is not None else None


Error = _both(sqlite3.Error, getattr(_psycopg, "Error", None))
DatabaseError = _both(sqlite3.DatabaseError, getattr(_psycopg, "DatabaseError", None))
InterfaceError = _both(sqlite3.InterfaceError, getattr(_psycopg, "InterfaceError", None))
OperationalError = _both(sqlite3.OperationalError, getattr(_psycopg, "OperationalError", None))
ProgrammingError = _both(sqlite3.ProgrammingError, getattr(_psycopg, "ProgrammingError", None))
IntegrityError = _both(sqlite3.IntegrityError, getattr(_psycopg, "IntegrityError", None))


class _Errors:
    """The ``psycopg.errors`` names the codebase catches."""

    UniqueViolation = _both(sqlite3.IntegrityError, _pg("UniqueViolation"))
    ForeignKeyViolation = _both(sqlite3.IntegrityError, _pg("ForeignKeyViolation"))
    CheckViolation = _both(sqlite3.IntegrityError, _pg("CheckViolation"))
    NotNullViolation = _both(sqlite3.IntegrityError, _pg("NotNullViolation"))
    UndefinedTable = _both(sqlite3.OperationalError, _pg("UndefinedTable"))
    UndefinedColumn = _both(sqlite3.OperationalError, _pg("UndefinedColumn"))
    SyntaxError = _both(sqlite3.OperationalError, _pg("SyntaxError"))
    InvalidTextRepresentation = _both(sqlite3.OperationalError, _pg("InvalidTextRepresentation"))
    # SQLite has no role system, so a privilege error has no
    # direct equivalent; it surfaces as an operational error.
    InsufficientPrivilege = _both(sqlite3.OperationalError, _pg("InsufficientPrivilege"))
    # RAISE EXCEPTION in Postgres is the delete-guard trigger
    # (migration 0003); on SQLite the translated trigger uses
    # RAISE(ABORT, ...), which surfaces as a constraint error.
    RaiseException = _both(sqlite3.IntegrityError, _pg("RaiseException"))


errors = _Errors()


def _timestamp_converter(raw: bytes):
    try:
        return datetime.fromisoformat(raw.decode("utf-8"))
    except ValueError:
        return raw.decode("utf-8", "replace")


def _date_converter(raw: bytes):
    try:
        return date.fromisoformat(raw.decode("utf-8"))
    except ValueError:
        return raw.decode("utf-8", "replace")


def _uuid_converter(raw: bytes):
    try:
        return uuid.UUID(raw.decode("utf-8"))
    except ValueError:
        return raw.decode("utf-8", "replace")


def _json_converter(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")


def _bool_converter(raw: bytes):
    text = raw.decode("utf-8", "replace").strip().lower()
    if text in ("1", "true", "t", "yes"):
        return True
    if text in ("0", "false", "f", "no", ""):
        return False
    return text


def _decimal_converter(raw: bytes):
    try:
        return Decimal(raw.decode("utf-8"))
    except (UnicodeDecodeError, InvalidOperation):
        return raw.decode("utf-8", "replace")


def _parse_postgres_array_literal(text: str) -> list | None:
    """A Postgres array literal (``{a,b,"c,d"}``) as a list, else None."""
    if not text.startswith("{") or not text.endswith("}"):
        return None
    body = text[1:-1]
    if body == "":
        return []
    items: list[str] = []
    current: list[str] = []
    in_quotes = False
    i = 0
    while i < len(body):
        char = body[i]
        if in_quotes:
            if char == "\\" and i + 1 < len(body):
                current.append(body[i + 1])
                i += 2
                continue
            if char == '"':
                in_quotes = False
                i += 1
                continue
            current.append(char)
            i += 1
            continue
        if char == '"':
            in_quotes = True
            i += 1
            continue
        if char == ",":
            items.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    if in_quotes:
        return None
    items.append("".join(current))
    return [item if item != "NULL" else None for item in items]


def _array_converter(raw: bytes):
    """A Postgres array column (``UUID[]`` / ``TEXT[]``) as a list.

    Writers bind Python lists, which the parameter adapter stores
    as JSON text; psycopg on Postgres returns a list, so the
    SQLite read must too. The column DEFAULT is the Postgres
    literal ``'{}'``, which parses to an empty list.
    """
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        return parsed
    literal = _parse_postgres_array_literal(text)
    if literal is not None:
        return literal
    return text


for _name, _converter in (
    ("TIMESTAMPTZ", _timestamp_converter),
    ("timestamptz", _timestamp_converter),
    ("TIMESTAMP", _timestamp_converter),
    ("timestamp", _timestamp_converter),
    ("timestamp with time zone", _timestamp_converter),
    ("timestamp without time zone", _timestamp_converter),
    ("DATE", _date_converter),
    ("date", _date_converter),
    ("UUID", _uuid_converter),
    ("uuid", _uuid_converter),
    ("JSONB", _json_converter),
    ("jsonb", _json_converter),
    ("JSON", _json_converter),
    ("json", _json_converter),
    ("BOOLEAN", _bool_converter),
    ("boolean", _bool_converter),
    ("BOOL", _bool_converter),
    ("bool", _bool_converter),
    ("NUMERIC", _decimal_converter),
    ("numeric", _decimal_converter),
    ("DECIMAL", _decimal_converter),
    ("decimal", _decimal_converter),
    ("UUID[]", _array_converter),
    ("TEXT[]", _array_converter),
):
    sqlite3.register_converter(_name, _converter)


def _adapt_param(value):
    """One bound parameter as a value sqlite3 accepts."""
    if value is None or isinstance(value, (str, int, float, bytes)):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Jsonb):
        return json.dumps(value.value, default=str)
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    if isinstance(value, (tuple, set)):
        return json.dumps(list(value), default=str)
    if isinstance(value, datetime):
        # Canonical timestamp text (D-SQLite-3): always with
        # milliseconds, exactly like the strftime format the SQL
        # translator emits for now(). A datetime without a
        # fractional part would sort AFTER its own stored form in
        # a TEXT comparison ('...00.000+00:00' > '...00+00:00'),
        # so due triggers and range queries would miss rows.
        return value.isoformat(timespec="milliseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, Path):
        return str(value)
    return value


def _column_name(raw: str) -> str:
    """One result column name, spelled the way Postgres spells it.

    Postgres names an unaliased function call after the function
    (``SELECT COUNT(*)`` comes back as ``count``); SQLite names it
    after the whole call (``COUNT(*)``). Call sites read
    ``row["count"]``, so the function name is used, lowercased.
    Aliased columns and plain column names pass through untouched.
    """
    match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\(.*\)$", raw.strip())
    return match.group(1).lower() if match else raw


class Cursor:
    """A psycopg-shaped cursor over one sqlite3 cursor."""

    def __init__(self, raw: sqlite3.Cursor, connection: Connection, row_factory=None):
        self._raw = raw
        self._connection = connection
        # Tuples by default, dicts when dict_row was requested,
        # exactly like psycopg.
        self._dict_rows = row_factory is not None
        self._rowcount_override: int | None = None
        self._noop = False

    def _convert(self, row):
        if row is None or not self._dict_rows:
            return row
        names = [d[0] for d in self._raw.description]
        return {_column_name(name): value for name, value in zip(names, row)}

    def _add_column_already_exists(self, statement: str) -> bool:
        """True when ``ADD COLUMN IF NOT EXISTS`` names a live column.

        SQLite has no ``IF NOT EXISTS`` for ``ADD COLUMN``; the
        Postgres files use it for idempotent backfills, so the
        check is done here against ``PRAGMA table_info`` instead.
        """
        match = re.match(
            r"\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?"
            r'"?([\w.]+)"?\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"?([\w]+)"?',
            statement,
            re.IGNORECASE,
        )
        if not match:
            return False
        table, column = match.group(1).split(".")[-1], match.group(2)
        info = self._connection._raw.execute(f"PRAGMA table_info({table})").fetchall()
        # PRAGMA rows are plain tuples: (cid, name, type, ...).
        return any(row[1] == column for row in info)

    def execute(self, sql, params=None):
        translated, new_params = translate_sql(sql, params)
        if not translated:
            self._noop = True
            self._rowcount_override = -1
            return self
        self._noop = False
        self._rowcount_override = None
        statements = split_statements(translated)
        if sql.strip().upper().startswith("TRUNCATE"):
            # TRUNCATE ... CASCADE deletes every listed table
            # regardless of foreign-key order. SQLite checks FKs
            # row by row, so a parent deleted before its child
            # would fail; deferring the check to commit (when all
            # listed rows are gone) gives the same result. The
            # deferral can be set inside a transaction, unlike
            # PRAGMA foreign_keys, and it is switched off again
            # right after the batch.
            statements = [
                "PRAGMA defer_foreign_keys=ON",
                *statements,
                "PRAGMA defer_foreign_keys=OFF",
            ]
        if len(statements) > 1 and new_params:
            raise ValueError("parameters are not allowed with multi-statement SQL")
        adapted = tuple(_adapt_param(v) for v in new_params)
        for statement in statements:
            if self._add_column_already_exists(statement):
                continue
            rewritten = re.sub(
                r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b",
                "ADD COLUMN",
                statement,
                flags=re.IGNORECASE,
            )
            self._raw.execute(rewritten, adapted if len(statements) == 1 else ())
        return self

    def executemany(self, sql, params_list):
        translated, _ = translate_sql(sql, (), placeholders_only=True)
        if not translated:
            self._noop = True
            return self
        statements = split_statements(translated)
        if len(statements) != 1:
            raise ValueError("executemany needs exactly one statement")
        adapted = [tuple(_adapt_param(v) for v in row) for row in params_list]
        self._raw.executemany(statements[0], adapted)
        return self

    def fetchone(self):
        if self._noop:
            return None
        return self._convert(self._raw.fetchone())

    def fetchall(self):
        if self._noop:
            return []
        rows = [self._convert(row) for row in self._raw.fetchall()]
        self._rowcount_override = len(rows)
        return rows

    def fetchmany(self, size: int | None = None):
        if self._noop:
            return []
        rows = self._raw.fetchmany(size) if size is not None else self._raw.fetchmany()
        rows = [self._convert(row) for row in rows]
        self._rowcount_override = len(rows)
        return rows

    def __iter__(self):
        if self._noop:
            return iter(())
        return (self._convert(row) for row in self._raw)

    @property
    def rowcount(self) -> int:
        if self._rowcount_override is not None:
            return self._rowcount_override
        return self._raw.rowcount

    @property
    def description(self):
        return self._raw.description

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class Connection:
    """A psycopg-shaped connection over one sqlite3 connection."""

    def __init__(self, raw: sqlite3.Connection, row_factory=None):
        self._raw = raw
        self._row_factory = row_factory

    def cursor(self, row_factory=None):
        if row_factory is None:
            row_factory = self._row_factory
        return Cursor(self._raw.cursor(), self, row_factory)

    def execute(self, sql, params=None):
        return self.cursor().execute(sql, params)

    def executemany(self, sql, params_list):
        return self.cursor().executemany(sql, params_list)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    @property
    def autocommit(self) -> bool:
        return self._raw.isolation_level is None

    @autocommit.setter
    def autocommit(self, value: bool):
        self._raw.isolation_level = None if value else ""

    def begin_immediate(self):
        """Take the write lock now (D-SQLite-7).

        A no-op when a transaction is already open: the caller must
        call this before the first write of the transaction.
        """
        if not self._raw.in_transaction:
            self._raw.execute("BEGIN IMMEDIATE")

    @property
    def in_transaction(self) -> bool:
        return self._raw.in_transaction

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        return False


def maybe_begin_immediate(conn) -> None:
    """Take the SQLite write lock when ``conn`` is SQLite.

    A no-op for a Postgres connection (which has no such method and
    does not need it: row locks do the job there). Call sites use
    this instead of branching on the backend themselves.
    """
    begin = getattr(conn, "begin_immediate", None)
    if callable(begin):
        begin()


def _sqlite_connect(path: str, autocommit: bool, row_factory=None) -> Connection:
    """Open a SQLite file with the desktop pragmas (D-SQLite-7)."""
    if path.startswith("sqlite://"):
        path = path[len("sqlite://") :]
    elif path.startswith("sqlite:"):
        path = path[len("sqlite:") :]
    if path != ":memory:":
        parent = Path(path).expanduser().parent
        parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(
        path,
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=30.0,
        isolation_level=None if autocommit else "",
        check_same_thread=False,
    )
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    raw.execute("PRAGMA foreign_keys=ON")
    return Connection(raw, row_factory)


def connect(dsn=None, *, row_factory=None, autocommit=False, **kwargs):
    """Open a database connection for ``dsn`` (D-SQLite-1).

    A SQLite DSN (a file path, ``:memory:``, or nothing) opens a
    SQLite connection; a Postgres DSN opens a real psycopg
    connection with the same arguments, so hosted behaviour is
    unchanged. Unknown keyword arguments are ignored on the SQLite
    path and passed through on the Postgres path.
    """
    resolved = resolve_dsn(dsn)
    if not is_sqlite_dsn(resolved):
        import psycopg

        call_kwargs: dict = {}
        if row_factory is not None:
            call_kwargs["row_factory"] = row_factory
        if autocommit:
            call_kwargs["autocommit"] = True
        call_kwargs.update(kwargs)
        return psycopg.connect(resolved, **call_kwargs)
    return _sqlite_connect(resolved, autocommit, row_factory)
