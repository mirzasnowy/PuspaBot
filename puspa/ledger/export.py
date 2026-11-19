"""User data export (K12): one portable JSON file, never any secrets.

:func:`export_user_data` dumps everything Puspa holds about one
user into a single JSON file:

- identity: user id + email (never the password hash);
- profile facts, including superseded history (``include_history``);
- episodic memory (chat/task history, without embedding vectors);
- memory derivatives: consolidation summaries, learned procedures,
  memory-slice grants, style examples;
- onboarding state and data consents;
- ledger events for the user's agents (the audit log rows that
  belong to this user, via the ``agents`` -> owner mapping);
- vault metadata: entry names + update times only.

Deliberately NOT exported, and listed in the file itself under
``excluded`` so the gap is visible, not silent:

- vault ciphertext (secret values are never exported);
- ``password_hash`` and session token hashes;
- slice-grant ``token_ref`` values (permission-token references);
- episodic embedding vectors (recomputable derivatives, large);
- any other user's data (every query filters on this user's id,
  and ledger events filter on this user's agent ids);
- the kill-switch flag itself (operator state, not user data).

Secret-looking text that a user typed into memory (for example a
password pasted into a chat line) is redacted with
:func:`puspa.memory.trust.redact_secrets` before it is written, so
the export keeps the shape of the record without the secret.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from puspa.memory.trust import redact_secrets

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Format version of the export file. Bump when the sections change.
EXPORT_VERSION = 1

#: What is deliberately left out of every export (also written
#: into the file itself, so the exclusion is visible to the user).
EXCLUDED = (
    "vault ciphertext: secret values are never exported, only entry names",
    "password_hash: the user's password hash is never exported",
    "session token hashes: never exported",
    "slice-grant token_ref values: permission-token references are never exported",
    "episodic embedding vectors: recomputable derivatives, excluded for size",
    "kill-switch flag: operator state, not user data",
    "other users' data: every query filters on this user's id",
)


@dataclass
class UserExport:
    """One finished export: where it went and what is in it."""

    path: Path
    user_id: str
    exported_at: datetime
    counts: dict[str, int] = field(default_factory=dict)
    excluded: list[str] = field(default_factory=lambda: list(EXCLUDED))

    @property
    def total_rows(self) -> int:
        return sum(self.counts.values())


def _jsonable(value):
    """Recursively convert a value to plain JSON data.

    Datetimes become ISO-8601 strings, UUIDs become strings, and
    every string is passed through secret redaction.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _connect(dsn: str | None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row
    )


def _agent_ids(user_id: str, conn) -> list[str]:
    """Agent ids owned by this user (the ledger has no user_id)."""
    with conn.cursor() as cur:
        cur.execute("SELECT agent_id FROM agents WHERE user_id = %s", (user_id,))
        return [row["agent_id"] for row in cur.fetchall()]


def _ledger_events(agent_ids: list[str], conn) -> list[dict]:
    """Ledger events for these agents, insertion order."""
    if not agent_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM events WHERE agent_id = ANY(%s) ORDER BY created_at ASC, id ASC",
            (agent_ids,),
        )
        return [_jsonable(dict(row)) for row in cur.fetchall()]


def _vault_metadata(user_id: str, conn) -> list[dict]:
    """Vault entry names + update times. Never the ciphertext."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, updated_at FROM vault WHERE user_id = %s ORDER BY name ASC",
            (user_id,),
        )
        return [_jsonable(dict(row)) for row in cur.fetchall()]


def _table_rows(user_id: str, table: str, conn, *, columns: str, order: str) -> list[dict]:
    """All rows of one user-owned table, as JSON data."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {columns} FROM {table} WHERE user_id = %s ORDER BY {order}",
            (user_id,),
        )
        return [_jsonable(dict(row)) for row in cur.fetchall()]


def build_user_export(user_id: str, *, dsn: str | None = None) -> dict:
    """Build the export document for one user (no file written).

    Raises ``ValueError`` when the user does not exist: exporting
    a stranger's id by typo must fail loudly instead of producing
    an empty file that looks like "no data".
    """
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    from puspa.memory import consolidate, episodic, procedural, profile, slices

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email FROM users WHERE id = %s", (user_id,))
            user_row = cur.fetchone()
        if user_row is None:
            raise ValueError(f"unknown user: {user_id!r}")
        agent_ids = _agent_ids(user_id, conn)
        vault = _vault_metadata(user_id, conn)
        onboarding = _table_rows(user_id, "onboarding", conn, columns="*", order="started_at ASC")
        consents = _table_rows(user_id, "data_consents", conn, columns="*", order="approved_at ASC")
        style_examples = _table_rows(
            user_id, "style_examples", conn, columns="*", order="created_at ASC"
        )
        events = _ledger_events(agent_ids, conn)

    facts = [_jsonable(f) for f in profile.list_facts(user_id, include_history=True, dsn=dsn)]
    episodes = [_jsonable(e) for e in episodic.list_episodes(user_id, limit=10000, dsn=dsn)]
    procedures = [_jsonable(p) for p in procedural.list_procedures(user_id, dsn=dsn)]
    summaries = [_jsonable(s) for s in consolidate.list_summaries(user_id, dsn=dsn)]
    grants = [_jsonable(g) for g in slices.list_grants(user_id, dsn=dsn)]
    for grant in grants:
        # A grant record stays; the permission-token reference goes.
        grant["token_ref"] = "«withheld»"

    sections: dict[str, list[dict]] = {
        "profile_facts": facts,
        "episodic": episodes,
        "procedures": procedures,
        "summaries": summaries,
        "slice_grants": grants,
        "style_examples": style_examples,
        "onboarding": onboarding,
        "data_consents": consents,
        "ledger_events": events,
        "vault": vault,
    }
    return {
        "format": "puspa-user-export",
        "version": EXPORT_VERSION,
        "user": {"id": str(user_row["id"]), "email": user_row["email"]},
        "agent_ids": agent_ids,
        "exported_at": datetime.now(UTC).isoformat(),
        "sections": sections,
        "counts": {name: len(rows) for name, rows in sections.items()},
        "excluded": list(EXCLUDED),
        "notes": (
            "World beliefs are deterministic derivatives of the exported "
            "ledger events and can be rebuilt from them; they are not "
            "stored separately. Vault entries list names only — values "
            "are never exported."
        ),
    }


def export_user_data(user_id: str, path: str | Path, *, dsn: str | None = None) -> UserExport:
    """Write one user's data to ``path`` as a single JSON file.

    Returns a :class:`UserExport` with the per-section row counts.
    The file is written atomically (temp file + rename) so a
    failed export never leaves a half-written file behind.
    """
    path = Path(path)
    document = build_user_export(user_id, dsn=dsn)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)
    return UserExport(
        path=path,
        user_id=user_id,
        exported_at=datetime.fromisoformat(document["exported_at"]),
        counts=dict(document["counts"]),
    )
