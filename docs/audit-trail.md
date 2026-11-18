# Audit trail with initiator (G6)

The audit trail is the ledger (Buku Kerja) plus three additions in
`puspa/ledger/audit.py`. None of them adds a 32nd event type — the
closed set of 31 types is untouched.

Pattern source (studied, rewritten as Puspa code, never copied
verbatim — see `NOTICE.md` and `REUSE.md`): OpenBot
`app/src/lib/audit/` + `api/openapi/audit-events.yaml` +
`shared/audit-words.ts` (MIT).

## 1. Initiator attribute

Every event content may carry `initiator: {"kind", "id"}` — who or
what started the run:

| Kind | Meaning |
| --- | --- |
| `person` | A person started it |
| `deployment` | A deployment / server start started it |
| `routine` | A scheduled routine started it |
| `responsibility` | A goal / responsibility started it |
| `memory` | A memory (for example a dream / consolidation) started it |
| `handoff` | A handoff from another agent started it |

`Ledger.append_event(..., initiator={"kind", "id"})` validates the
kind and stores it inside the event content — an attribute, not a
new event type. `attach_initiator(content, initiator)` does the
same for a plain content dict (it copies; it never mutates).
`initiator_of(event)` reads it back.

### "Nobody watching"

The filter is routine + handoff + responsibility + memory: runs no
person is watching. `is_nobody_watching(event)` and
`filter_nobody_watching(events)` implement it. `person` and
`deployment` runs are watched by definition (a deployment is
started by an operator's deploy, and the filter follows OpenBot's
list exactly).

## 2. Handover as control events

`ControlState` holds who controls the browser right now: the bot
(`controller is None`) or a human (`controller` is their id).
Handover steps are audited through existing handoff types, with a
`control` attribute naming the step:

| Handover step | Ledger event type | Content |
| --- | --- | --- |
| `help_requested` | `handoff_proposed` | `{"control": "help_requested", "actor", "reason", "initiator"?}` |
| `control_taken` | `handoff_handed_over` | `{"control": "control_taken", "actor", "initiator"?}` |
| `control_released` | `handoff_accepted` | `{"control": "control_released", "actor", "initiator"?}` |

The events go through the injected `log` callable
(`log(event_type, content)`, the shape `Gate` and `PuspaTool` use);
without a log, the state still changes but nothing is recorded.

While a human controls the browser, bot actions are **rejected,
not queued**: `bot_action_allowed()` is False, and
`reject_bot_action(action)` raises `ControlRejected`. The action is
dropped; nothing holds it for later.

## 3. Export guards + self-audit

`export_events_csv(events, *, exporter, filters, log)`:

- **At most 5,000 rows** (`MAX_EXPORT_ROWS`). A cut export starts
  with the marker header `# Puspa-Export-Truncated: true` and the
  result reports `truncated=True`, `rows` written and `total_rows`
  matched.
- **Formula-injection guard.** Any cell starting with `=`, `+`,
  `-` or `@` gets a `'` prefix (`sanitize_cell`).
- **Secrets redacted.** Content values are redacted before the
  JSON is written (same rule as
  `puspa.memory.trust.redact_secrets`: `api_key` / `secret` /
  `token` / `password` assignments become `«redacted»`), and the
  content cell stays valid JSON after redaction.
- **Saved files contribute path + size, never content.**
  `file_audit_entry(path)` returns `{"path", "size_bytes"}`.
- **Every export is itself recorded.** `log` (an
  `AuditExportLog`) is required: exporting without one raises.
  The record holds who exported, how many rows, whether it was
  truncated, and which filters applied — never the rows themselves.

### Where the export record lives (a chosen trade-off)

The export record does **not** go into the core ledger: it is not
a new event type (the 31 are closed), and shoehorning it into an
unrelated existing type (for example `evidence_recorded`) would
lie about what happened — an export is not evidence. It goes into
a separate append-only export log, `AuditExportLog`: one JSONL
file (`audit-exports.jsonl`) in a directory, each record chained
to the previous one by hash (`GENESIS` for the first), verifiable
with `verify_chain()`. The core ledger stays the single source of
truth for what Puspa did; the export log is the record of who
looked at it.

## Retention

`AUDIT_RETENTION_DAYS` environment variable, in days. Unset means
forever. `audit_retention_days()` reads it (an invalid value
raises instead of silently picking a number). `AuditExportLog.prune()`
drops export records older than the window and returns the pruned
ids. Retention prunes the export log only — never the core ledger.

Pruning truncates the head of the export log's hash chain, so
`verify_chain()` anchors at the oldest *kept* record's previous
hash: the chain from there on is fully checked, but what came
before the oldest kept record can no longer be verified. That is
inherent to retention pruning, not a silent gap.

## Honest limits

- **The initiator is only as truthful as its caller.** Nothing
  verifies that a run really was started by the recorded person or
  routine; a caller can attach any initiator. The attribute is an
  audit claim, not a proof.
- **"Nobody watching" is a filter, not a guarantee.** A routine
  run can still be watched by a person who happens to be looking;
  the filter only says no person *started* it.
- **Export log is a second store.** It is a plain JSONL file with
  a hash chain, not a database table: concurrent writers in two
  processes can interleave appends, and there is no locking. The
  core ledger (Postgres, advisory lock per task) does not have this
  problem.
- **Pruning loses the chain head.** See retention above.
- **Redaction is pattern-based.** Only the secret-assignment
  patterns are redacted; a secret in a free-form sentence without
  a key (`sk-live-...` standing alone) is not caught. The same
  limit applies to `puspa.memory.trust.redact_secrets`.
- **No UI.** The export is a function returning CSV text; there is
  no download endpoint or viewer yet. The export log directory is
  the caller's choice (no default path is assumed).
- **Control state is in-memory.** `ControlState` does not survive
  a restart, and it is not shared between processes. A restart
  while a human controls the browser hands control back to the bot
  silently — the handover events in the ledger are the only record.
