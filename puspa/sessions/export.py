"""Conversation export and import (Kosmos V2, V2-11).

A conversation is one session in the per-profile session store
(``puspa.sessions.store``): its metadata plus every message in
order, with role, text and timestamp. This module writes a
conversation (or all of them) to one portable archive file, and
reads such a file back into a session store.

Archive format (same pattern as the memory archive in
``puspa/memory/importer.py``): a zip file holding
``conversations.jsonl`` (one JSON object per conversation, with
its messages inline), ``lineage.jsonl`` when any compression
lineage edge falls inside the export, and ``manifest.json`` with
the format name, schema version, per-file SHA-256 hashes and
counts. A tampered file is refused before anything is written:
the manifest hash check, the count check and the per-conversation
content hash all have to pass.

Import, in plain words:

1. Validate the whole archive first. A wrong format or schema
   version, a manifest hash mismatch, a count mismatch, a bad
   role or timestamp, or a duplicate id inside the archive
   refuses the whole import and writes nothing.
2. Dry run first: :func:`plan_import_conversations` (and
   :func:`import_conversations` without ``approve=True``) shows
   exactly what would change and writes nothing. Nothing is
   written until the caller approves.
3. Merge by conversation id and content hash. A conversation
   whose id already exists with identical content is a duplicate
   and is skipped, so importing the same archive twice changes
   nothing. A conversation whose id exists with different
   content is a conflict: the whole import is refused, never
   settled silently, and nothing is written.
4. With approval, the new conversations land in one transaction
   (``SessionStore.restore_conversations``), so a failed import
   writes nothing. A parent link whose parent is not in the
   archive and not in the target store is dropped and reported,
   not invented; the same goes for a lineage edge whose endpoint
   is missing.

Honest limits:

- Message row ids are local database ids and are not preserved:
  sequence number, role, text and timestamp are. A restored
  conversation compares equal on those, not on row ids.
- The archive holds plain text. A message that carries an image
  or file reference keeps the reference text only; the file
  itself is not bundled. The session store keeps no attachment
  blobs today, so nothing is lost from what it stores.
- Profile-level model configuration (``model_config``) is not
  part of a conversation and is not exported.
- There is no encryption here: a full memory export is
  encrypted by default, but a conversation archive is a plain
  zip. Do not store it where strangers can read it.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import SessionNotFoundError
from .store import ROLES

__all__ = [
    "ARCHIVE_FORMAT",
    "ARCHIVE_SCHEMA_VERSION",
    "ConversationArchiveError",
    "export_conversations",
    "import_conversations",
    "load_archive",
    "plan_import_conversations",
    "validate_archive",
]

#: Archive format version this module understands.
ARCHIVE_SCHEMA_VERSION = 1

#: ``format`` value a manifest must carry.
ARCHIVE_FORMAT = "puspa-conversation-archive"

#: Largest archive accepted (bytes). Bigger archives are refused.
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024

#: Largest number of conversations accepted in one archive.
MAX_CONVERSATIONS = 10000

#: Largest number of messages accepted in one conversation.
MAX_MESSAGES_PER_CONVERSATION = 10000

#: Longest text accepted for one message (characters).
MAX_TEXT_CHARS = 1000000

#: How many sessions "export all" reads at most. A profile with
#: more sessions than this must export by id instead.
_ALL_SESSIONS_LIMIT = 10000


class ConversationArchiveError(ValueError):
    """An archive failed validation and was refused."""


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _content_hash(record: dict[str, Any]) -> str:
    """SHA-256 of a conversation record, for duplicate detection."""
    return hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()


def _parse_time(value: Any) -> datetime | None:
    """Parse an ISO timestamp, or None when it is missing or bad."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _canonical_record(session: dict[str, Any]) -> dict[str, Any]:
    """The portable shape of one session record (with messages)."""
    return {
        "id": str(session["id"]),
        "title": str(session.get("title") or ""),
        "created_at": str(session["created_at"]),
        "updated_at": str(session["updated_at"]),
        "parent_id": session.get("parent_id"),
        "model": session.get("model"),
        "metadata": dict(session.get("metadata") or {}),
        "messages": [
            {
                "seq": int(message["seq"]),
                "role": str(message["role"]),
                "content": str(message["content"]),
                "created_at": str(message["created_at"]),
            }
            for message in session.get("messages") or []
        ],
    }


def _validate_conversation(row: dict[str, Any], *, index: int) -> None:
    """Validate one archived conversation object (before any write)."""
    where = f"conversations.jsonl row {index}"
    if not isinstance(row, dict):
        raise ConversationArchiveError(f"{where}: row is not an object")
    if not isinstance(row.get("id"), str) or not row["id"]:
        raise ConversationArchiveError(f"{where}: no conversation id")
    if _parse_time(row.get("created_at")) is None:
        raise ConversationArchiveError(f"{where}: bad created_at")
    if _parse_time(row.get("updated_at")) is None:
        raise ConversationArchiveError(f"{where}: bad updated_at")
    messages = row.get("messages")
    if not isinstance(messages, list):
        raise ConversationArchiveError(f"{where}: messages are not a list")
    if len(messages) > MAX_MESSAGES_PER_CONVERSATION:
        raise ConversationArchiveError(f"{where}: too many messages")
    previous_seq = 0
    for message in messages:
        if not isinstance(message, dict):
            raise ConversationArchiveError(f"{where}: a message is not an object")
        if message.get("role") not in ROLES:
            raise ConversationArchiveError(f"{where}: unknown message role {message.get('role')!r}")
        if not isinstance(message.get("content"), str):
            raise ConversationArchiveError(f"{where}: a message has no text content")
        if len(message["content"]) > MAX_TEXT_CHARS:
            raise ConversationArchiveError(f"{where}: a message is over the length cap")
        if _parse_time(message.get("created_at")) is None:
            raise ConversationArchiveError(f"{where}: a message has a bad created_at")
        seq = message.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= previous_seq:
            raise ConversationArchiveError(f"{where}: messages out of order (seq {seq!r})")
        previous_seq = seq
    declared = row.get("content_hash")
    if not isinstance(declared, str) or not declared:
        raise ConversationArchiveError(f"{where}: no content_hash")
    if declared != _content_hash(_canonical_record(row)):
        raise ConversationArchiveError(f"{where}: content_hash does not match the content")


def _validate_edge(edge: dict[str, Any], *, index: int) -> None:
    where = f"lineage.jsonl row {index}"
    if not isinstance(edge, dict):
        raise ConversationArchiveError(f"{where}: row is not an object")
    for key in ("parent_session_id", "child_session_id"):
        if not isinstance(edge.get(key), str) or not edge[key]:
            raise ConversationArchiveError(f"{where}: no {key}")
    if _parse_time(edge.get("created_at")) is None:
        raise ConversationArchiveError(f"{where}: bad created_at")


def _read_jsonl(data: bytes, name: str) -> list[dict[str, Any]]:
    """Parse one ``.jsonl`` file into a list of objects."""
    text = data.decode("utf-8")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConversationArchiveError(f"{name} line {line_number}: not valid JSON") from exc
        if not isinstance(row, dict):
            raise ConversationArchiveError(f"{name} line {line_number}: row is not an object")
        rows.append(row)
    return rows


def load_archive(path: str | Path) -> dict[str, bytes]:
    """Read a conversation archive (directory or zip) into name-to-bytes.

    Raises :class:`ConversationArchiveError` when the path does
    not exist, is not a directory or zip file, or is over the size cap.
    """
    location = Path(path)
    if not location.exists():
        raise ConversationArchiveError(f"archive not found: {path}")
    if location.is_dir():
        files: dict[str, bytes] = {}
        total = 0
        for child in sorted(location.rglob("*")):
            if not child.is_file():
                continue
            rel = child.relative_to(location).as_posix()
            data = child.read_bytes()
            total += len(data)
            if total > MAX_ARCHIVE_BYTES:
                raise ConversationArchiveError("archive is over the size cap")
            files[rel] = data
        return files
    if location.suffix.lower() != ".zip":
        raise ConversationArchiveError(f"archive must be a directory or a zip file: {path}")
    if location.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ConversationArchiveError("archive is over the size cap")
    files = {}
    with zipfile.ZipFile(location) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            files[info.filename] = archive.read(info.filename)
    if sum(len(data) for data in files.values()) > MAX_ARCHIVE_BYTES:
        raise ConversationArchiveError("archive is over the size cap")
    return files


def validate_archive(files: dict[str, bytes]) -> dict[str, Any]:
    """Validate a loaded archive and return its parsed contents.

    Checks the manifest (format, schema version, per-file sha256
    hashes, counts), then every conversation and lineage edge.
    Returns ``{"manifest", "conversations", "lineage", "counts"}``.
    Any failure raises :class:`ConversationArchiveError` and
    imports nothing.
    """
    if "manifest.json" not in files:
        raise ConversationArchiveError("archive has no manifest.json")
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConversationArchiveError("manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ConversationArchiveError("manifest.json is not an object")
    if manifest.get("format") != ARCHIVE_FORMAT:
        raise ConversationArchiveError(f"unknown archive format: {manifest.get('format')!r}")
    if manifest.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
        raise ConversationArchiveError(
            f"unsupported schema version: {manifest.get('schema_version')!r}"
        )
    listed = manifest.get("files")
    if not isinstance(listed, dict):
        raise ConversationArchiveError("manifest has no files hash list")
    for name, digest in listed.items():
        if name not in files:
            raise ConversationArchiveError(f"manifest lists missing file: {name}")
        actual = hashlib.sha256(files[name]).hexdigest()
        if actual != digest:
            raise ConversationArchiveError(f"manifest hash mismatch for file: {name}")
    if "conversations.jsonl" not in files:
        raise ConversationArchiveError("archive has no conversations.jsonl")
    conversations = _read_jsonl(files["conversations.jsonl"], "conversations.jsonl")
    if len(conversations) > MAX_CONVERSATIONS:
        raise ConversationArchiveError("archive has too many conversations")
    seen_ids: set[str] = set()
    for index, row in enumerate(conversations):
        _validate_conversation(row, index=index)
        if row["id"] in seen_ids:
            raise ConversationArchiveError(f"archive holds conversation {row['id']!r} twice")
        seen_ids.add(str(row["id"]))
    lineage: list[dict[str, Any]] = []
    if "lineage.jsonl" in files:
        lineage = _read_jsonl(files["lineage.jsonl"], "lineage.jsonl")
        if len(lineage) > MAX_CONVERSATIONS:
            raise ConversationArchiveError("archive has too many lineage edges")
        for index, edge in enumerate(lineage):
            _validate_edge(edge, index=index)
    counts = {
        "conversations": len(conversations),
        "messages": sum(len(row["messages"]) for row in conversations),
        "lineage": len(lineage),
    }
    declared = manifest.get("counts")
    if isinstance(declared, dict):
        for key, found in counts.items():
            if key in declared and int(declared[key]) != found:
                raise ConversationArchiveError(
                    f"manifest count mismatch for {key}: declared {declared[key]}, found {found}"
                )
    known = {"manifest.json", "conversations.jsonl", "lineage.jsonl"}
    ignored = sorted(name for name in files if name not in known)
    return {
        "manifest": manifest,
        "conversations": conversations,
        "lineage": lineage,
        "counts": counts,
        "ignored_files": ignored,
    }


def export_conversations(
    reader, *, conversation_ids: list[str] | None = None, out: str | Path
) -> dict[str, Any]:
    """Export conversations from ``reader`` to one zip archive.

    ``reader`` is a read-only session store or a gateway (both
    expose ``get_session`` and ``list_sessions``). With
    ``conversation_ids`` given, only those conversations are
    exported; without it, every session in the profile is
    exported. ``out`` is the zip file to write (parent
    directories are created). Returns a result dict with the
    path, the exported conversation ids and the counts.
    """
    if conversation_ids is not None:
        records = []
        for conversation_id in conversation_ids:
            try:
                records.append(reader.get_session(str(conversation_id)))
            except SessionNotFoundError as exc:
                raise ConversationArchiveError(
                    f"no conversation {conversation_id!r} in this profile"
                ) from exc
    else:
        listed = reader.list_sessions(limit=_ALL_SESSIONS_LIMIT)
        if len(listed) == _ALL_SESSIONS_LIMIT:
            raise ConversationArchiveError(
                f"profile holds at least {_ALL_SESSIONS_LIMIT} conversations; "
                "export by id instead of exporting all"
            )
        records = [reader.get_session(record["id"]) for record in listed]
    canonical = [_canonical_record(record) for record in records]
    exported_ids = {record["id"] for record in canonical}
    lineage: list[dict[str, Any]] = []
    for record in canonical:
        for edge in reader.children(record["id"]):
            if edge["child_session_id"] in exported_ids:
                lineage.append(
                    {
                        "parent_session_id": record["id"],
                        "child_session_id": str(edge["child_session_id"]),
                        "kind": str(edge.get("kind") or "compression"),
                        "reason": edge.get("reason"),
                        "created_at": str(edge["created_at"]),
                    }
                )
    payloads: dict[str, bytes] = {}
    conversations_text = "".join(
        _canonical_json({**record, "content_hash": _content_hash(record)}) + "\n"
        for record in canonical
    )
    payloads["conversations.jsonl"] = conversations_text.encode("utf-8")
    if lineage:
        lineage_text = "".join(_canonical_json(edge) + "\n" for edge in lineage)
        payloads["lineage.jsonl"] = lineage_text.encode("utf-8")
    counts = {
        "conversations": len(canonical),
        "messages": sum(len(record["messages"]) for record in canonical),
        "lineage": len(lineage),
    }
    manifest = {
        "format": ARCHIVE_FORMAT,
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "exported_at": _now().isoformat(),
        "counts": counts,
        "conversations": [
            {
                "id": record["id"],
                "title": record["title"],
                "messages": len(record["messages"]),
                "content_hash": _content_hash(record),
            }
            for record in canonical
        ],
        "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in sorted(payloads.items())
        },
    }
    payloads["manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(payloads):
            archive.writestr(name, payloads[name])
    return {
        "status": "exported",
        "path": str(out_path),
        "conversations": [record["id"] for record in canonical],
        "counts": counts,
        "exported_at": manifest["exported_at"],
    }


def _effective_record(
    archived: dict[str, Any], *, existing_ids: set[str], archived_ids: set[str]
) -> tuple[dict[str, Any], bool]:
    """The record as it would be stored, with unresolvable parents dropped.

    Returns ``(record, dropped_parent)``. The content hash is
    computed on the effective record, so a re-import of the same
    archive still recognises the stored conversation as a
    duplicate even when its parent link was dropped.
    """
    record = _canonical_record(archived)
    parent = record.get("parent_id")
    dropped = False
    if parent is not None and parent not in existing_ids and parent not in archived_ids:
        record["parent_id"] = None
        dropped = True
    return record, dropped


def _load_and_plan(path: str | Path, reader) -> dict[str, Any]:
    """Validate an archive and plan its import against ``reader``.

    Returns the validated archive plus the plan (per-conversation
    dispositions, the effective records to create, the lineage
    edges to restore and the ones to drop). Writes nothing.
    """
    validated = validate_archive(load_archive(path))
    archived = validated["conversations"]
    archived_ids = {str(row["id"]) for row in archived}
    existing: dict[str, dict[str, Any]] = {}
    for row in archived:
        try:
            existing[str(row["id"])] = _canonical_record(reader.get_session(str(row["id"])))
        except SessionNotFoundError:
            continue
    existing_ids = set(existing)
    items: list[dict[str, Any]] = []
    counts = {"create": 0, "duplicate": 0, "conflict": 0}
    to_create: list[dict[str, Any]] = []
    dropped_parents: list[dict[str, Any]] = []
    for row in archived:
        record, dropped = _effective_record(
            row, existing_ids=existing_ids, archived_ids=archived_ids
        )
        stored = existing.get(record["id"])
        if stored is None:
            disposition, reason = "create", ""
            counts["create"] += 1
            to_create.append(record)
            if dropped:
                dropped_parents.append({"id": record["id"], "parent_id": row.get("parent_id")})
        elif _content_hash(stored) == _content_hash(record):
            disposition, reason = "duplicate", "already stored with identical content"
            counts["duplicate"] += 1
        else:
            disposition = "conflict"
            reason = "a conversation with this id already exists with different content"
            counts["conflict"] += 1
        items.append(
            {
                "id": record["id"],
                "title": record["title"],
                "messages": len(record["messages"]),
                "disposition": disposition,
                "reason": reason,
                "dropped_parent": dropped,
            }
        )
    created_ids = existing_ids | {record["id"] for record in to_create}
    lineage_ok: list[dict[str, Any]] = []
    lineage_dropped: list[dict[str, Any]] = []
    for edge in validated["lineage"]:
        if edge["parent_session_id"] in created_ids and edge["child_session_id"] in created_ids:
            lineage_ok.append(edge)
        else:
            lineage_dropped.append(edge)
    counts["lineage_restore"] = len(lineage_ok)
    counts["lineage_drop"] = len(lineage_dropped)
    counts["total"] = len(items)
    counts["would_change"] = counts["create"]
    to_create.sort(key=lambda record: (record["created_at"], record["id"]))
    return {
        "validated": validated,
        "items": items,
        "counts": counts,
        "to_create": to_create,
        "dropped_parents": dropped_parents,
        "lineage_ok": lineage_ok,
        "lineage_dropped": lineage_dropped,
    }


def plan_import_conversations(path: str | Path, *, reader) -> dict[str, Any]:
    """Dry-run plan for importing an archive: what would change.

    Writes nothing. Each archived conversation is labelled
    ``create``, ``duplicate`` or ``conflict``; lineage edges are
    labelled restorable or dropped. Returns the plan with
    per-item dispositions and counts.
    """
    planned = _load_and_plan(path, reader)
    return {
        "status": "plan",
        "counts": planned["counts"],
        "items": planned["items"],
        "dropped_parents": planned["dropped_parents"],
        "lineage_dropped": planned["lineage_dropped"],
        "would_change": planned["counts"]["would_change"] > 0,
    }


def import_conversations(
    path: str | Path, *, writer, approve: bool = False, dry_run: bool = False
) -> dict[str, Any]:
    """Import conversations from an archive into ``writer``.

    ``writer`` is a gateway (or any store with ``get_session``
    and ``restore_conversations``). Without ``approve=True`` (or
    with ``dry_run=True``) this only plans: it validates and
    reports dispositions, and returns
    ``{"status": "awaiting_approval" or "dry_run", ...}`` having
    written nothing. With approval it restores the new
    conversations in one transaction. Any conflict refuses the
    whole import and writes nothing.
    """
    planned = _load_and_plan(path, writer)
    counts = planned["counts"]
    if dry_run or not approve:
        return {
            "status": "dry_run" if dry_run else "awaiting_approval",
            "counts": counts,
            "items": planned["items"],
            "dropped_parents": planned["dropped_parents"],
            "lineage_dropped": planned["lineage_dropped"],
            "would_change": counts["would_change"] > 0,
        }
    if counts["conflict"] > 0:
        return {
            "status": "refused",
            "counts": counts,
            "items": planned["items"],
            "dropped_parents": planned["dropped_parents"],
            "lineage_dropped": planned["lineage_dropped"],
            "would_change": False,
            "reason": "one or more conversations conflict with stored content; nothing was written",
        }
    created = writer.restore_conversations(planned["to_create"], planned["lineage_ok"])
    return {
        "status": "imported",
        "created": created,
        "duplicates": [
            item["id"] for item in planned["items"] if item["disposition"] == "duplicate"
        ],
        "counts": counts,
        "dropped_parents": planned["dropped_parents"],
        "lineage_dropped": planned["lineage_dropped"],
        "would_change": False,
    }
