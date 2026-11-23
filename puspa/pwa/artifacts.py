"""Artifact store for the artifacts + run-logs browser.

What this is: one durable row per file a run produced
(name, kind, media type, size, the bytes themselves), plus
the read path the PWA browser serves. The run-log half of
the browser needs no store of its own: a run's log is its
ledger events, already replayed step by step by
:mod:`puspa.pwa.replay`, so this module only holds artifacts.

The first producer is the replay GIF export: exporting a
run's replay records the generated GIF here (see
:mod:`puspa.pwa.replay_api`). Any later producer records
through :meth:`ArtifactStore.record_artifact` with the same
columns — there is deliberately no second artifact storage
anywhere else.

Rules the store keeps:

- Ownership is enforced here, not just in the HTTP layer:
  every method takes the requesting ``user_id`` and refuses
  (:class:`ArtifactError`, 403) when the agent is not theirs,
  and an artifact of another user's agent reads as 404,
  never as someone else's file.
- The list answer carries metadata only (no bytes); the
  bytes are served by :meth:`ArtifactStore.get_artifact`
  for the single download route.
- A 5 MB cap keeps one runaway export from bloating the
  table; the cap is a guard rail, not a target — real
  artifacts (a rendered GIF, a markdown summary) stay far
  below it.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from puspa.db import connect as _db_connect
from puspa.db import dict_row

from ..evidence.proof import user_agent_ids

__all__ = [
    "ARTIFACT_KINDS",
    "MAX_ARTIFACT_BYTES",
    "Artifact",
    "ArtifactError",
    "ArtifactStore",
]

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = None

#: Migration that owns the artifacts table: ``create_schema``
#: applies it through the migration runner — table DDL lives
#: only in migrations.
ARTIFACTS_MIGRATION = "0035"

#: Largest artifact kept in the table (5 MB). A rendered GIF
#: or a markdown summary is kilobytes; anything bigger is a
#: bug or abuse, not an artifact.
MAX_ARTIFACT_BYTES = 5_000_000

#: Kinds an artifact may carry. The kind is a short label for
#: the browser's list (and its icon text), not a file
#: extension: the download uses ``media_type`` + ``name``.
ARTIFACT_KINDS = ("gif", "markdown", "text", "json", "image", "file")

#: Longest artifact name kept (a filesystem-safe length).
MAX_NAME_CHARS = 200


class ArtifactError(Exception):
    """An artifact read or write was refused.

    ``status`` is the HTTP status the API layer answers with:
    403 not your agent, 404 unknown artifact, 400 invalid
    record (bad kind, empty name, oversize content).
    """

    def __init__(self, message: str, *, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Artifact:
    """One file a run produced."""

    artifact_id: str
    user_id: str
    agent_id: str
    run_id: str | None
    name: str
    kind: str
    media_type: str
    size_bytes: int
    created_at: str
    content: bytes | None = None

    def to_dict(self) -> dict:
        """JSON-safe metadata of this artifact (no bytes)."""
        return {
            "artifact_id": self.artifact_id,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "name": self.name,
            "kind": self.kind,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
        }


def _row_to_artifact(row: dict, *, with_content: bool) -> Artifact:
    created_at = row["created_at"]
    if isinstance(created_at, datetime):
        created_at = created_at.astimezone(UTC).isoformat()
    content = row.get("content")
    if isinstance(content, memoryview):
        content = content.tobytes()
    return Artifact(
        artifact_id=str(row["id"]),
        user_id=row["user_id"],
        agent_id=row["agent_id"],
        run_id=row["run_id"],
        name=row["name"],
        kind=row["kind"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        created_at=str(created_at),
        content=content if with_content else None,
    )


class ArtifactStore:
    """The durable artifacts table: one row per produced file."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)

    def create_schema(self) -> None:
        """Apply migration 0035 through the migration runner.

        Idempotent; safe to call before every test. Test
        fixtures apply only migration 0001, so artifact tests
        call this themselves; a database that already records
        0035 applies nothing here.
        """
        from ..migrations import apply_migrations

        apply_migrations(self.dsn, target=ARTIFACTS_MIGRATION)

    def _connect(self):
        return _db_connect(self.dsn, row_factory=dict_row)

    def require_agent_owner(self, user_id: str, agent_id: str) -> None:
        """403 when ``agent_id`` is not one of ``user_id``'s agents."""
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if agent_id not in user_agent_ids(user_id, dsn=self.dsn):
            raise ArtifactError(f"agent {agent_id!r} does not belong to this user", status=403)

    def record_artifact(
        self,
        *,
        user_id: str,
        agent_id: str,
        run_id: str | None,
        name: str,
        kind: str,
        media_type: str,
        content: bytes,
    ) -> Artifact:
        """Record one produced file; the stored row (metadata).

        Raises :class:`ArtifactError` (403) when the agent is
        not the user's, (400) when the name is empty, the kind
        is unknown, or the content is over the size cap.
        """
        self.require_agent_owner(user_id, agent_id)
        if not isinstance(name, str) or not name.strip():
            raise ArtifactError("artifact name must be a non-empty string", status=400)
        if len(name) > MAX_NAME_CHARS:
            raise ArtifactError(f"artifact name is over {MAX_NAME_CHARS} characters", status=400)
        if kind not in ARTIFACT_KINDS:
            raise ArtifactError(f"unknown artifact kind: {kind!r}", status=400)
        if not isinstance(content, (bytes, bytearray, memoryview)):
            raise ArtifactError("artifact content must be bytes", status=400)
        content_bytes = bytes(content)
        if len(content_bytes) > MAX_ARTIFACT_BYTES:
            raise ArtifactError(f"artifact is over the {MAX_ARTIFACT_BYTES}-byte cap", status=400)
        if not isinstance(media_type, str) or not media_type.strip():
            raise ArtifactError("artifact media_type must be a non-empty string", status=400)
        artifact_id = uuid.uuid4()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO artifacts "
                "(id, user_id, agent_id, run_id, name, kind, media_type, size_bytes, content) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (
                    artifact_id,
                    user_id,
                    agent_id,
                    run_id,
                    name,
                    kind,
                    media_type,
                    len(content_bytes),
                    content_bytes,
                ),
            )
            row = cur.fetchone()
            conn.commit()
        return _row_to_artifact(row, with_content=False)

    def list_artifacts(self, user_id: str, agent_id: str, *, limit: int = 100) -> list[dict]:
        """The agent's artifacts, newest first (metadata only).

        Raises :class:`ArtifactError` (403) when the agent is
        not the user's.
        """
        self.require_agent_owner(user_id, agent_id)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, agent_id, run_id, name, kind, media_type, "
                "size_bytes, created_at FROM artifacts "
                "WHERE agent_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
                (agent_id, limit),
            )
            rows = cur.fetchall()
        return [_row_to_artifact(row, with_content=False).to_dict() for row in rows]

    def get_artifact(self, user_id: str, artifact_id: str) -> Artifact:
        """One artifact with its bytes, for the download route.

        Raises :class:`ArtifactError` (404) when the id is
        unknown or belongs to another user's agent. An empty
        id is a caller bug, so it raises ValueError instead.
        """
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("artifact_id must be a non-empty string")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM artifacts WHERE id = %s", (artifact_id,))
            row = cur.fetchone()
        if row is None:
            raise ArtifactError(f"artifact {artifact_id!r} not found", status=404)
        if row["agent_id"] not in user_agent_ids(user_id, dsn=self.dsn):
            raise ArtifactError(f"artifact {artifact_id!r} not found", status=404)
        return _row_to_artifact(row, with_content=True)
