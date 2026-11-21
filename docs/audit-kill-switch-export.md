# Audit log outside the agent's reach + kill switch + export (external audit log, kill switch and export)

Kosmos external audit log, kill switch and export hardens the ledger that already existed (the append-only,
hash-chained `events` table, `puspa/ledger/ledger.py`, 31 closed
revision-7 event types). external audit log, kill switch and export adds no second ledger and no 32nd event
type. It adds three things on top of the ledger: role-based
enforcement that the agent cannot modify the log, a kill switch that
stops the agent, and a user-data export.

## Threat model

What the agent can do:

- Append events to the audit log (INSERT) and read them back
  (SELECT), through `puspa.ledger.audit_writer.AuditWriter`, which
  connects as the application role `puspa_app`.
- Read the kill-switch flag (SELECT on `kill_switch`).
- Run turns and scheduled triggers — until the flag is set.

What the agent cannot do (enforced by Postgres, not by a promise):

- UPDATE or DELETE any row of `events`. The schema revokes both
  from PUBLIC and grants the application role SELECT + INSERT only
  (`puspa/migrations/versions/0001_core.sql`). An attempt fails with
  `InsufficientPrivilege` and changes nothing.
- INSERT, UPDATE, or DELETE the `kill_switch` flag row. The role
  has SELECT only on that table.
- Arm or disarm the kill switch. Flipping the flag needs the table
  owner's connection (`KillSwitch.activate` / `deactivate`); the
  agent is never handed such a connection, and there is no
  agent-side code path that reaches either method.
- Read another user's data in an export: every export query filters
  on the user's id, and ledger events filter on the user's agent
  ids (via the `agents` -> owner mapping).
- Read secrets from an export (see below).

What this does NOT protect against:

- A database superuser / table owner tampering with the log. The
  owner can still UPDATE `events` — that is how the chain-break
  detector is tested (`tests/test_ledger.py` tampers as the owner
  and expects `verify_chain` to catch it). Detection, not
  prevention, is the defence at that level: any owner-side change
  breaks the hash chain and `Ledger.verify_chain` reports exactly
  which rows broke.
- A compromised operator connection. The kill switch and the schema
  are only as safe as the operator's credentials.
- The agent exfiltrating data it legitimately read while running.
  The audit log records what it did; it does not sandbox what it
  saw.

## Enforcement mechanism

| Rule | Where | How it is proven |
| --- | --- | --- |
| Agent cannot UPDATE/DELETE `events` | migration `0001_core.sql` (REVOKE + GRANT) | `tests/test_audit_kill_switch.py::test_agent_role_cannot_update_or_delete_events`, plus `AuditWriter.probe_tamper` |
| Agent writes only as `puspa_app` | `puspa/ledger/audit_writer.py` (`Ledger(role="puspa_app")`, `SET ROLE` per connection) | `test_audit_writer_appends_as_app_role_and_chain_verifies` |
| Agent cannot touch the flag table | migration `0001_core.sql` (SELECT only for `puspa_app`) | `test_agent_role_cannot_touch_kill_switch_table`, `test_agent_cannot_deactivate_kill_switch` |
| Agent cannot flip the flag | `puspa/ledger/kill_switch.py` (operator-only methods) | same tests as above |
| Tamper is detected if it ever happens | hash chain, `Ledger.verify_chain` | chain asserted valid after every tamper attempt |

One implementation note worth knowing: in psycopg, `conn.rollback()`
resets a `SET ROLE` back to the session user. `AuditWriter.probe_tamper`
therefore re-issues `SET ROLE` before each attempt and rolls back
afterwards, so a probe never runs as the owner by accident — and a
misconfigured grant that let an attempt through would still leave
the row unchanged (the rollback undoes it).

## Kill switch

The flag is one row (`id = 1`) in the `kill_switch` table. The row
also records the last change: who armed/disarmed it, why, and when.

- **Arming** — `KillSwitch.activate(actor, reason, ledger, task_id,
  agent_id)`. The flag flips first, then the activation is recorded
  in the ledger as a `task_stopped` event with
  `content["kill_switch"] == "activated"`. If the ledger write
  fails, the flag stays active (fail-closed, safe direction) and
  `KillSwitchError` is raised so the missing record is not silent.
- **Disarming** — `KillSwitch.deactivate(...)`, the explicit operator
  path only. Recorded as a `user_message` event with
  `content["kill_switch"] == "deactivated"` (a person telling the
  system it may run again). Both directions reuse existing event
  types; the closed set of 31 stays closed.
- **Effect** — while active, `CodingHarness.run` returns a `killed`
  outcome before the first attempt, and re-checks the flag before
  every later attempt, so a switch flipped mid-run stops the run
  before the next attempt (no new generator or executor call).
  `scheduler.tick` / `run_trigger` / `run_missed` run nothing: no
  heartbeat (a stopped scheduler must not report itself healthy),
  no run row, no schedule advance — the trigger stays due and runs
  after the switch is disarmed.
- **Restoring** — disarm with `deactivate` from an operator
  connection (same call shape as arming, with a reason). There is
  no automatic re-arm timer and no agent-side re-arm: the flag
  stays as the operator left it until an operator changes it.

Wiring: the harness takes an optional `kill_switch` argument (a
`KillSwitch`, a `() -> bool` callable, or any object with
`is_active()`); `None` means no switch is wired in and behaviour is
unchanged. The scheduler takes the same optional argument on
`tick` / `run_trigger` / `run_missed`. The shared helper
`puspa.ledger.kill_switch.is_kill_switch_active` accepts all three
shapes and raises `TypeError` for anything else, so a mis-wired
switch never silently reads as "off".

## Export

`puspa.ledger.export.export_user_data(user_id, path, dsn=None)`
writes one user's data to a single JSON file (written atomically:
temp file + rename, so a failed export leaves no half-written file)
and returns a `UserExport` with per-section row counts.
`build_user_export` builds the same document without writing a file.

Exported sections:

- `user`: id + email.
- `profile_facts`: all facts including superseded history.
- `episodic`: chat/task history (without embedding vectors).
- `procedures`, `summaries`, `slice_grants`, `style_examples`:
  memory derivatives — learned procedures, consolidation
  summaries, memory-slice grants, style examples.
- `onboarding`, `data_consents`.
- `ledger_events`: the user's ledger events, in chain order, via
  the `agents` -> owner mapping.
- `vault`: entry names + update times only.

Deliberately NOT exported (also listed in the file itself under
`excluded`, so the gap is visible, not silent):

- vault ciphertext — secret values are never exported;
- `password_hash` and session token hashes;
- slice-grant `token_ref` values (permission-token references;
  the grant record stays, the reference becomes `«withheld»`);
- episodic embedding vectors (recomputable derivatives, excluded
  for size);
- the kill-switch flag (operator state, not user data);
- any other user's data.

Two more guarantees: secret-looking text the user typed into
memory (for example a password pasted into a chat line) is redacted
with `puspa.memory.trust.redact_secrets` before it is written, and
exporting an unknown user id raises `ValueError` instead of
producing an empty file. World beliefs are not stored separately in
the export: they are deterministic derivatives of the exported
ledger events and can be rebuilt from them (noted in the file).

## Honest limits

- The owner-level tamper case is detection-only (see the threat
  model): a superuser can still change a row, and only the hash
  chain catches it afterwards.
- The kill switch stops new actions; it does not interrupt an
  action already in flight (an executor call that started before
  the flag was set runs to completion — the check happens between
  attempts, not inside one).
- The flag lives in Postgres: if the database is down, the harness
  and scheduler cannot read it. A `KillSwitch` built on a missing
  table raises `KillSwitchError` instead of reading "off" — but
  callers that never wire a switch in (`kill_switch=None`) have no
  protection at all. Wiring the switch into the production entry
  points is a deployment step, not done by this change.
- The export is a point-in-time snapshot: data written after the
  export started is not included, and there is no incremental or
  scheduled export yet.
- `AuditWriter.probe_tamper` proves the grants on the database it
  runs against. A fresh database that never ran `create_schema`
  (or ran it before the `puspa_app` role existed, so the grants
  were skipped) is not protected — run `ensure_audit_role` first,
  then `create_schema`.
