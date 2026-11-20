"""Docs-drift test (G15, hermes-agent pattern: config/docs changes must
have matching docs).

Rule: the ledger event-type set must be identical in all three places
that list it, because each place enforces a different thing and a drift
between them silently changes what the system accepts:

- ``puspa/ledger/ledger.py`` — ``EVENT_TYPES`` (Python validation).
- ``puspa/migrations/versions/0001_core.sql`` — the ``CHECK (type IN
  (...))`` lists, in both the ``CREATE TABLE`` constraint and the
  revision 7 migration block (database validation).
- ``docs/contracts.md`` — the "Closed type set (31, revision 7: ...)"
  line (the written contract).

Adding an event type means updating all three in one commit; this test
fails the commit if any one is missed. The count in the contracts line
(31) is checked against the real set size too, so a stale count is
caught as well.

This test imports ``puspa.ledger`` (which needs psycopg installed) but
opens no database connection, so it also runs in CI's dependency-light
``checks`` lane with ``--noconftest``.
"""

from __future__ import annotations

import re
from pathlib import Path

from puspa.ledger import EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_MD = REPO_ROOT / "docs" / "contracts.md"
LEDGER_SCHEMA_SQL = REPO_ROOT / "puspa" / "migrations" / "versions" / "0001_core.sql"


def _event_types_from_contracts() -> tuple[set[str], int]:
    """Parse the 'Closed type set (N, revision 7: ...)' line in contracts.md."""
    lines = CONTRACTS_MD.read_text(encoding="utf-8").splitlines()
    matches = [line for line in lines if "Closed type set" in line]
    assert len(matches) == 1, (
        f"expected exactly one 'Closed type set' line in {CONTRACTS_MD}, found {len(matches)}"
    )
    line = matches[0]
    count_match = re.search(r"\((\d+),", line)
    assert count_match is not None, f"could not parse the count from the contracts line: {line!r}"
    claimed_count = int(count_match.group(1))
    # The list follows the closing paren of "(31, revision 7: 22 old +
    # 9 new):" — the first colon on the line sits inside those parens,
    # so split on "):" instead of ":".
    list_match = re.search(r"\):\s*(.*?)\s*\.?\s*$", line)
    assert list_match is not None, (
        f"could not find the event-type list in the contracts line: {line!r}"
    )
    listed = list_match.group(1).rstrip(".")
    types = {item.strip() for item in listed.split(",") if item.strip()}
    return types, claimed_count


def _event_types_from_schema() -> list[set[str]]:
    """Parse every CHECK (type IN (...)) list in the ledger schema."""
    sql = LEDGER_SCHEMA_SQL.read_text(encoding="utf-8")
    groups = re.findall(r"CHECK \(type IN \((.*?)\)\)", sql, flags=re.DOTALL)
    assert groups, f"no CHECK (type IN (...)) list found in {LEDGER_SCHEMA_SQL}"
    parsed = []
    for group in groups:
        types = set(re.findall(r"'([a-z_]+)'", group))
        parsed.append(types)
    return parsed


def test_event_types_match_contracts() -> None:
    """docs/contracts.md lists exactly the code's EVENT_TYPES (all 31)."""
    docs_types, claimed_count = _event_types_from_contracts()
    assert docs_types == set(EVENT_TYPES), (
        "docs/contracts.md event-type list drifted from puspa.ledger.EVENT_TYPES:\n"
        f"  only in docs: {sorted(docs_types - set(EVENT_TYPES))}\n"
        f"  only in code: {sorted(set(EVENT_TYPES) - docs_types)}"
    )
    assert claimed_count == len(EVENT_TYPES), (
        f"docs/contracts.md claims {claimed_count} event types but the code "
        f"has {len(EVENT_TYPES)} — update the count in the contracts line"
    )


def test_event_types_match_schema_check() -> None:
    """Every CHECK list in the ledger migration equals EVENT_TYPES."""
    for index, sql_types in enumerate(_event_types_from_schema()):
        assert sql_types == set(EVENT_TYPES), (
            f"CHECK list #{index + 1} in puspa/migrations/versions/0001_core.sql "
            "drifted from puspa.ledger.EVENT_TYPES:\n"
            f"  only in schema: {sorted(sql_types - set(EVENT_TYPES))}\n"
            f"  only in code: {sorted(set(EVENT_TYPES) - sql_types)}"
        )


def test_schema_check_lists_agree_with_each_other() -> None:
    """The CREATE TABLE list and the migration list in 0001_core agree.

    The migration block exists because CREATE TABLE IF NOT EXISTS does not
    change the constraint of a table created when the set was smaller; if
    the two lists disagree, fresh databases and migrated databases accept
    different event types.
    """
    parsed = _event_types_from_schema()
    assert len(parsed) == 2, (
        f"expected exactly 2 CHECK lists in {LEDGER_SCHEMA_SQL} "
        f"(CREATE TABLE + migration), found {len(parsed)}"
    )
    assert parsed[0] == parsed[1], (
        "CREATE TABLE CHECK list and migration CHECK list disagree:\n"
        f"  only in CREATE TABLE: {sorted(parsed[0] - parsed[1])}\n"
        f"  only in migration: {sorted(parsed[1] - parsed[0])}"
    )
