"""Portable memory export (V2-09): one archive covering every memory layer.

One command, API call, or PWA button produces a portable archive
for one user:

- ``MEMORY.md``: the nightly durable memory document;
- ``events.jsonl``: ledger entries with cause, outcome, valid time
  and known time (the hash chain, so the archive is auditable);
- ``graph.jsonl``: nodes and edges rebuilt deterministically from
  the episodes (no extra storage, same as ``episodes_to_graph``);
- ``people.jsonl`` / ``values.jsonl``: person models, preferences
  and standing goals, split out of the profile facts;
- ``skills/procedures.jsonl``: learned procedures;
- ``causal.jsonl`` / ``ignorance.jsonl``: causal hypotheses with
  strength and evidence, and the known unknowns;
- ``manifest.json``: format and Puspa versions, embedding model id
  and dimensions, per-section counts, sha256 of every file, and
  the ledger hash-chain tip per task.

Rules (design doc ``docs/kosmos-brain-v2.md``, section "Memory
import and export"):

- Embeddings are left out: only the model id and dimensions go
  in the manifest. Vectors are recomputed on import (V2-10).
- Secrets are never exported. Vault entries are not in the
  archive at all (not even their names: this archive is memory,
  not an account dump), and every exported string passes through
  ``puspa.memory.trust.redact_secrets`` first, so a secret a user
  pasted into a chat line leaves as a redaction marker.
- A full export is encrypted by default. The key comes from a
  passphrase with PBKDF2-HMAC-SHA256; the ZIP bytes are sealed
  with Fernet. The salt travels in a file header so the archive
  reopens with the same passphrase. No passphrase ever travels
  as a command-line argument.
- Each export is itself a ledger event: ``evidence_recorded``
  (the closed set of 33 types stays closed) on task
  ``memory-export-<user_id>``, carrying counts and the archive
  hash only, never memory content.
- Scope can be full, chosen layers, a date range, one person,
  or one project. A date range filters the time-ordered
  sections (events and graph episodes); snapshots (MEMORY.md,
  people, values, skills, causal, ignorance) are exported as
  they stand. A person keeps only that person's record in
  ``people.jsonl``. A project keeps the episodes and ledger events
  of one task, named by its task id (a UUID string: episodes store
  task ids as UUIDs, ledger events store the same string as text);
  the snapshot sections (MEMORY.md, people, values, skills, causal,
  ignorance) are personal, not per-project, so a project scope
  leaves them as they stand.

The archive is built to be re-importable: ``verify_archive``
reads an archive back and checks that it unpacks, that every
manifest hash matches its file, and that every manifest count
matches its section. The full export-wipe-import round trip
(the amnesia test, 20 of 20) needs the import side (V2-10) and
is proven there; this module proves the archive is complete
and structurally sound here.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.db import errors as pg_errors
from puspa.memory.schema import resolve_dsn
from puspa.memory.trust import redact_secrets

if TYPE_CHECKING:
    from puspa.ledger import Ledger


def _puspa_version() -> str:
    """Installed package version, falling back to the pyproject version."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("puspa")
    except (ImportError, PackageNotFoundError):
        return "0.1.0"


__all__ = [
    "ARCHIVE_FORMAT",
    "ARCHIVE_VERSION",
    "EXPORT_EVENT_TYPE",
    "ExportScope",
    "MemoryArchive",
    "MemoryExport",
    "build_archive_bytes",
    "build_memory_archive",
    "decrypt_archive_bytes",
    "derive_key",
    "encrypt_archive_bytes",
    "export_archive_bytes",
    "export_memory",
    "memory_export_task_id",
    "record_export_event",
    "verify_archive",
    "verify_archive_bytes",
]

#: Archive format name, written into the manifest and the ledger event.
ARCHIVE_FORMAT = "puspa-memory-archive"

#: Archive format version. Bump when the sections change.
ARCHIVE_VERSION = 1

#: Closed ledger event type for exports (no new type added).
EXPORT_EVENT_TYPE = "evidence_recorded"

#: Magic header of an encrypted archive file, followed by the salt.
HEADER_MAGIC = b"PUSPA-MEMORY-ARCHIVE-V1\n"

#: Salt bytes stored in the encrypted file header.
SALT_BYTES = 16

#: PBKDF2 iterations for the passphrase-derived key.
PBKDF2_ITERATIONS = 600_000

#: ZIP entries carry a fixed timestamp, so the same memory
#: exports to the same bytes (reproducible archives).
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)

#: Section name -> file inside the ZIP.
SECTION_FILES = {
    "memory": "MEMORY.md",
    "events": "events.jsonl",
    "graph": "graph.jsonl",
    "people": "people.jsonl",
    "values": "values.jsonl",
    "skills": "skills/procedures.jsonl",
    "causal": "causal.jsonl",
    "ignorance": "ignorance.jsonl",
}

#: What is deliberately left out of every archive (also written
#: into the manifest, so the exclusion is visible, not silent).
EXCLUDED = (
    "vault entries: secret values are never exported, and vault names are not memory",
    "password_hash: the user's password hash is never exported",
    "session token hashes: never exported",
    "slice-grant token_ref values: permission-token references are never exported",
    "episodic embedding vectors: recomputable derivatives, excluded for size",
    "other users' data: every query filters on this user's id",
)


def memory_export_task_id(user_id: str) -> str:
    """Ledger task holding one user's memory-export events."""
    return f"memory-export-{user_id}"


@dataclass
class ExportScope:
    """What one export covers.

    ``layers`` is a subset of the section names in
    ``SECTION_FILES`` (``None`` means all of them). ``date_from``
    / ``date_to`` bound the time-ordered sections only. ``person``
    keeps one person record in ``people.jsonl``. ``project`` keeps
    the episodes and ledger events of one task, named by its task
    id (a UUID string).
    """

    layers: tuple[str, ...] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    person: str | None = None
    project: str | None = None

    def __post_init__(self) -> None:
        if self.layers is not None:
            unknown = sorted(set(self.layers) - set(SECTION_FILES))
            if unknown:
                raise ValueError(f"unknown export layers: {unknown}")
        for name, value in (("date_from", self.date_from), ("date_to", self.date_to)):
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_to < self.date_from
        ):
            raise ValueError("date_to must not be before date_from")

    def includes(self, section: str) -> bool:
        """Whether ``section`` is part of this export."""
        return self.layers is None or section in self.layers

    def describe(self) -> dict:
        """The scope as plain data (manifest + ledger event)."""
        return {
            "layers": list(self.layers) if self.layers is not None else "full",
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "person": self.person,
            "project": self.project,
        }


@dataclass
class MemoryArchive:
    """One built archive: the ZIP bytes plus its manifest."""

    zip_bytes: bytes
    manifest: dict
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def archive_sha256(self) -> str:
        return hashlib.sha256(self.zip_bytes).hexdigest()


@dataclass
class MemoryExport:
    """One finished export: where it went and what is in it."""

    path: Path
    user_id: str
    exported_at: datetime
    counts: dict[str, int] = field(default_factory=dict)
    archive_sha256: str = ""
    encrypted: bool = True
    manifest: dict = field(default_factory=dict)


# -- JSON helpers ------------------------------------------------------


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
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    return value


def _jsonl(rows) -> bytes:
    """Rows as JSON lines (one object per line, redacted)."""
    text = "".join(
        json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    return text.encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- encryption --------------------------------------------------------


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a Fernet key from a passphrase and salt (PBKDF2)."""
    if not isinstance(passphrase, str) or not passphrase:
        raise ValueError("passphrase must be a non-empty string")
    if not isinstance(salt, bytes) or len(salt) != SALT_BYTES:
        raise ValueError(f"salt must be {SALT_BYTES} bytes")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_archive_bytes(zip_bytes: bytes, passphrase: str) -> bytes:
    """Seal ZIP bytes: magic header + salt + Fernet ciphertext."""
    salt = os.urandom(SALT_BYTES)
    token = Fernet(derive_key(passphrase, salt)).encrypt(zip_bytes)
    return HEADER_MAGIC + salt + token


def decrypt_archive_bytes(data: bytes, passphrase: str | None = None) -> bytes:
    """Open archive bytes: decrypt when sealed, else return as-is.

    Raises ValueError on a wrong passphrase, a truncated header,
    or corrupted ciphertext. A plain (unencrypted) ZIP passes
    through untouched.
    """
    if not isinstance(data, bytes) or not data:
        raise ValueError("archive bytes must not be empty")
    if not data.startswith(HEADER_MAGIC):
        return data
    if passphrase is None:
        raise ValueError("this archive is encrypted: a passphrase is required")
    rest = data[len(HEADER_MAGIC) :]
    if len(rest) < SALT_BYTES + 1:
        raise ValueError("encrypted archive is truncated")
    salt, token = rest[:SALT_BYTES], rest[SALT_BYTES:]
    try:
        return Fernet(derive_key(passphrase, salt)).decrypt(token)
    except InvalidToken as exc:
        raise ValueError("wrong passphrase or corrupted archive") from exc


# -- archive build (pure: no database) ---------------------------------


def build_archive_bytes(sections: dict[str, bytes], manifest_base: dict) -> tuple[bytes, dict]:
    """Pack section bytes into a ZIP and build its manifest.

    ``sections`` maps ZIP file names to their bytes (manifest
    excluded: it is built here). Returns ``(zip_bytes, manifest)``.
    The manifest holds the sha256 and byte size of every file plus
    the caller-supplied base fields (format, version, counts and
    so on). ZIP entry timestamps are fixed, so identical sections
    produce identical bytes.
    """
    if not sections:
        raise ValueError("at least one section is required")
    files = {name: {"sha256": _sha256(data), "bytes": len(data)} for name, data in sections.items()}
    manifest = dict(manifest_base)
    manifest["files"] = files
    manifest_bytes = (
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(sections):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, sections[name])
        info = zipfile.ZipInfo("manifest.json", date_time=_ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, manifest_bytes)
    return buffer.getvalue(), manifest


def verify_archive_bytes(data: bytes, passphrase: str | None = None) -> dict:
    """Read an archive back and check it end to end.

    Decrypts when sealed, then checks that the ZIP unpacks, that
    ``manifest.json`` exists and parses, that every file it lists
    is present with a matching sha256 and byte size, and that
    every manifest count matches its section. Returns the
    manifest. Raises ValueError on any failure.
    """
    zip_bytes = decrypt_archive_bytes(data, passphrase)
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("archive is not a valid ZIP file") from exc
    with archive:
        names = set(archive.namelist())
        if "manifest.json" not in names:
            raise ValueError("archive has no manifest.json")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("manifest.json is not valid JSON") from exc
        if manifest.get("format") != ARCHIVE_FORMAT:
            raise ValueError(f"unknown archive format: {manifest.get('format')!r}")
        if manifest.get("version") != ARCHIVE_VERSION:
            raise ValueError(f"unsupported archive version: {manifest.get('version')!r}")
        files = manifest.get("files") or {}
        counts = manifest.get("counts") or {}
        for name, info in sorted(files.items()):
            if name not in names:
                raise ValueError(f"manifest lists {name!r} but the archive lacks it")
            content = archive.read(name)
            if _sha256(content) != info.get("sha256"):
                raise ValueError(f"hash mismatch for {name!r}")
            if len(content) != info.get("bytes"):
                raise ValueError(f"size mismatch for {name!r}")
        for section, filename in SECTION_FILES.items():
            if filename not in files:
                continue
            content = archive.read(filename)
            if filename.endswith(".jsonl"):
                line_count = len(
                    [line for line in content.decode("utf-8").splitlines() if line.strip()]
                )
            else:
                line_count = 1
            if counts.get(section, line_count) != line_count:
                raise ValueError(
                    f"count mismatch for {section!r}: manifest says "
                    f"{counts.get(section)}, file has {line_count}"
                )
    return manifest


def verify_archive(path: str | Path, passphrase: str | None = None) -> dict:
    """Read back an archive file and verify it. Returns the manifest."""
    return verify_archive_bytes(Path(path).read_bytes(), passphrase)


# -- collection (database) ---------------------------------------------


def _connect(dsn: str | None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


def _agent_ids(user_id: str, conn) -> list[str]:
    """Agent ids owned by this user (the ledger has no user_id)."""
    with conn.cursor() as cur:
        cur.execute("SELECT agent_id FROM agents WHERE user_id = %s", (user_id,))
        return [row["agent_id"] for row in cur.fetchall()]


def _ledger_events(agent_ids: list[str], conn, scope: ExportScope) -> list[dict]:
    """Ledger events for these agents, insertion order (hash chain order)."""
    if not agent_ids:
        return []
    query = (
        "SELECT * FROM events WHERE agent_id = ANY(%s)"
        " AND (%s::timestamptz IS NULL OR created_at >= %s::timestamptz)"
        " AND (%s::timestamptz IS NULL OR created_at < %s::timestamptz)"
    )
    params: list = [
        agent_ids,
        scope.date_from,
        scope.date_from,
        scope.date_to,
        scope.date_to,
    ]
    if scope.project is not None:
        query += " AND task_id = %s"
        params.append(scope.project)
    query += " ORDER BY created_at ASC, id ASC"
    with conn.cursor() as cur:
        cur.execute(query, params)
        return [_jsonable(dict(row)) for row in cur.fetchall()]


def _ledger_tips(events: list[dict]) -> dict[str, str]:
    """Hash-chain tip per task: the last event hash of each task."""
    tips: dict[str, str] = {}
    for event in events:
        task_id = event.get("task_id")
        event_hash = event.get("hash")
        if task_id and event_hash:
            tips[str(task_id)] = str(event_hash)
    return tips


def _memory_md(user_id: str, dsn: str | None) -> str:
    """The stored MEMORY.md, rebuilt first when none is stored."""
    from puspa.memory import durable

    document = durable.read_memory(user_id, dsn=dsn)
    if document is not None:
        return str(document["content"])
    return str(durable.rebuild_memory(user_id, dsn=dsn)["content"])


def _facts_split(user_id: str, scope: ExportScope, dsn: str | None):
    """Profile facts split into people and values, scope-filtered."""
    from puspa.memory import profile

    facts = [_jsonable(f) for f in profile.list_facts(user_id, include_history=True, dsn=dsn)]
    people = [f for f in facts if str(f.get("key") or "").startswith("person:")]
    values = [
        f for f in facts if str(f.get("key") or "").startswith(("preference:", "standing_goal"))
    ]
    if scope.person is not None:
        wanted = f"person:{scope.person}"
        people = [f for f in people if str(f.get("key")) == wanted]
    return people, values


def _episodes(user_id: str, scope: ExportScope, dsn: str | None) -> list[dict]:
    """The user's episodes, date- and project-filtered."""
    from puspa.memory import episodic

    rows = [_jsonable(e) for e in episodic.list_episodes(user_id, limit=10000, dsn=dsn)]
    kept = []
    for row in rows:
        created = row.get("created_at")
        if (
            scope.date_from is not None
            and created is not None
            and _parse_time(created) < scope.date_from
        ):
            continue
        if (
            scope.date_to is not None
            and created is not None
            and _parse_time(created) >= scope.date_to
        ):
            continue
        if scope.project is not None and row.get("task_id") != scope.project:
            continue
        kept.append(row)
    return kept


def _parse_time(value):
    """An ISO-8601 string (or datetime) as an aware datetime."""
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _graph_lines(episodes: list[dict]) -> list[dict]:
    """Graph nodes and edges rebuilt from the episodes.

    Deterministic: episodes become documents in ``episodes_to_graph``,
    then nodes and edges are read back sorted by name, so the same
    episodes always produce the same lines.
    """
    from puspa.memory import episodic

    graph = episodic.episodes_to_graph(episodes)
    lines: list[dict] = []
    for name in sorted(graph.nodes):
        node = graph.nodes[name]
        lines.append(
            {
                "type": "node",
                "name": node.name,
                "kind": node.kind,
                "description": node.description,
                "source_ids": sorted(str(s) for s in node.source_ids),
            }
        )
    for key in sorted(graph.edges, key=lambda k: (str(k[0]), str(k[1]), str(k[2]))):
        edge = graph.edges[key]
        lines.append(
            {
                "type": "edge",
                "subject": edge.subject,
                "relation": edge.relation,
                "object": edge.object,
                "source_ids": sorted(str(s) for s in edge.source_ids),
            }
        )
    return [_jsonable(line) for line in lines]


def _procedures(user_id: str, dsn: str | None) -> list[dict]:
    """The user's learned procedures."""
    from puspa.memory import procedural

    return [_jsonable(p) for p in procedural.list_procedures(user_id, dsn=dsn)]


def _causal(user_id: str, agent_ids: list[str], dsn: str | None) -> list[dict]:
    """Causal hypotheses for the user's agents (empty when the
    world tables do not exist yet on this database)."""
    from puspa.world.store import load_causal_ledger

    rows: list[dict] = []
    for agent_id in agent_ids:
        try:
            ledger = load_causal_ledger(user_id, agent_id, dsn=dsn)
        except pg_errors.UndefinedTable:
            return []
        if ledger is None:
            continue
        rows.extend(_jsonable(h) for h in ledger.to_dict()["hypotheses"])
    rows.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")))
    return rows


def _ignorance(user_id: str, agent_ids: list[str], dsn: str | None) -> list[dict]:
    """Known unknowns for the user's agents (empty when the world
    tables do not exist yet on this database)."""
    from puspa.world.store import load_world_model

    rows: list[dict] = []
    for agent_id in agent_ids:
        try:
            model = load_world_model(user_id, agent_id, dsn=dsn)
        except pg_errors.UndefinedTable:
            return []
        if model is None:
            continue
        rows.extend(_jsonable(area.to_dict()) for area in model.ignorance_map())
    rows.sort(key=lambda row: str(row.get("key") or ""))
    return rows


def _embedding_info(dsn: str | None) -> dict:
    """Embedding model id + dimensions (vectors are never exported)."""
    try:
        from puspa.retrieval.embedding_health import get_embedding_state

        state = get_embedding_state(dsn)
        return {
            "model_id": str(state.get("active_embedder_id") or "hash"),
            "dimension": int(state.get("active_dimension") or 384),
        }
    except Exception:
        return {"model_id": "hash", "dimension": 384}


def build_memory_archive(
    user_id: str,
    scope: ExportScope | None = None,
    *,
    dsn: str | None = None,
    encrypted: bool = True,
) -> MemoryArchive:
    """Collect one user's memory layers into an archive (ZIP bytes).

    Raises ValueError for an empty user id or an unknown user.
    The returned ZIP bytes are unencrypted; encryption happens
    in :func:`export_memory` / :func:`export_archive_bytes`.
    ``encrypted`` is recorded in the manifest so a reader knows
    how the file was sealed; the archive hash itself is added by
    the caller (it covers the sealed file bytes).
    """
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    scope = scope or ExportScope()
    sections: dict[str, bytes] = {}
    counts: dict[str, int] = {}
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email FROM users WHERE id = %s", (user_id,))
            user_row = cur.fetchone()
        if user_row is None:
            raise ValueError(f"unknown user: {user_id!r}")
        agent_ids = _agent_ids(user_id, conn)
        events = _ledger_events(agent_ids, conn, scope) if scope.includes("events") else []
    if scope.includes("memory"):
        text = _memory_md(user_id, dsn)
        sections[SECTION_FILES["memory"]] = redact_secrets(text).encode("utf-8")
        counts["memory"] = 1
    if scope.includes("events"):
        sections[SECTION_FILES["events"]] = _jsonl(events)
        counts["events"] = len(events)
    episodes = _episodes(user_id, scope, dsn) if scope.includes("graph") else []
    if scope.includes("graph"):
        lines = _graph_lines(episodes)
        sections[SECTION_FILES["graph"]] = _jsonl(lines)
        counts["graph"] = len(lines)
    people: list[dict] = []
    values: list[dict] = []
    if scope.includes("people") or scope.includes("values"):
        people, values = _facts_split(user_id, scope, dsn)
    if scope.includes("people"):
        sections[SECTION_FILES["people"]] = _jsonl(people)
        counts["people"] = len(people)
    if scope.includes("values"):
        sections[SECTION_FILES["values"]] = _jsonl(values)
        counts["values"] = len(values)
    if scope.includes("skills"):
        procedures = _procedures(user_id, dsn)
        sections[SECTION_FILES["skills"]] = _jsonl(procedures)
        counts["skills"] = len(procedures)
    if scope.includes("causal"):
        hypotheses = _causal(user_id, agent_ids, dsn)
        sections[SECTION_FILES["causal"]] = _jsonl(hypotheses)
        counts["causal"] = len(hypotheses)
    if scope.includes("ignorance"):
        areas = _ignorance(user_id, agent_ids, dsn)
        sections[SECTION_FILES["ignorance"]] = _jsonl(areas)
        counts["ignorance"] = len(areas)
    manifest_base = {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "puspa_version": _puspa_version(),
        "user_id": str(user_id),
        "exported_at": datetime.now(UTC).isoformat(),
        "scope": scope.describe(),
        "embedding": _embedding_info(dsn),
        "counts": counts,
        "ledger_tips": _ledger_tips(events),
        "excluded": list(EXCLUDED),
        "encrypted": encrypted,
    }
    zip_bytes, manifest = build_archive_bytes(sections, manifest_base)
    return MemoryArchive(zip_bytes=zip_bytes, manifest=manifest, counts=counts)


# -- ledger event + file output ------------------------------------------


def record_export_event(
    user_id: str,
    manifest: dict,
    *,
    dsn: str | None = None,
    ledger: Ledger | None = None,
) -> dict:
    """Audit one export as a ledger event (counts + hash only).

    The content never carries memory content: only the format,
    scope, per-section counts, the archive hash, and whether the
    file is encrypted. Returns the stored event row.

    The Ledger import is lazy: importing it at module level
    would circle back through ``puspa.ledger.export`` into
    ``puspa.memory``.
    """
    from puspa.ledger import Ledger

    content = {
        "kind": "memory_export",
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "scope": manifest.get("scope"),
        "counts": manifest.get("counts"),
        "archive_sha256": manifest.get("archive_sha256"),
        "encrypted": manifest.get("encrypted"),
    }
    ledger = ledger or Ledger(resolve_dsn(dsn))
    return ledger.append_event(
        task_id=memory_export_task_id(str(user_id)),
        agent_id="puspa",
        event_type=EXPORT_EVENT_TYPE,
        content=content,
    )


def export_archive_bytes(
    user_id: str,
    passphrase: str | None = None,
    scope: ExportScope | None = None,
    *,
    dsn: str | None = None,
    encrypted: bool = True,
) -> tuple[bytes, dict]:
    """Build an archive and record its ledger event, in memory.

    Returns ``(file_bytes, manifest)``. ``file_bytes`` is the
    sealed (encrypted) file when ``encrypted`` is true, else the
    raw ZIP. Raises ValueError when encryption is on but no
    passphrase was given. The manifest records the archive hash
    of the file bytes that are returned.
    """
    if encrypted and not passphrase:
        raise ValueError("a passphrase is required: full exports are encrypted by default")
    archive = build_memory_archive(user_id, scope, dsn=dsn, encrypted=encrypted)
    if encrypted:
        assert passphrase is not None
        file_bytes = encrypt_archive_bytes(archive.zip_bytes, passphrase)
    else:
        file_bytes = archive.zip_bytes
    manifest = dict(archive.manifest)
    manifest["encrypted"] = encrypted
    manifest["archive_sha256"] = _sha256(file_bytes)
    record_export_event(user_id, manifest, dsn=dsn)
    return file_bytes, manifest


def export_memory(
    user_id: str,
    path: str | Path,
    passphrase: str | None = None,
    scope: ExportScope | None = None,
    *,
    dsn: str | None = None,
    encrypted: bool = True,
) -> MemoryExport:
    """Write one user's memory archive to ``path``.

    Returns a :class:`MemoryExport` with the per-section counts
    and the archive hash. The file is written atomically (temp
    file + rename) so a failed export never leaves a half-written
    file behind. The export is recorded as a ledger event (see
    :func:`record_export_event`).
    """
    path = Path(path)
    file_bytes, manifest = export_archive_bytes(
        user_id, passphrase, scope, dsn=dsn, encrypted=encrypted
    )
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(file_bytes)
    tmp_path.replace(path)
    return MemoryExport(
        path=path,
        user_id=str(user_id),
        exported_at=datetime.fromisoformat(manifest["exported_at"]),
        counts=dict(manifest["counts"]),
        archive_sha256=manifest["archive_sha256"],
        encrypted=bool(manifest["encrypted"]),
        manifest=manifest,
    )
