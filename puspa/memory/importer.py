"""Memory import (Kosmos V2, memory export and import).

Import memory from three sources into the durable memory layers:

- a Kosmos archive: a directory or zip file holding ``MEMORY.md``,
  ``events.jsonl``, ``graph.jsonl``, ``people.jsonl``,
  ``values.jsonl``, ``skills/``, ``causal.jsonl``,
  ``ignorance.jsonl`` and ``manifest.json`` (the format the memory
  export produces, see ``docs/kosmos-brain-v2.md``);
- a hand-edited ``MEMORY.md`` file: the user's own readable memory,
  whose bullets become user-confirmed facts;
- another assistant's export, through an adapter
  (``generic_json`` or ``generic_markdown``): always untrusted.

The pipeline, in plain words:

1. Parse and validate. A Kosmos archive is refused on a schema
   version mismatch, a manifest hash mismatch, a count mismatch,
   or a broken ledger hash chain in ``events.jsonl``.
2. Treat every imported text as untrusted data. Secrets are
   redacted, instruction-like text is blocked, and text from
   another assistant is quarantined until the user approves it.
   Quarantined text is never stored as a fact without approval.
3. Merge by bitemporal rules: the newer valid time wins, an
   identical item merges as a duplicate, and a contradiction
   against a newer stored fact is reported, never settled
   silently. A newer value supersedes the old one; the old row
   stays as history.
4. Dry run first: :func:`plan_import` (and
   :func:`import_memory` without ``approve=True``) shows exactly
   what would change and writes nothing. Nothing is written
   until the caller approves.
5. Writes go through :func:`puspa.memory.facts.record_fact`,
   tagged with the import batch id, and one ledger event records
   the import. Imported items from the user's own archive or
   file restore as active facts; adapter items and inferred
   hypotheses start as proposals.
6. Rollback by batch id expires exactly the facts that import
   created, as new history rows: the ledger stays append-only.
7. An erased item goes on a forget list that blocks re-import:
   :func:`forget_memory_item` hard-deletes the fact and records
   its (key, content hash), and a later import skips any
   candidate that matches it.

Embeddings are not stored in the archive (they are derived
data), so there is nothing to restore: the recall index is
rebuilt from the restored facts by the durable rebuild that
runs after an import.

Honest limits:

- The knowledge graph is validated and its person nodes are
  restored as ``person:`` facts, but graph edges are counted,
  not re-materialised into a separate graph store: the graph is
  rebuilt from episodes by the durable rebuild.
- Skill files are validated (each skill needs a ``SKILL.md``)
  and counted, but not installed into the procedural store.
- Archived ledger events are validated against their hash
  chain, but they are not replayed into the live ledger by
  default: replaying them would fork the live chain. Pass
  ``replay_events=True`` with a ledger to replay them as fresh
  events (same content, new hashes) on a fresh system.
- A full export is encrypted by default; this importer reads
  plain archives only. An encrypted archive must be decrypted
  by the caller first.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.ledger.ledger import GENESIS, canonical_json, compute_event_hash

from . import durable
from . import facts as facts_module
from .schema import resolve_dsn
from .trust import find_instruction_matches, redact_secrets

__all__ = [
    "ARCHIVE_FORMAT",
    "ARCHIVE_SCHEMA_VERSION",
    "MAX_ARCHIVE_BYTES",
    "MAX_ITEMS_PER_FILE",
    "MAX_TEXT_CHARS",
    "Candidate",
    "ImportValidationError",
    "MemoryImportStore",
    "PostgresImportStore",
    "add_to_forget_list",
    "approve_quarantine",
    "approve_quarantine_batch",
    "default_import_store",
    "forget_memory_item",
    "import_memory",
    "is_forgotten",
    "load_archive",
    "parse_generic_json",
    "parse_generic_markdown",
    "parse_memory_md_candidates",
    "plan_import",
    "rollback_import",
    "validate_archive",
]

#: Archive format version this importer understands.
ARCHIVE_SCHEMA_VERSION = 1

#: ``format`` value a manifest must carry.
ARCHIVE_FORMAT = "puspa-memory-archive"

#: Largest archive accepted (bytes). Bigger archives are refused.
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024

#: Largest number of rows accepted in one ``.jsonl`` archive file.
MAX_ITEMS_PER_FILE = 10000

#: Longest text accepted for one imported item (characters).
MAX_TEXT_CHARS = 10000

#: Fact keys that look like a secret are never imported as facts.
_SECRET_KEY_RE = re.compile(r"(token|secret|password|passwd|api[_-]?key|credential)", re.IGNORECASE)

#: Archive file names, in the order they are read.
_ARCHIVE_FILES = (
    "MEMORY.md",
    "events.jsonl",
    "graph.jsonl",
    "people.jsonl",
    "values.jsonl",
    "causal.jsonl",
    "ignorance.jsonl",
)

#: Section heading to key prefix for plain (unkeyed) MEMORY.md bullets.
_PLAIN_KEY_PREFIX = {
    "Standing Goals": "goal:",
    "Commitments": "commitment:",
    "Corrections": "correction:",
    "Preferences": "preference:",
    "Facts": "fact:",
    "People": "person:",
    "Lessons": "lesson:",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ImportValidationError(ValueError):
    """An archive or adapter input failed validation and was refused."""


def _now() -> datetime:
    return datetime.now(UTC)


def _slug(text: str) -> str:
    slug = _SLUG_RE.sub("-", str(text or "").lower()).strip("-")
    return slug[:60] or "item"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


@dataclass
class Candidate:
    """One importable item, before trust screening and merge."""

    key: str
    text: str
    kind: str = "fact"
    source: str = "user_confirmed"
    origin: str = "user"
    valid_from: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def load_archive(path: str | Path) -> dict[str, bytes]:
    """Read a Kosmos archive (directory or zip) into name-to-bytes.

    Raises :class:`ImportValidationError` when the path does not
    exist, is not a directory or zip file, or is over the size cap.
    """
    location = Path(path)
    if not location.exists():
        raise ImportValidationError(f"archive not found: {path}")
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
                raise ImportValidationError("archive is over the size cap")
            files[rel] = data
        return files
    if location.suffix.lower() != ".zip":
        raise ImportValidationError(f"archive must be a directory or a zip file: {path}")
    if location.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ImportValidationError("archive is over the size cap")
    files = {}
    with zipfile.ZipFile(location) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            files[info.filename] = archive.read(info.filename)
    if sum(len(data) for data in files.values()) > MAX_ARCHIVE_BYTES:
        raise ImportValidationError("archive is over the size cap")
    return files


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
            raise ImportValidationError(f"{name} line {line_number}: not valid JSON") from exc
        if not isinstance(row, dict):
            raise ImportValidationError(f"{name} line {line_number}: row is not an object")
        rows.append(row)
        if len(rows) > MAX_ITEMS_PER_FILE:
            raise ImportValidationError(f"{name} has too many rows")
    return rows


def _validate_event_chain(events: list[dict[str, Any]]) -> None:
    """Check the ledger hash chain of archived events, in order.

    Every event must carry the fields the ledger hashes, its
    stored hash must match a recomputation, and each
    ``previous_hash`` must link to the hash before it (or
    ``GENESIS`` for the first event). A broken link refuses the
    whole archive.
    """
    expected_prev = GENESIS
    for index, event in enumerate(events):
        try:
            created_at = _parse_time(event["created_at"])
            recomputed = compute_event_hash(
                task_id=str(event["task_id"]),
                agent_id=str(event["agent_id"]),
                subagent_id=event.get("subagent_id"),
                event_type=str(event["type"]),
                content=event["content"],
                parent_id=event.get("parent_id"),
                previous_hash=str(event["previous_hash"]),
                created_at=created_at or _now(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ImportValidationError(f"events.jsonl row {index}: missing hash field") from exc
        if not isinstance(event.get("content"), dict):
            raise ImportValidationError(f"events.jsonl row {index}: content is not an object")
        if event.get("previous_hash") != expected_prev:
            raise ImportValidationError(
                f"events.jsonl row {index}: hash chain is broken (previous_hash mismatch)"
            )
        if event.get("hash") != recomputed:
            raise ImportValidationError(
                f"events.jsonl row {index}: hash chain is broken (hash mismatch)"
            )
        expected_prev = str(event["hash"])


def _archive_candidates(
    files: dict[str, bytes], manifest: dict[str, Any]
) -> tuple[list[Candidate], dict[str, int], list[str]]:
    """Build import candidates from one validated archive's files.

    Returns ``(candidates, counts, ignored_files)``. ``counts``
    covers the archive parts (facts, events, graph nodes and
    edges, people, values, skills, causal, ignorance).
    """
    exported_at = _parse_time(manifest.get("exported_at"))
    candidates: list[Candidate] = []
    counts: dict[str, int] = {}
    ignored: list[str] = []

    if "MEMORY.md" in files:
        text = files["MEMORY.md"].decode("utf-8")
        md_candidates = parse_memory_md_candidates(text, valid_from=exported_at)
        candidates.extend(md_candidates)
        counts["facts"] = len(md_candidates)
    if "events.jsonl" in files:
        events = _read_jsonl(files["events.jsonl"], "events.jsonl")
        _validate_event_chain(events)
        counts["events"] = len(events)
    else:
        events = []
    if "graph.jsonl" in files:
        rows = _read_jsonl(files["graph.jsonl"], "graph.jsonl")
        nodes = [row for row in rows if row.get("type") == "node"]
        edges = [row for row in rows if row.get("type") == "edge"]
        counts["graph_nodes"] = len(nodes)
        counts["graph_edges"] = len(edges)
        for node in nodes:
            name = str(node.get("name") or node.get("id") or "").strip()
            if node.get("kind") == "person" and name:
                candidates.append(
                    Candidate(
                        key=f"person:{name}",
                        text=name,
                        kind="person",
                        source="user_confirmed",
                        origin="user",
                        valid_from=exported_at,
                        raw={"node_id": node.get("id")},
                    )
                )
    if "people.jsonl" in files:
        rows = _read_jsonl(files["people.jsonl"], "people.jsonl")
        counts["people"] = len(rows)
        for row in rows:
            name = str(row.get("name") or row.get("key") or "").strip()
            if not name:
                continue
            key = str(row.get("key") or f"person:{name}")
            text = str(row.get("notes") or row.get("text") or name)
            candidates.append(
                Candidate(
                    key=key,
                    text=text,
                    kind="person",
                    source="user_confirmed",
                    origin="user",
                    valid_from=_parse_time(row.get("valid_from")) or exported_at,
                    raw=row,
                )
            )
    if "values.jsonl" in files:
        rows = _read_jsonl(files["values.jsonl"], "values.jsonl")
        counts["values"] = len(rows)
        for row in rows:
            key = str(row.get("key") or "").strip()
            text = str(row.get("text") or row.get("value") or "").strip()
            if not key or not text:
                continue
            kind = str(row.get("kind") or "value")
            candidates.append(
                Candidate(
                    key=key,
                    text=text,
                    kind=kind,
                    source="user_confirmed",
                    origin="user",
                    valid_from=_parse_time(row.get("valid_from")) or exported_at,
                    raw=row,
                )
            )
    if "causal.jsonl" in files:
        rows = _read_jsonl(files["causal.jsonl"], "causal.jsonl")
        counts["causal"] = len(rows)
        for row in rows:
            text = str(row.get("hypothesis") or row.get("text") or "").strip()
            if not text:
                continue
            candidates.append(
                Candidate(
                    key=f"causal:{_slug(text)}",
                    text=text,
                    kind="causal",
                    source="inferred",
                    origin="user",
                    valid_from=_parse_time(row.get("valid_from")) or exported_at,
                    raw=row,
                )
            )
    if "ignorance.jsonl" in files:
        rows = _read_jsonl(files["ignorance.jsonl"], "ignorance.jsonl")
        counts["ignorance"] = len(rows)
        for row in rows:
            text = str(row.get("unknown") or row.get("text") or "").strip()
            if not text:
                continue
            candidates.append(
                Candidate(
                    key=f"ignorance:{_slug(text)}",
                    text=text,
                    kind="ignorance",
                    source="inferred",
                    origin="user",
                    valid_from=_parse_time(row.get("valid_from")) or exported_at,
                    raw=row,
                )
            )
    skill_files = sorted(
        name for name in files if name.startswith("skills/") and name.endswith("/SKILL.md")
    )
    counts["skills"] = len(skill_files)
    for name in skill_files:
        if not files[name].decode("utf-8").strip():
            raise ImportValidationError(f"{name} is empty")
    known = set(_ARCHIVE_FILES) | {"manifest.json"}
    for name in sorted(files):
        if name not in known and not name.startswith("skills/"):
            ignored.append(name)
    return candidates, counts, ignored


def validate_archive(files: dict[str, bytes]) -> dict[str, Any]:
    """Validate a loaded archive and build its import candidates.

    Checks the manifest (format, schema version, per-file sha256
    hashes, counts, ledger head) and the ledger hash chain, then
    returns ``{"manifest", "candidates", "counts", "events",
    "ignored_files"}``. Any failure raises
    :class:`ImportValidationError` and imports nothing.
    """
    if "manifest.json" not in files:
        raise ImportValidationError("archive has no manifest.json")
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ImportValidationError("manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ImportValidationError("manifest.json is not an object")
    if manifest.get("format") != ARCHIVE_FORMAT:
        raise ImportValidationError(f"unknown archive format: {manifest.get('format')!r}")
    if manifest.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
        raise ImportValidationError(
            f"unsupported schema version: {manifest.get('schema_version')!r}"
        )
    listed = manifest.get("files")
    if not isinstance(listed, dict):
        raise ImportValidationError("manifest has no files hash list")
    for name, digest in listed.items():
        if name not in files:
            raise ImportValidationError(f"manifest lists missing file: {name}")
        actual = hashlib.sha256(files[name]).hexdigest()
        if actual != digest:
            raise ImportValidationError(f"manifest hash mismatch for file: {name}")
    if "MEMORY.md" not in files:
        raise ImportValidationError("archive has no MEMORY.md")
    candidates, counts, ignored = _archive_candidates(files, manifest)
    declared = manifest.get("counts")
    if isinstance(declared, dict):
        for key in (
            "events",
            "graph_nodes",
            "graph_edges",
            "people",
            "values",
            "causal",
            "ignorance",
        ):
            if key in declared and key in counts and int(declared[key]) != counts[key]:
                raise ImportValidationError(
                    f"manifest count mismatch for {key}: "
                    f"declared {declared[key]}, found {counts[key]}"
                )
    events = _read_jsonl(files["events.jsonl"], "events.jsonl") if "events.jsonl" in files else []
    head = manifest.get("ledger_head")
    if events:
        if head is not None and head != events[-1].get("hash"):
            raise ImportValidationError("manifest ledger_head does not match the last event")
    elif head is not None and head != GENESIS:
        raise ImportValidationError("manifest ledger_head set but the archive has no events")
    return {
        "manifest": manifest,
        "candidates": candidates,
        "counts": counts,
        "events": events,
        "ignored_files": ignored,
    }


def parse_memory_md_candidates(text: str, *, valid_from: datetime | None = None) -> list[Candidate]:
    """Candidates from a MEMORY.md text (hand-edited or archived).

    Keyed bullets become facts with their written key; plain
    bullets get a derived key from their section. Journal
    bullets are history and are never imported; unknown sections
    are ignored. Guesses become inferred proposals, everything
    else becomes a user-confirmed fact.
    """
    parsed = durable.parse_memory_md(text)
    candidates: list[Candidate] = []
    for section, items in parsed["facts"].items():
        if section == "Journal":
            continue
        for item in items:
            key = str(item["key"])
            value = str(item["value"])
            if not value or value == "_Nothing recorded yet._":
                continue
            source = "inferred" if section == "Unconfirmed Guesses" else "user_confirmed"
            candidates.append(
                Candidate(key=key, text=value, source=source, origin="user", valid_from=valid_from)
            )
    diff = durable.diff_memory_md("", text)
    for entry in diff["added"]:
        if entry["key"] is not None or entry["section"] == "Journal":
            continue
        section = entry["section"]
        value = str(entry["new"] or "")
        prefix = _PLAIN_KEY_PREFIX.get(section)
        if prefix is None or not value or value == "_Nothing recorded yet._":
            continue
        source = "inferred" if section == "Unconfirmed Guesses" else "user_confirmed"
        candidates.append(
            Candidate(
                key=f"{prefix}{_slug(value)}",
                text=value,
                source=source,
                origin="user",
                valid_from=valid_from,
            )
        )
    return candidates


def parse_generic_json(data: Any) -> list[Candidate]:
    """Adapter for another assistant's JSON memory export.

    Accepts ``{"memories": [...]}`` or a bare list. Each item
    needs ``key`` (or ``title``) and ``text`` (or ``content`` or
    ``memory``). Every candidate is marked origin ``other_bot``:
    untrusted until approved.
    """
    if isinstance(data, dict):
        items = data.get("memories") or data.get("items") or data.get("facts")
        if items is None:
            raise ImportValidationError("generic JSON export has no memories list")
    elif isinstance(data, list):
        items = data
    else:
        raise ImportValidationError("generic JSON export must be an object or a list")
    if not isinstance(items, list):
        raise ImportValidationError("generic JSON export memories must be a list")
    if len(items) > MAX_ITEMS_PER_FILE:
        raise ImportValidationError("generic JSON export has too many items")
    candidates: list[Candidate] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ImportValidationError(f"generic JSON item {index} is not an object")
        key = str(item.get("key") or item.get("title") or "").strip()
        text = str(item.get("text") or item.get("content") or item.get("memory") or "").strip()
        if not key or not text:
            raise ImportValidationError(f"generic JSON item {index} has no key or text")
        candidates.append(
            Candidate(
                key=key,
                text=text,
                source="inferred",
                origin="other_bot",
                valid_from=_parse_time(item.get("created_at") or item.get("valid_from")),
                raw=item,
            )
        )
    return candidates


def parse_generic_markdown(text: str) -> list[Candidate]:
    """Adapter for another assistant's Markdown memory export.

    ``## Heading`` sections group ``- bullet`` lines; a bullet
    written ``- **key**: value`` keeps its key, a plain bullet
    gets a key from its heading. Every candidate is marked
    origin ``other_bot``: untrusted until approved.
    """
    candidates: list[Candidate] = []
    section = "Facts"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip() or "Facts"
            continue
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        match = re.match(r"^\*\*(.+?)\*\*:\s*(.*)$", body)
        if match:
            key, value = match.group(1).strip(), match.group(2).strip()
        else:
            prefix = _PLAIN_KEY_PREFIX.get(section, "fact:")
            key, value = f"{prefix}{_slug(body)}", body
        if not value:
            continue
        candidates.append(
            Candidate(
                key=key, text=value, source="inferred", origin="other_bot", raw={"section": section}
            )
        )
        if len(candidates) > MAX_ITEMS_PER_FILE:
            raise ImportValidationError("generic Markdown export has too many items")
    return candidates


def _screen(candidate: Candidate) -> tuple[str, str, str]:
    """Trust-screen one candidate.

    Returns ``(disposition, reason, screened_text)`` where
    disposition is ``"allow"``, ``"quarantine"`` or ``"blocked"``.
    Secrets are redacted in every case; instruction-like text is
    blocked; text from another assistant is quarantined.
    """
    if _SECRET_KEY_RE.search(candidate.key):
        return "blocked", "key looks like a secret", candidate.text
    if len(candidate.text) > MAX_TEXT_CHARS:
        return "blocked", "text is over the length cap", candidate.text
    redacted = redact_secrets(candidate.text)
    if not redacted.strip():
        return "blocked", "text is empty after redaction", redacted
    matches = find_instruction_matches(redacted)
    if matches:
        return "blocked", f"instruction pattern: {', '.join(matches)}", redacted
    if candidate.origin not in ("user", "user_confirmed", "user_stated"):
        return "quarantine", f"external origin: {candidate.origin}", redacted
    return "allow", "", redacted


def _dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Drop duplicate keys, keeping the newest valid time."""
    by_key: dict[str, Candidate] = {}
    for candidate in candidates:
        previous = by_key.get(candidate.key)
        if previous is None:
            by_key[candidate.key] = candidate
            continue
        prev_time = previous.valid_from or datetime.min.replace(tzinfo=UTC)
        new_time = candidate.valid_from or datetime.min.replace(tzinfo=UTC)
        if new_time >= prev_time:
            by_key[candidate.key] = candidate
    return list(by_key.values())


class MemoryImportStore:
    """In-memory import store: same interface as the Postgres store."""

    def __init__(self) -> None:
        self._batches: dict[str, dict[str, Any]] = {}
        self._items: list[dict[str, Any]] = []
        self._quarantine: dict[str, dict[str, Any]] = {}
        self._forget: list[dict[str, Any]] = []

    def create_batch(self, user_id: str, source: str, counts: dict[str, int]) -> dict[str, Any]:
        batch: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "source": source,
            "status": "importing",
            "counts": dict(counts),
            "created_at": _now(),
        }
        self._batches[batch["id"]] = batch
        return dict(batch)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        batch = self._batches.get(str(batch_id))
        return dict(batch) if batch is not None else None

    def update_batch(self, batch_id: str, **fields: Any) -> dict[str, Any] | None:
        batch = self._batches.get(str(batch_id))
        if batch is None:
            return None
        batch.update(fields)
        return dict(batch)

    def add_batch_item(
        self, batch_id: str, user_id: str, fact_id: str | None, key: str, disposition: str
    ) -> dict[str, Any]:
        item = {
            "id": str(uuid.uuid4()),
            "batch_id": str(batch_id),
            "user_id": str(user_id),
            "fact_id": fact_id,
            "fact_key": key,
            "disposition": disposition,
            "created_at": _now(),
        }
        self._items.append(item)
        return dict(item)

    def list_batch_items(self, batch_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self._items if item["batch_id"] == str(batch_id)]

    def add_quarantine(
        self,
        batch_id: str | None,
        user_id: str,
        key: str,
        text: str,
        reason: str,
        status: str = "pending",
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "batch_id": str(batch_id) if batch_id else None,
            "user_id": str(user_id),
            "fact_key": key,
            "text": text,
            "reason": reason,
            "status": status,
            "created_at": _now(),
            "decided_at": None,
        }
        self._quarantine[row["id"]] = row
        return dict(row)

    def get_quarantine(self, quarantine_id: str) -> dict[str, Any] | None:
        row = self._quarantine.get(str(quarantine_id))
        return dict(row) if row is not None else None

    def list_quarantine(
        self, user_id: str, *, batch_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        rows = [row for row in self._quarantine.values() if row["user_id"] == str(user_id)]
        if batch_id is not None:
            rows = [row for row in rows if row["batch_id"] == str(batch_id)]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        rows.sort(key=lambda row: row["created_at"])
        return [dict(row) for row in rows]

    def update_quarantine(self, quarantine_id: str, **fields: Any) -> dict[str, Any] | None:
        row = self._quarantine.get(str(quarantine_id))
        if row is None:
            return None
        row.update(fields)
        return dict(row)

    def add_forget(
        self, user_id: str, key: str, content_hash: str, reason: str = ""
    ) -> dict[str, Any]:
        for row in self._forget:
            if (
                row["user_id"] == str(user_id)
                and row["fact_key"] == key
                and row["content_hash"] == content_hash
            ):
                return dict(row)
        row = {
            "user_id": str(user_id),
            "fact_key": key,
            "content_hash": content_hash,
            "reason": reason,
            "created_at": _now(),
        }
        self._forget.append(row)
        return dict(row)

    def is_forgotten(self, user_id: str, key: str, content_hash: str) -> bool:
        return any(
            row["user_id"] == str(user_id)
            and row["fact_key"] == key
            and row["content_hash"] == content_hash
            for row in self._forget
        )

    def list_forget(self, user_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._forget if row["user_id"] == str(user_id)]


class PostgresImportStore:
    """Postgres import store over migration 0031 tables."""

    def __init__(self, dsn: str | None = None):
        self.dsn = resolve_dsn(dsn)

    def _connect(self):
        return _db_connect(self.dsn, row_factory=dict_row)

    def create_batch(self, user_id: str, source: str, counts: dict[str, int]) -> dict[str, Any]:
        import json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_import_batches (user_id, source, status, counts) "
                "VALUES (%s, %s, 'importing', %s::jsonb) RETURNING *",
                (str(user_id), source, json.dumps(counts)),
            )
            row = cur.fetchone()
            conn.commit()
        return _public_batch(row)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM memory_import_batches WHERE id = %s", (str(batch_id),))
            row = cur.fetchone()
        return _public_batch(row) if row is not None else None

    def update_batch(self, batch_id: str, **fields: Any) -> dict[str, Any] | None:
        import json

        sets: list[str] = []
        params: list[Any] = []
        for name, value in fields.items():
            if name == "counts":
                sets.append("counts = %s::jsonb")
                params.append(json.dumps(value))
            else:
                sets.append(f"{name} = %s")
                params.append(value)
        if not sets:
            return self.get_batch(batch_id)
        params.append(str(batch_id))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE memory_import_batches SET {', '.join(sets)}, "
                "updated_at = now() WHERE id = %s RETURNING *",
                params,
            )
            row = cur.fetchone()
            conn.commit()
        return _public_batch(row) if row is not None else None

    def add_batch_item(
        self, batch_id: str, user_id: str, fact_id: str | None, key: str, disposition: str
    ) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_import_items "
                "(batch_id, user_id, fact_id, fact_key, disposition) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING *",
                (str(batch_id), str(user_id), fact_id, key, disposition),
            )
            row = cur.fetchone()
            conn.commit()
        return _public_item(row)

    def list_batch_items(self, batch_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM memory_import_items WHERE batch_id = %s ORDER BY created_at ASC",
                (str(batch_id),),
            )
            return [_public_item(row) for row in cur.fetchall()]

    def add_quarantine(
        self,
        batch_id: str | None,
        user_id: str,
        key: str,
        text: str,
        reason: str,
        status: str = "pending",
    ) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_import_quarantine "
                "(batch_id, user_id, fact_key, text, reason, status) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
                (str(batch_id) if batch_id else None, str(user_id), key, text, reason, status),
            )
            row = cur.fetchone()
            conn.commit()
        return _public_quarantine(row)

    def get_quarantine(self, quarantine_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM memory_import_quarantine WHERE id = %s", (str(quarantine_id),)
            )
            row = cur.fetchone()
        return _public_quarantine(row) if row is not None else None

    def list_quarantine(
        self, user_id: str, *, batch_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_import_quarantine WHERE user_id = %s"
        params: list[Any] = [str(user_id)]
        if batch_id is not None:
            query += " AND batch_id = %s"
            params.append(str(batch_id))
        if status is not None:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            return [_public_quarantine(row) for row in cur.fetchall()]

    def update_quarantine(self, quarantine_id: str, **fields: Any) -> dict[str, Any] | None:
        sets: list[str] = []
        params: list[Any] = []
        for name, value in fields.items():
            sets.append(f"{name} = %s")
            params.append(value)
        if not sets:
            return self.get_quarantine(quarantine_id)
        params.append(str(quarantine_id))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE memory_import_quarantine SET {', '.join(sets)} WHERE id = %s RETURNING *",
                params,
            )
            row = cur.fetchone()
            conn.commit()
        return _public_quarantine(row) if row is not None else None

    def add_forget(
        self, user_id: str, key: str, content_hash: str, reason: str = ""
    ) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_forget_list (user_id, fact_key, content_hash, reason) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (user_id, fact_key, content_hash) DO NOTHING "
                "RETURNING *",
                (str(user_id), key, content_hash, reason),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT * FROM memory_forget_list "
                    "WHERE user_id = %s AND fact_key = %s AND content_hash = %s",
                    (str(user_id), key, content_hash),
                )
                row = cur.fetchone()
            conn.commit()
        return _public_forget(row)

    def is_forgotten(self, user_id: str, key: str, content_hash: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM memory_forget_list "
                "WHERE user_id = %s AND fact_key = %s AND content_hash = %s",
                (str(user_id), key, content_hash),
            )
            return cur.fetchone() is not None

    def list_forget(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM memory_forget_list WHERE user_id = %s ORDER BY created_at ASC",
                (str(user_id),),
            )
            return [_public_forget(row) for row in cur.fetchall()]


def _public_batch(row: dict[str, Any]) -> dict[str, Any]:
    import json

    counts = row["counts"]
    if isinstance(counts, str):
        counts = json.loads(counts)
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "source": row["source"],
        "status": row["status"],
        "counts": dict(counts or {}),
        "created_at": row["created_at"],
    }


def _public_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "batch_id": str(row["batch_id"]),
        "user_id": str(row["user_id"]),
        "fact_id": str(row["fact_id"]) if row.get("fact_id") else None,
        "fact_key": row["fact_key"],
        "disposition": row["disposition"],
        "created_at": row["created_at"],
    }


def _public_quarantine(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "batch_id": str(row["batch_id"]) if row.get("batch_id") else None,
        "user_id": str(row["user_id"]),
        "fact_key": row["fact_key"],
        "text": row["text"],
        "reason": row["reason"],
        "status": row["status"],
        "created_at": row["created_at"],
        "decided_at": row.get("decided_at"),
    }


def _public_forget(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(row["user_id"]),
        "fact_key": row["fact_key"],
        "content_hash": row["content_hash"],
        "reason": row.get("reason") or "",
        "created_at": row["created_at"],
    }


def default_import_store(dsn: str | None = None):
    """Production import store (Postgres)."""
    return PostgresImportStore(dsn=dsn)


def plan_import(
    user_id: str,
    candidates: list[Candidate],
    *,
    fact_store: Any = None,
    import_store: Any = None,
    dsn: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dry-run plan for importing ``candidates``: what would change.

    Writes nothing. Each candidate is screened (forget list,
    secrets, instruction patterns, external origin) and merged
    against the stored active facts by bitemporal rules: same
    key and text merges as a duplicate, a newer valid time
    supersedes, an older value against a newer stored fact is a
    conflict that is never settled silently. Returns the plan
    with per-item dispositions and counts.
    """
    now = now or _now()
    fact_store = fact_store or facts_module.PostgresFactStore(dsn=dsn)
    import_store = import_store or default_import_store(dsn=dsn)
    active = {fact["key"]: fact for fact in fact_store.list_user(user_id, statuses=("active",))}
    items: list[dict[str, Any]] = []
    counts = {
        "add": 0,
        "supersede": 0,
        "duplicate": 0,
        "conflict": 0,
        "quarantine": 0,
        "blocked": 0,
        "forgotten": 0,
    }
    for candidate in _dedupe_candidates(candidates):
        if not candidate.key or not candidate.text:
            continue
        text = candidate.text.strip()
        if not text:
            continue
        if import_store.is_forgotten(user_id, candidate.key, _content_hash(text)):
            disposition, reason = "forgotten", "on the forget list (erased item)"
            counts["forgotten"] += 1
        else:
            screened, reason, screened_text = _screen(candidate)
            text = screened_text
            if screened == "blocked":
                disposition = "blocked"
                counts["blocked"] += 1
            elif screened == "quarantine":
                disposition = "quarantine"
                counts["quarantine"] += 1
            else:
                existing = active.get(candidate.key)
                if existing is None:
                    disposition, reason = "add", ""
                    counts["add"] += 1
                elif str(existing.get("content") or "") == text:
                    disposition, reason = "duplicate", "already stored with the same text"
                    counts["duplicate"] += 1
                else:
                    candidate_time = candidate.valid_from or now
                    if candidate_time.tzinfo is None:
                        candidate_time = candidate_time.replace(tzinfo=UTC)
                    stored_time = existing.get("valid_from") or existing.get("created_at") or now
                    if stored_time.tzinfo is None:
                        stored_time = stored_time.replace(tzinfo=UTC)
                    if candidate_time > stored_time:
                        disposition, reason = "supersede", "newer valid time than the stored fact"
                        counts["supersede"] += 1
                    else:
                        disposition = "conflict"
                        reason = "stored fact has a newer valid time; not settled silently"
                        counts["conflict"] += 1
        items.append(
            {
                "key": candidate.key,
                "text": text,
                "kind": candidate.kind,
                "source": candidate.source,
                "disposition": disposition,
                "reason": reason,
                "valid_from": candidate.valid_from,
            }
        )
    counts["total"] = len(items)
    counts["would_change"] = counts["add"] + counts["supersede"]
    return {
        "batch_id": str(uuid.uuid4()),
        "user_id": str(user_id),
        "counts": counts,
        "items": items,
        "would_change": counts["would_change"] > 0,
    }


def _load_candidates(
    source: str, path: str | Path | None, raw: Any
) -> tuple[list[Candidate], dict[str, Any]]:
    """Load candidates for one import source. Returns (candidates, info)."""
    if source == "kosmos":
        if path is None:
            raise ImportValidationError("kosmos import needs an archive path")
        validated = validate_archive(load_archive(path))
        return validated["candidates"], {
            "counts": validated["counts"],
            "events": validated["events"],
            "ignored_files": validated["ignored_files"],
        }
    if source == "memory_md":
        if path is None:
            raise ImportValidationError("memory_md import needs a file path")
        text = Path(path).read_text(encoding="utf-8")
        return parse_memory_md_candidates(text), {"counts": {}, "events": [], "ignored_files": []}
    if source == "generic_json":
        if raw is not None:
            data = raw
        elif path is not None:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        else:
            raise ImportValidationError("generic_json import needs a file path or data")
        return parse_generic_json(data), {"counts": {}, "events": [], "ignored_files": []}
    if source == "generic_markdown":
        if path is not None:
            text = Path(path).read_text(encoding="utf-8")
        elif isinstance(raw, str):
            text = raw
        else:
            raise ImportValidationError("generic_markdown import needs a file path or text")
        return parse_generic_markdown(text), {"counts": {}, "events": [], "ignored_files": []}
    raise ImportValidationError(f"unknown import source: {source!r}")


def _detect_source(path: str | Path) -> str:
    location = Path(path)
    if location.suffix.lower() == ".md":
        return "memory_md"
    if location.suffix.lower() == ".json":
        return "generic_json"
    return "kosmos"


def _replay_events(events: list[dict[str, Any]], ledger: Any) -> dict[str, int]:
    """Replay validated archive events into a ledger (fresh chain).

    Each event is appended with its original task, agent, type,
    content and valid time, so the content is preserved verbatim
    while the hashes are recomputed for the live chain. Events
    whose canonical content already exists in the same task's
    chain are skipped as duplicates. Returns replayed/skipped
    counts.
    """
    replayed = 0
    skipped = 0
    seen_tasks: dict[str, set[str]] = {}
    for event in events:
        task_id = str(event["task_id"])
        if task_id not in seen_tasks:
            existing = ledger.events(task_id)
            seen_tasks[task_id] = {canonical_json(item["content"]) for item in existing}
        if canonical_json(event["content"]) in seen_tasks[task_id]:
            skipped += 1
            continue
        ledger.append_event(
            task_id=task_id,
            agent_id=str(event["agent_id"]),
            subagent_id=event.get("subagent_id"),
            event_type=str(event["type"]),
            content=event["content"],
            parent_id=event.get("parent_id"),
            valid_at=_parse_time(event.get("valid_at")),
        )
        seen_tasks[task_id].add(canonical_json(event["content"]))
        replayed += 1
    return {"replayed": replayed, "skipped": skipped}


def _record_ledger(ledger: Any, task_id: str, agent_id: str, content: dict[str, Any]) -> str | None:
    if ledger is None:
        return None
    event = ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="task_completed",
        content=content,
    )
    return str(event["id"])


def import_memory(
    user_id: str,
    path: str | Path | None = None,
    *,
    source: str | None = None,
    data: Any = None,
    candidates: list[Candidate] | None = None,
    approve: bool = False,
    dry_run: bool = False,
    fact_store: Any = None,
    durable_store: Any = None,
    import_store: Any = None,
    ledger: Any = None,
    agent_id: str = "puspa",
    dsn: str | None = None,
    replay_events: bool = False,
    rebuild: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Import memory for ``user_id`` from one source.

    ``source`` is ``"kosmos"`` (archive directory or zip),
    ``"memory_md"`` (a MEMORY.md file), ``"generic_json"`` or
    ``"generic_markdown"`` (another assistant's export); it is
    auto-detected from ``path`` when omitted. ``candidates`` (a
    prebuilt list) overrides path loading, for callers and tests
    that already parsed the input.

    Without ``approve=True`` (or with ``dry_run=True``) this
    only plans: it validates, screens and merges, and returns
    ``{"status": "awaiting_approval" or "dry_run", ...}`` having
    written nothing. With approval it writes the batch, the
    facts (superseding by bitemporal rules), the quarantine
    rows, one ledger event, and rebuilds the durable MEMORY.md.
    """
    now = now or _now()
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("user_id must be a non-empty string")
    fact_store = fact_store or facts_module.PostgresFactStore(dsn=dsn)
    import_store = import_store or default_import_store(dsn=dsn)
    if ledger is None and dsn is not None:
        from puspa.ledger.ledger import Ledger

        ledger = Ledger(dsn)
    if candidates is not None:
        loaded: list[Candidate] = list(candidates)
        info: dict[str, Any] = {"counts": {}, "events": [], "ignored_files": []}
        resolved_source = source or "candidates"
    else:
        if path is None and data is None:
            raise ImportValidationError("import needs a path, data, or candidates")
        resolved_source = source or (_detect_source(path) if path is not None else "generic_json")
        loaded, info = _load_candidates(resolved_source, path, data)
    plan = plan_import(
        user_id, loaded, fact_store=fact_store, import_store=import_store, dsn=dsn, now=now
    )
    if dry_run or not approve:
        return {
            "status": "dry_run" if dry_run else "awaiting_approval",
            "batch_id": plan["batch_id"],
            "user_id": str(user_id),
            "source": resolved_source,
            "counts": plan["counts"],
            "items": plan["items"],
            "would_change": plan["would_change"],
            "archive_counts": info["counts"],
            "ignored_files": info["ignored_files"],
        }
    batch = import_store.create_batch(user_id, resolved_source, plan["counts"])
    batch_id = str(batch["id"])
    fact_ids: list[str] = []
    quarantined: list[dict[str, Any]] = []
    for item in plan["items"]:
        disposition = item["disposition"]
        if disposition in ("add", "supersede"):
            recorded = facts_module.record_fact(
                user_id,
                item["key"],
                item["text"],
                source=item["source"],
                store=fact_store,
                origin="user",
                trust_check=False,
                now=now,
                source_detail=f"import:{batch_id}",
            )
            if not recorded.get("stored"):
                continue
            fact_id = str(recorded["id"])
            valid_from = item.get("valid_from")
            if valid_from is not None:
                valid_from = min(valid_from, now)
                fact_store.update(fact_id, user_id, valid_from=valid_from, updated_at=now)
            fact_ids.append(fact_id)
            import_store.add_batch_item(batch_id, user_id, fact_id, item["key"], disposition)
        elif disposition == "quarantine":
            row = import_store.add_quarantine(
                batch_id, user_id, item["key"], item["text"], item["reason"]
            )
            quarantined.append(row)
        elif disposition == "blocked":
            import_store.add_quarantine(
                batch_id, user_id, item["key"], item["text"], item["reason"], status="blocked"
            )
    replayed: dict[str, int] | None = None
    if replay_events and ledger is not None and info["events"]:
        replayed = _replay_events(info["events"], ledger)
    rebuilt = None
    if rebuild:
        durable_store = durable_store or durable.PostgresDurableStore(dsn=dsn)
        rebuilt = durable.rebuild_memory(
            user_id,
            dsn=dsn,
            store=durable_store,
            fact_store=fact_store,
            now=now,
            summaries=[],
            episodes=[],
            journal_entries=[],
        )
    ledger_event_id = _record_ledger(
        ledger,
        task_id=f"memory-import-{batch_id}",
        agent_id=agent_id,
        content={
            "job": "memory_import",
            "user_id": str(user_id),
            "batch_id": batch_id,
            "source": resolved_source,
            "counts": plan["counts"],
            "fact_ids": fact_ids,
            "quarantined": len(quarantined),
            "replayed_events": replayed,
        },
    )
    import_store.update_batch(batch_id, status="completed")
    return {
        "status": "applied",
        "batch_id": batch_id,
        "user_id": str(user_id),
        "source": resolved_source,
        "counts": plan["counts"],
        "items": plan["items"],
        "would_change": plan["would_change"],
        "archive_counts": info["counts"],
        "ignored_files": info["ignored_files"],
        "fact_ids": fact_ids,
        "quarantined": quarantined,
        "replayed_events": replayed,
        "rebuilt": rebuilt,
        "ledger_event_id": ledger_event_id,
    }


def rollback_import(
    user_id: str,
    batch_id: str,
    *,
    fact_store: Any = None,
    import_store: Any = None,
    ledger: Any = None,
    agent_id: str = "puspa",
    dsn: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Roll back one import by batch id.

    Every fact the import created that is still active is
    expired (a new history row, never a delete), so the ledger
    stays append-only. Facts the import created that a later
    write already superseded are left as history. Raises
    KeyError for an unknown batch.
    """
    now = now or _now()
    fact_store = fact_store or facts_module.PostgresFactStore(dsn=dsn)
    import_store = import_store or default_import_store(dsn=dsn)
    if ledger is None and dsn is not None:
        from puspa.ledger.ledger import Ledger

        ledger = Ledger(dsn)
    batch = import_store.get_batch(batch_id)
    if batch is None:
        raise KeyError(f"import batch not found: {batch_id}")
    expired: list[str] = []
    already_resolved: list[str] = []
    missing: list[str] = []
    for item in import_store.list_batch_items(batch_id):
        fact_id = item.get("fact_id")
        if fact_id is None:
            continue
        fact = fact_store.get(fact_id, user_id)
        if fact is None:
            missing.append(str(fact_id))
        elif fact["status"] == "active":
            facts_module.expire_fact(user_id, fact_id, store=fact_store, now=now)
            expired.append(str(fact_id))
        else:
            already_resolved.append(str(fact_id))
    import_store.update_batch(batch_id, status="rolled_back")
    ledger_event_id = _record_ledger(
        ledger,
        task_id=f"memory-import-{batch_id}",
        agent_id=agent_id,
        content={
            "job": "memory_import_rollback",
            "user_id": str(user_id),
            "batch_id": str(batch_id),
            "expired": expired,
            "already_resolved": already_resolved,
            "missing": missing,
        },
    )
    return {
        "status": "rolled_back",
        "batch_id": str(batch_id),
        "user_id": str(user_id),
        "expired": expired,
        "already_resolved": already_resolved,
        "missing": missing,
        "ledger_event_id": ledger_event_id,
    }


def add_to_forget_list(
    user_id: str,
    key: str,
    content: str,
    *,
    import_store: Any = None,
    dsn: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Put one erased (key, content) pair on the forget list.

    A later import skips any candidate that matches this key
    and content hash, so an erased item cannot come back
    through an old archive. The list stores the hash, not the
    text.
    """
    if not key or not content:
        raise ValueError("key and content must be non-empty strings")
    import_store = import_store or default_import_store(dsn=dsn)
    return import_store.add_forget(user_id, key, _content_hash(content.strip()), reason=reason)


def is_forgotten(
    user_id: str,
    key: str,
    content: str,
    *,
    import_store: Any = None,
    dsn: str | None = None,
) -> bool:
    """True when (key, content) is on the user's forget list."""
    import_store = import_store or default_import_store(dsn=dsn)
    return import_store.is_forgotten(user_id, key, _content_hash(content.strip()))


def forget_memory_item(
    user_id: str,
    key: str,
    *,
    content: str | None = None,
    fact_store: Any = None,
    import_store: Any = None,
    ledger: Any = None,
    agent_id: str = "puspa",
    dsn: str | None = None,
    reason: str = "user erased this item",
) -> dict[str, Any]:
    """Forget one fact and block its re-import.

    Hard-deletes the fact and all its past versions (the
    data-subject deletion in :func:`puspa.memory.facts.
    forget_fact`), then records the (key, content hash) on the
    forget list so an old archive cannot bring it back. Raises
    KeyError when no fact with that key exists.
    """
    fact_store = fact_store or facts_module.PostgresFactStore(dsn=dsn)
    import_store = import_store or default_import_store(dsn=dsn)
    if ledger is None and dsn is not None:
        from puspa.ledger.ledger import Ledger

        ledger = Ledger(dsn)
    matches = [fact for fact in fact_store.list_for_key(user_id, key) if fact["status"] == "active"]
    if not matches:
        raise KeyError(f"no active fact with key: {key}")
    forgotten_content = content if content is not None else str(matches[0].get("content") or "")
    result = facts_module.forget_fact(user_id, matches[0]["id"], store=fact_store)
    entry = add_to_forget_list(
        user_id, key, forgotten_content, import_store=import_store, reason=reason
    )
    _record_ledger(
        ledger,
        task_id=f"memory-forget-{uuid.uuid4().hex[:12]}",
        agent_id=agent_id,
        content={
            "job": "memory_import_forget",
            "user_id": str(user_id),
            "key": key,
            "forgotten_ids": result["forgotten_ids"],
        },
    )
    return {
        "forgotten_ids": result["forgotten_ids"],
        "key": key,
        "user_id": str(user_id),
        "forget_listed": entry,
    }


def approve_quarantine(
    user_id: str,
    quarantine_id: str,
    *,
    approve: bool = True,
    fact_store: Any = None,
    import_store: Any = None,
    ledger: Any = None,
    agent_id: str = "puspa",
    dsn: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Approve or reject one quarantined item.

    Approval stores it as a user-confirmed fact (the user has
    now vouched for it) and links it to its import batch.
    Rejection marks it rejected and it is never stored. Raises
    KeyError for an unknown id and ValueError when it was
    already decided.
    """
    now = now or _now()
    fact_store = fact_store or facts_module.PostgresFactStore(dsn=dsn)
    import_store = import_store or default_import_store(dsn=dsn)
    if ledger is None and dsn is not None:
        from puspa.ledger.ledger import Ledger

        ledger = Ledger(dsn)
    row = import_store.get_quarantine(quarantine_id)
    if row is None:
        raise KeyError(f"quarantined item not found: {quarantine_id}")
    if str(row["user_id"]) != str(user_id):
        raise KeyError(f"quarantined item not found: {quarantine_id}")
    if row["status"] != "pending":
        raise ValueError(f"quarantined item already decided: {row['status']}")
    if not approve:
        updated = import_store.update_quarantine(quarantine_id, status="rejected", decided_at=now)
        return {"status": "rejected", "quarantine": updated, "fact_id": None}
    recorded = facts_module.record_fact(
        user_id,
        row["fact_key"],
        row["text"],
        source="user_confirmed",
        store=fact_store,
        origin="user",
        trust_check=False,
        now=now,
        source_detail=f"import:{row['batch_id']}:approved"
        if row.get("batch_id")
        else "import:approved",
    )
    fact_id = str(recorded["id"]) if recorded.get("stored") else None
    if fact_id is not None and row.get("batch_id"):
        import_store.add_batch_item(row["batch_id"], user_id, fact_id, row["fact_key"], "approved")
    updated = import_store.update_quarantine(quarantine_id, status="approved", decided_at=now)
    _record_ledger(
        ledger,
        task_id=f"memory-import-{row.get('batch_id') or quarantine_id}",
        agent_id=agent_id,
        content={
            "job": "memory_import_approve",
            "user_id": str(user_id),
            "quarantine_id": str(quarantine_id),
            "key": row["fact_key"],
            "fact_id": fact_id,
        },
    )
    return {"status": "approved", "quarantine": updated, "fact_id": fact_id}


def approve_quarantine_batch(
    user_id: str,
    batch_id: str,
    *,
    fact_store: Any = None,
    import_store: Any = None,
    ledger: Any = None,
    agent_id: str = "puspa",
    dsn: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Approve every pending quarantined item of one batch."""
    import_store = import_store or default_import_store(dsn=dsn)
    approved: list[str] = []
    for row in import_store.list_quarantine(user_id, batch_id=batch_id, status="pending"):
        result = approve_quarantine(
            user_id,
            row["id"],
            approve=True,
            fact_store=fact_store,
            import_store=import_store,
            ledger=ledger,
            agent_id=agent_id,
            dsn=dsn,
            now=now,
        )
        if result["fact_id"] is not None:
            approved.append(result["fact_id"])
    return {"batch_id": str(batch_id), "user_id": str(user_id), "approved_fact_ids": approved}
