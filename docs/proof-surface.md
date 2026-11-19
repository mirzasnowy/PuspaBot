# Proof surface (B4)

The ledger hash chain, approval receipts, and audit trail are shown
to the user, not hidden in logs. Puspa shows how it reached an
answer and what it did.

## Receipts

Every ledger event has a human-readable receipt
(`puspa.evidence.proof.Receipt`, built by `build_receipt`):

- **What happened** — one plain-language line per event type
  (`describe_event`), plus the detail the event itself carries
  (summary, purpose, or description).
- **When** — `created_at` (when the event was recorded) and
  `valid_at` (when it was true in the world, when known).
- **Hash** — the event's sha256 hash, recomputed from the stored
  fields at build time (`verified`), and the `previous_hash`
  linking it to the event before it.
- **Chain position** — the event's 1-based position in its task's
  hash chain, and the chain length (`chain_position` /
  `chain_length`). `chain_ok` says whether the whole task chain
  verified at build time.
- **Action kind** — `money`, `send`, or `other`. A receipt says
  explicitly whether the action moved money or sent something
  out, because those are the actions the user has to be able to
  check. The classification reads the markers the approval
  taxonomy (G4) already writes: the content's `action_kind` /
  `gate_kind`, an outcome record's `outcome_type`, and — for
  `tool_called` events — the tool name through
  `puspa.policy.approvals.classify_action`. Anything
  unrecognized is `other`: the receipt never claims an action
  was harmless, it only labels what it can prove.
- **Evidence** — the evidence entries linked from the event's
  content (outcome records carry their evidence list).

A receipt is a read: building one writes nothing to the ledger.

### Re-verification

`verify_receipt(ledger, receipt)` re-checks a receipt against the
live ledger and returns `(True, [])` or `(False, [reasons])`. It
fails when the stored event's recomputed hash no longer matches
the receipt, when the chain link or chain position changed, or
when the task chain no longer verifies. A tampered event — content,
time, or hash edited after the receipt was built — fails here.

## Evidence on answers

`attach_proof(answer, evidence, receipt)` returns a copy of an
answer dict with a `proof` block attached:

```json
{"proof": {"evidence": [...], "receipt": {...} | null,
           "proven": true, "missing_evidence": []}}
```

`proven` is true only when at least one evidence entry is
attached. An answer with no evidence gets `proven: false` and a
`missing_evidence` note — it is flagged, never silently shown as
proven. A receipt without evidence still leaves `proven` false:
a hash proves the event happened, not that the claim behind it
is true.

Two real paths use it:

- **Check Before You Buy (B1)** — `record_outcome` returns a
  `proof` block with the answer: the verified price-evidence
  ids, the receipt for the ledger event the answer was recorded
  as, and — when the answer could not verify anything — the
  unverified sections named (`missing_evidence`).
- **Outcome records (B2)** — `outcome_with_proof(record, ledger)`
  returns a record with its evidence and the receipt for the
  ledger event the record was written as, so the hash on the
  receipt is the hash of the record itself.

## Audit-trail export

`build_audit_trail_export(user_id, limit=100, offset=0)` builds
one user's own audit trail as a portable JSON document:

```json
{"format": "puspa-audit-trail", "version": 1,
 "user": {"id": "...", "email": "..."},
 "exported_at": "...",
 "pagination": {"total": 42, "limit": 100, "offset": 0, "returned": 42},
 "events": [{"event": {...}, "receipt": {...}}],
 "counts": {"events": 42, "total_events": 42},
 "excluded": [...]}
```

The shape reuses the K12 user export (`puspa.ledger.export`):
`format` / `version` / `exported_at` / `counts` / `excluded`.
The ownership rule is the K12 rule too: the ledger has no
`user_id` column, so an event belongs to the user when its agent
is one of the user's agents, or when its content carries the
user's id (outcome records do). Only the requesting user's
events are ever included; secret-looking text is redacted, and
the `excluded` list names what is left out.

The export is bounded and paginated: `limit` defaults to 100
and clamps to 5000 (the K12/G6 export cap `MAX_EXPORT_ROWS`),
and `pagination` carries the total so the user pages through
their whole trail instead of getting a silently cut file.
Exporting an unknown user id fails loudly instead of producing
an empty file that looks like "no data".

## PWA surface

`puspa.pwa.proof_api` mounts the routes on the PWA app
(`create_app` wires it in next to the rooms/drafts APIs). All
routes require login; receipts and outcomes of another user
return 403, unknown ids return 404, anonymous callers get 401.

- `GET /api/proof/receipts/{event_id}` — one receipt.
- `GET /api/proof/outcomes/{outcome_id}` — one B2 outcome
  record with its proof attached.
- `GET /api/proof/audit-trail?limit=&offset=` — the user's own
  audit trail, paginated.
- `GET /api/proof/audit-trail/export?limit=&offset=` — the same
  document as a JSON download.
- `GET /proof` — a minimal read page (Jinja template
  `apps/pwa/templates/proof.html`, no React build needed):
  the user's recent receipts with hashes and chain positions,
  plus the export link. It is a thin view over the same
  receipts the API serves; the full screen work (filtering,
  per-action receipt views in the SPA) is not part of this
  ticket.

## Honest limits

- A receipt proves an event is on the ledger unchanged. It does
  not prove the claim behind the event — that is what the
  attached evidence is for, and an answer without evidence is
  flagged as unproven.
- Chain verification is per task: the ledger chains per
  `task_id`, so a receipt verifies its own task's chain, not a
  global chain across tasks.
- The `/proof` page is a minimal Jinja list (newest 100 events);
  it has no filtering, no search, and no SPA screen.
- The export is a snapshot: it does not re-verify the chain at
  download time beyond the per-event checks at build time.
  Re-verify any receipt against the live ledger with the hash
  chain before relying on it.
