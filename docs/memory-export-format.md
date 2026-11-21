# Memory export format (`puspa-memory-archive`, version 1)

One command, API call, or PWA button produces a portable archive
of one user's memory. This document describes the file, the
encryption, and the read-back check. The builder lives in
`puspa/memory/memory_export.py`; the design is in
`docs/kosmos-brain-v2.md`, section "Memory import and export".

## How to export

```sh
# CLI (passphrase from the environment or a prompt, never an argument)
export PUSPA_EXPORT_PASSPHRASE="..."
puspa memory export --user <user-id> --out memory.puspa
```

```http
POST /api/memory/export/archive
{"passphrase": "..."}
```

Both return the same file. The old `GET /api/memory/export`
(JSON user export, K12) is untouched.

## The file

A full export is encrypted by default. The file layout is:

```
PUSPA-MEMORY-ARCHIVE-V1\n   magic header (24 bytes, ASCII)
<16 bytes salt>              PBKDF2 salt
<Fernet token>               the ZIP bytes, sealed
```

Inside the seal is one ZIP with a fixed entry timestamp, so the
same memory always packs to the same ZIP bytes. The ZIP holds:

| File | Content |
| --- | --- |
| `MEMORY.md` | The nightly durable memory document (rebuilt first when none is stored) |
| `events.jsonl` | Ledger entries, insertion order (the hash chain: `hash` + `previous_hash`, `valid_at`, `created_at`) |
| `graph.jsonl` | Knowledge graph nodes (`{"type": "node", ...}`) and edges (`{"type": "edge", ...}`), rebuilt deterministically from the episodes |
| `people.jsonl` | Profile facts whose key starts `person:` |
| `values.jsonl` | Profile facts whose key starts `preference:` or `standing_goal` |
| `skills/procedures.jsonl` | Learned procedures |
| `causal.jsonl` | Causal hypotheses with strength, evidence, and expiry |
| `ignorance.jsonl` | Known unknowns from the world model |
| `manifest.json` | See below |

Every line is one JSON object with sorted keys. Every string in
the archive passed through secret redaction first.

## Manifest

```json
{
  "format": "puspa-memory-archive",
  "version": 1,
  "puspa_version": "0.1.0",
  "user_id": "<uuid>",
  "exported_at": "<ISO-8601>",
  "scope": {
    "layers": "full",
    "date_from": null,
    "date_to": null,
    "person": null,
    "project": null
  },
  "embedding": {"model_id": "hash", "dimension": 384},
  "counts": {"memory": 1, "events": 12, "graph": 8, "...": 0},
  "ledger_tips": {"<task-id>": "<last event hash>"},
  "excluded": ["vault entries: ...", "..."],
  "encrypted": true,
  "files": {
    "MEMORY.md": {"sha256": "<hex>", "bytes": 1234}
  }
}
```

Notes:

- `embedding` carries the model id and dimensions only. Vectors
  are never exported; the importer recomputes them (V2-10).
- `ledger_tips` is the hash-chain tip (last event hash) per task
  at export time. It lets an importer confirm the exported chain
  is complete before it trusts the events.
- `excluded` lists what is deliberately left out, so the gap is
  visible: vault entries (values and names), password hashes,
  session token hashes, slice-grant token references, embedding
  vectors, and other users' data.
- `archive_sha256` (the hash of the sealed file bytes) is NOT in
  the manifest: it covers the file that contains the manifest.
  It is returned by the export call and written into the ledger
  event instead.

## Encryption

- Key: PBKDF2-HMAC-SHA256, 600,000 iterations, 32-byte output,
  salt from the file header, passphrase UTF-8.
- Seal: Fernet (AES-128-CBC + HMAC-SHA256, from the
  `cryptography` library already used by the vault).
- The passphrase comes from the `PUSPA_EXPORT_PASSPHRASE`
  environment variable or an interactive prompt. It is never a
  command-line argument (arguments are visible in the process
  list) and never written to the archive, the manifest, or the
  ledger.
- An unencrypted export is possible (`--no-encrypt`, or
  `encrypted: false` on the API) for local debugging only. The
  manifest records which one a file is.

## Scope

- `layers`: a subset of `memory, events, graph, people, values,
  skills, causal, ignorance`. Omitted layers are absent from the
  ZIP and from the manifest counts.
- `date_from` / `date_to`: bound the time-ordered sections only
  (`events.jsonl` by `created_at`, graph episodes by
  `created_at`). Snapshots are exported as they stand.
- `person`: keeps only the `person:<name>` record in
  `people.jsonl`.
- `project`: keeps the episodes and ledger events of one task,
  named by its task id (a UUID string: episodes store task ids
  as UUIDs, ledger events store the same string as text).

## Ledger event

Each export writes one `evidence_recorded` event (the closed
set of 33 event types stays closed) on task
`memory-export-<user_id>`, with content:

```json
{
  "kind": "memory_export",
  "format": "puspa-memory-archive",
  "version": 1,
  "scope": {"layers": "full", "...": null},
  "counts": {"memory": 1, "...": 0},
  "archive_sha256": "<hex>",
  "encrypted": true
}
```

Counts and the archive hash only, never memory content.

## Read-back check

`verify_archive(path, passphrase)` (or
`verify_archive_bytes(data, passphrase)`) decrypts when the
file is sealed, then checks that the ZIP unpacks, that
`manifest.json` exists and parses, that the format is
`puspa-memory-archive` version 1, that every listed file is
present with a matching sha256 and byte size, and that every
manifest count matches its section. It raises `ValueError` on
a wrong passphrase, a truncated or corrupted file, a hash or
size mismatch, or a count mismatch, and returns the manifest
on success.

This check proves the archive is complete and structurally
sound. The full export-wipe-import round trip (the amnesia
test, 20 of 20) needs the import side (V2-10) and is proven
there; this format is the importer's input contract.
