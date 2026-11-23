"""Generate the SQLite migration set from the Postgres set.

Usage: ``python scripts/generate_sqlite_migrations.py`` (from the
repo root). Reads ``puspa/migrations/versions/*.sql`` and writes
``puspa/migrations/versions_sqlite/*.sql`` with the same version
numbers and file names, translating each statement with
:mod:`puspa.db_translate` (D-SQLite-6: the two sets define the same
tables and columns; the drift test in
``tests/test_sqlite_migrations.py`` enforces it).

What the generator drops or rewrites, beyond the generic
translator rules:

- RLS (``CREATE POLICY`` / grants / ``ENABLE ROW LEVEL
  SECURITY``): dropped (D-SQLite-5). A file whose whole section
  drops away (0021) becomes a ``SELECT 1`` no-op, because the
  runner refuses empty sections.
- ``ALTER TABLE ... ADD CONSTRAINT ... CHECK``: dropped. SQLite
  cannot add a constraint to an existing table; the checks are
  enforced in Python instead (the ledger validates event types,
  the stores validate their enums).
- ``CREATE OR REPLACE FUNCTION`` (plpgsql / sql helpers that only
  serve RLS policies): dropped. The one trigger function with
  real behaviour, ``tolak_delete_puspa`` (0003), is rewritten as
  a native SQLite trigger below.
- The 0006 ``DO`` block (information_schema guard for the
  pre-K3 ``events`` table): replaced with its plain statements.
  The ``ADD COLUMN valid_at`` step is skipped there because
  0001 already adds the column in this set.

Never edit a generated file by hand: edit the Postgres file (or
this script's fixups) and regenerate.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from puspa.db_translate import split_statements, translate_ddl_statement
from puspa.migrations.runner import _split_sections

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "puspa" / "migrations" / "versions"
TARGET_DIR = REPO_ROOT / "puspa" / "migrations" / "versions_sqlite"

HEADER = (
    "-- Generated from puspa/migrations/versions/{name} by\n"
    "-- scripts/generate_sqlite_migrations.py. Do not edit by hand;\n"
    "-- edit the Postgres file and regenerate.\n\n"
)

#: The 0003 delete-guard as a native SQLite trigger. The message
#: keeps the original wording: it is user-visible behaviour, not
#: new code.
SQLITE_DELETE_GUARD_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS trg_tolak_delete_puspa\n"
    "    BEFORE DELETE ON agent_meta\n"
    "    FOR EACH ROW WHEN OLD.required\n"
    "    BEGIN SELECT RAISE(ABORT, 'Puspa tidak bisa dihapus'); END;"
)

#: The 0006 upgrade block, flattened: 0001 already adds
#: ``valid_at`` in this set, so only the backfill and the index
#: remain.
SQLITE_WORLD_VALID_AT = (
    "UPDATE events SET valid_at = created_at WHERE valid_at IS NULL;\n"
    "CREATE INDEX IF NOT EXISTS idx_event_valid_at ON events (valid_at);"
)


def _apply_fixups(name: str, section: str) -> str:
    """Per-file replacements before generic translation."""
    if name.startswith("0003_") and "CREATE OR REPLACE FUNCTION tolak_delete_puspa" in section:
        pattern = re.compile(
            r"CREATE OR REPLACE FUNCTION tolak_delete_puspa\(\)"
            r" RETURNS trigger AS \$\$.*?END;\s*\$\$ LANGUAGE plpgsql;\s*"
            r"DROP TRIGGER IF EXISTS trg_tolak_delete_puspa ON agent_meta;\s*"
            r"CREATE TRIGGER trg_tolak_delete_puspa BEFORE DELETE ON agent_meta\s*"
            r"FOR EACH ROW EXECUTE FUNCTION tolak_delete_puspa\(\);",
            re.DOTALL,
        )
        section, count = pattern.subn(SQLITE_DELETE_GUARD_TRIGGER, section)
        if count != 1:
            raise SystemExit(f"{name}: delete-guard fixup did not match")
    if name.startswith("0006_") and "EXECUTE 'ALTER TABLE events ADD COLUMN" in section:
        pattern = re.compile(
            r"DO \$\$\s*BEGIN\s*IF EXISTS \(.*?END IF;\s*END \$\$;",
            re.DOTALL,
        )
        section, count = pattern.subn(SQLITE_WORLD_VALID_AT, section)
        if count != 1:
            raise SystemExit(f"{name}: valid_at fixup did not match")
    return section


def _translate_section(section: str) -> str:
    """Translate one up/down section to SQLite statements."""
    statements: list[str] = []
    for raw in split_statements(section):
        translated = translate_ddl_statement(raw)
        if translated is not None:
            statements.append(translated)
    if not statements:
        return (
            "-- Nothing in this section has a SQLite meaning (RLS /\n"
            "-- grants only); the runner refuses empty sections.\n"
            "SELECT 1;"
        )
    return ";\n\n".join(statements) + ";"


def generate() -> list[Path]:
    """Regenerate every file in ``versions_sqlite/``."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in sorted(SOURCE_DIR.glob("*.sql")):
        up_sql, down_sql = _split_sections(source)
        up_sql = _apply_fixups(source.name, up_sql)
        down_sql = _apply_fixups(source.name, down_sql)
        text = (
            HEADER.format(name=source.name)
            + "-- migrate:up\n"
            + _translate_section(up_sql)
            + "\n\n-- migrate:down\n"
            + _translate_section(down_sql)
            + "\n"
        )
        target = TARGET_DIR / source.name
        target.write_text(text, encoding="utf-8")
        written.append(target)
    # Remove stale generated files with no Postgres source.
    sources = {p.name for p in SOURCE_DIR.glob("*.sql")}
    for stale in TARGET_DIR.glob("*.sql"):
        if stale.name not in sources:
            stale.unlink()
    return written


def main() -> int:
    written = generate()
    print(f"wrote {len(written)} files to {TARGET_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
