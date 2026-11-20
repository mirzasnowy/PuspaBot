# Status archive: Wave 28 (K11 + K12) — 19 Nov 2026

Moved verbatim from `docs/status-maturity.md` on 20 Nov 2026 (Wave 45),
when the live log neared the 1000-line soft cap. Nothing here was
edited; the live log keeps Wave 30 onwards.

## Wave 28 — Kosmos K11: per-area autonomy dial (DONE 19 Nov 2026)

K11 is the per-area autonomy dial: trust per action area,
moving with prediction calibration, with an emergency drop
that is instant. New package `puspa/autonomy/` (`dial.py`,
543 lines), deterministic, no LLM, in-memory by deliberate
choice (see the honest limits). First item built under the
2-worker mode: worker 1 in worktree `build-w1` on
`puspa_test_w1`; the coordinator verified, merged, and runs
the suite in `build/` on `puspa_test`.

Areas: `money`, `messaging`, `files` (irreversible);
`scheduling`, `system` (reversible); `accounts`
(irreversible + capped at 0.59, so it never rises above
`propose`). Every area starts at 0.50 (`propose`): trust is
earned, never assumed. A correct outcome closes 5% of the
remaining gap to 1.0 (slow rise: 5 correct in a row reach
`do_and_show` at 0.613; 24 reach `do_silently` at 0.854).
An ordinary wrong outcome drops the score by 0.15, floored
at 0.0 (fast fall on purpose: 1 wrong from 0.85 lands at
0.70; 2 wrong land at 0.55, back to `propose`). A serious
failure is the emergency drop: in an irreversible area the
score falls to 0.0 at once (rebuild: 18 correct to
`do_and_show`, 37 to `do_silently`); in a reversible area
it falls by 0.30. A serious failure is an action that
failed harmfully, or an irreversible action that went
wrong — not a plain tool error (that is an ordinary wrong
outcome). `reset_area` is an operator action (tests, or a
human resetting trust after a review); the agent itself
never calls it. Each area keeps an audit history (cap 100
records): the dial's audit lives there, not in the ledger,
by design (no ledger event type for dial moves, keeping
the 31 revisi-7 types closed, same pattern as K10).

Calibration uses signals that already exist: the dial does
not score predictions itself, it only aggregates outcomes
already scored per area through `record_outcome` (the
world-model `WorldModel.stats` scores, the K5 per-contact
accuracy, and the learning-loop verdicts are its sources;
the turn loop is the first feeder, from tool-execution
results). The wiring chosen is the turn loop (same advisor
pattern as the K1 intent advisor), not approvals G4: one
clear gating path, and the dial feeds back from execution
results. `TurnLoop` takes an optional `autonomy_dial` (+
`dial_area_for_tool` overrides); the dial is consulted in
`_run_tool_calls` AFTER the intent advisor, so an intent
`ask` wins. At `propose` the tool does NOT run: the turn
records a `dial_ask` event and returns a
`dial_ask_payload` (`tool_was_run: false`). At `do_and_show`
the tool runs and the turn records `dial_show`; at
`do_silently` it runs and records `dial_silent` (the model
still sees the tool result, so it can continue the turn;
the presenting surface must honour the `show: false` flag
— the loop itself does not silence the model's narration).
Every executed outcome feeds the dial (`record_outcome`,
correct = ran as expected, wrong = error), wrapped so a
dial failure never breaks the turn; a broken dial fails
open (the call runs as if no dial were wired in). Verdicts
land on `TurnResult.dial_decisions`.

Files (worker 3afbe2f2 in build-w1, merged by the
coordinator): created `puspa/autonomy/dial.py` (543 lines),
`puspa/autonomy/__init__.py` (45 lines),
`tests/test_autonomy_dial.py` (584 lines, 36 tests),
`docs/autonomy-dial.md` (148 lines); changed
`puspa/turns/loop.py` (859 -> 969, still under 1000).
`puspa/world/model.py` (1,395) and `puspa/world/agents.py`
untouched. No new ledger event type (31 stay closed).

Verification (coordinator, re-ran herself in build-w1 on
`puspa_test_w1`): module tests
`tests/test_autonomy_dial.py`: 36 passed, including the
immediate-drop test (climb to `do_silently` with 24
correct, `record_serious_failure`, next turn blocks
`gmail.send` with an empty registry and payload
`tool_was_run: false`). Neighbour regression (autonomy
dial / intent turn wiring / approvals taxonomy / turn loop
/ turn intercepts): 148 passed. Ruff check + format:
clean. Mypy: clean. Read in full: the dial rules
(`record_outcome`, `record_serious_failure`, `_stage_for`)
and the loop wiring (`_run_tool_calls`,
`_consult_autonomy_dial`, fail-open).

Worktree suite (worker, build-w1, `puspa_test_w1`):
3,427 passed, 23 skipped, 1 failed, 36m21s, EXIT=1 —
total 3,428 tests, exactly 3,392 (Wave 27) + 36 K11. The
single failure was `tests/test_backup.py::
test_create_and_backup_items` asserting the manifest
database equals the hardcoded `"puspa_test"`, while the
backup manifest writes the real DSN database
(`puspa_test_w1`). Pre-existing environment mismatch, not
a K11 effect (the worker touched no backup code). Fixed
by the coordinator in `tests/test_backup.py` (rides with
this wave): the manifest assertion now reads the dbname
from the test DSN, and the restore database is derived
per-DSN (`<dbname>_restore`) instead of the shared
`puspa_test_restore`, so two worktree suites can no longer
race on it. Verified in build-w1: `tests/test_backup.py`
4 passed (including the restore test against
`puspa_test_w1_restore`).

Suite (coordinator, SOLO in build/ on `puspa_test`): 3,444
passed, 23 skipped, 1 warning, 21m52s, EXIT 0. The first
K11-only attempt was SIGTERM'd by a VM replacement at 54%
(no failures visible); the verification re-ran as one
combined run after the K12 merge, log
hidden_files/suite-wave28-k12.log.
Reconciliation: 3,392 (Wave 27) + 36 (K11 module) + 16 (K12
module) = 3,444.

Honest limits: the dial is in-memory — a restart returns
every area to 0.50 (the safe direction: only more
cautious, never bolder). Postgres persistence is a
follow-up. The calibration signal today is tool-execution
results (ran / errored), a proxy, not proof the prediction
was right; a stronger feeder (K8 outcome watcher) is
planned to use the same `record_outcome` entry point. At
`do_silently` the loop records `show: false` and
`dial_silent`, but the model still sees the tool result;
the presenting surface must honour the flag. No LICENSE or
billing model decision was made.

Remaining: Kosmos K12, K13, then B features
(B6, B9, B10, B11, B12, B13), then B15 culture LAST. Then uji
+ 15 gates + 10 reference tasks (waiting on Nero's decisions).
## Wave 28 (continued) — Kosmos K12: audit log outside agent reach + kill switch + export (DONE 19 Nov 2026)

K12 is the audit log the agent cannot touch, the kill switch
that stops it, and the export that lets the user take their
data home. Second item of the 2-worker mode: worker 2 in
worktree `build-w2` on `puspa_test_w2`; the coordinator
verified, merged, and runs the suite in `build/` on
`puspa_test`. (The worker's own suite hit the same backup
mismatch as K11's — see the K11 section above; the fix
rides with K11 and is already in `build/`.)

Audit log: no second ledger — the existing `puspa/ledger/`
hash chain is hardened. New `AuditWriter`
(`puspa/ledger/audit_writer.py`, 108 lines): a `Ledger`
bound to the `puspa_app` role (`SET ROLE` right after
connecting; `Ledger` takes an optional `role`, validated
against a role-name regex because `SET ROLE` takes an
identifier, never a parameter). Postgres itself enforces
the role: `events` is SELECT + INSERT only for
`puspa_app` (UPDATE/DELETE revoked, pre-existing in
`schema.sql`); the agent role can append and read, never
rewrite history. `probe_tamper` proves it: it attempts
UPDATE and DELETE on a log row as the agent role and
reports both blocked (`InsufficientPrivilege`); each
attempt runs in its own transaction and is rolled back
afterwards, and the probe re-issues `SET ROLE` before
each attempt because a rollback resets the role to the
session user — a psycopg behaviour documented in
`docs/audit-kill-switch-export.md`. `ensure_audit_role`
creates the role when missing (operator step, superuser);
the schema grants apply only when the role already
exists, so the role must exist before `create_schema`
runs — the test suite's shared fixture does this once per
session.

Kill switch: one row in a new `kill_switch` table
(`schema.sql` 76 -> 100 lines; `puspa/ledger/kill_switch.py`,
304 lines). The application role has SELECT only on the
flag table; only the table owner (the operator
connection) can INSERT/UPDATE it, so the agent can
neither arm nor disarm it. `KillSwitch.activate` /
`deactivate` are explicit operator calls (actor +
reason required, both non-empty); the flag flips FIRST
and the ledger write follows, so if the ledger is
unreachable the agent still stops and `KillSwitchError`
is raised (fail-closed, safe direction). Activation is
recorded as `task_stopped`, deactivation as
`user_message` — existing types only, the 31 revisi-7
types stay closed. The harness
(`puspa/harness/loop.py`, 788 -> 821 lines) takes an
optional `kill_switch`: a new `STATUS_KILLED` outcome,
checked before the run, before each attempt, and in
plan/answer modes, so a switch flipped mid-run stops the
run before the next attempt (0 generator/executor calls
when active from the start). The scheduler
(`puspa/scheduler/scheduler.py`, 410 -> 444 lines) takes
`kill_switch` in `tick` / `run_trigger` / `run_missed`:
while active, `tick` is a total no-op (no heartbeat) and
triggers stay due. `is_kill_switch_active` accepts None
(no switch wired: behaviour unchanged), a callable, an
object with `is_active()`, or a boolean `.active`
attribute — anything else raises `TypeError` instead of
silently reading as "off". A missing `kill_switch` table
raises `KillSwitchError`, never reads as "off".

Export: `puspa/ledger/export.py` (227 lines) —
`export_user_data` / `build_user_export` writes one
portable JSON file, atomically, with 10 sections
(profile, episodic, procedural, summaries, slice
grants, style examples, onboarding, consent, ledger
events, vault metadata) and an explicit `excluded` list
so the gap is visible, not silent. Never any secrets:
no `password_hash`, no session token hashes, no vault
ciphertext (entry names + update times only), no slice-
grant `token_ref` values (written as `«withheld»`), and
every string passes through `redact_secrets`, so a
password the user pasted into a chat line is redacted
(`«redacted»`). Export is a point-in-time snapshot; no
incremental/scheduled export yet.

Files (worker 9b6c42d8 in build-w2, merged by the
coordinator): created `puspa/ledger/kill_switch.py`
(304 lines), `puspa/ledger/audit_writer.py` (108 lines),
`puspa/ledger/export.py` (227 lines),
`tests/test_audit_kill_switch.py` (492 lines, 16 tests),
`docs/audit-kill-switch-export.md` (170 lines); changed
`puspa/ledger/schema.sql` (76 -> 100),
`puspa/ledger/ledger.py` (457 -> 483),
`puspa/ledger/__init__.py` (79 lines),
`puspa/harness/loop.py` (788 -> 821),
`puspa/scheduler/scheduler.py` (410 -> 444). No file
over 1000 lines; `puspa/world/model.py` (1,395) and
`puspa/world/agents.py` untouched. The merge deliberately
did NOT copy w2's `puspa/turns/loop.py` (K11's version in
`build/` is newer), `tests/test_backup.py` (the
coordinator's DSN-aware fix in `build/` is newer), or
`tests/test_file_size_ratchet.py` (the archive note in
`build/` is newer).

Verification (coordinator, re-ran herself in build-w2 on
`puspa_test_w2`): module tests
`tests/test_audit_kill_switch.py`: 16 passed. Neighbour
regression (ledger / event types / audit trail /
bitemporal / coding harness / harness modes / harness
unified / scheduler push / RLS / access): 188 passed.
Ledger/audit set (K12 module + ledger + event types +
audit trail): 72 passed. Ruff check + format: clean.
Mypy: clean. Read in full: `kill_switch.py` (flag
lifecycle, fail-closed reads, flip-before-record),
`audit_writer.py` (SET ROLE + `probe_tamper`), the
`schema.sql` grants (events SELECT+INSERT only,
kill_switch SELECT only), the harness + scheduler
wiring, and the export exclusions (verified
`password_hash` / ciphertext / `token_ref` handling +
`redact_secrets` in code). Event types still 31
(checked live).

Worktree suite (worker, build-w2, `puspa_test_w2`):
3,407 passed, 23 skipped, 1 failed, 36m49s, EXIT=1 —
total 3,408 tests, exactly 3,392 (Wave 27) + 16 K12. The
single failure was the same backup-test mismatch as
K11's (hardcoded `"puspa_test"` vs the real DSN
database `puspa_test_w2`); fixed by the coordinator in
the K11 merge.

Suite (coordinator, SOLO in build/ on `puspa_test`, log
hidden_files/suite-wave28-k12.log): 3,444 passed, 23 skipped,
1 warning, 21m52s, EXIT 0.
Reconciliation: 3,392 (Wave 27) + 36 (K11 module) + 16 (K12
module) = 3,444.

Honest limits: tampering by the owner/superuser is only
DETECTED (the hash chain breaks and `verify_chain`
flags the row), not prevented — prevention at that
level is detection. The kill switch stops new actions,
not an action already running (checks run between
attempts, not inside one execution). The flag lives in
Postgres: if the database is down the switch cannot be
read, and a missing table raises `KillSwitchError`
instead of reading "off". A caller that does not wire a
switch (`kill_switch=None`) has no protection — wiring
it into the production entry point is a deployment step,
not part of this change. `probe_tamper` proves the grants
on the database it runs against; a new database whose
`create_schema` ran before the `puspa_app` role existed
is unprotected — run `ensure_audit_role` first, then
`create_schema`. The `kill_switch` table is not
TRUNCATE'd by the shared conftest fixture; the K12
module-test fixture resets the flag itself before/after
each test. No LICENSE or billing model decision was made.

Remaining: B features (B11, B12, B13), then B18
(second-brain graph view), then culture LAST (B15, B16, B17, in
that order). Then uji + 15 gates + 10 reference tasks (waiting
on Nero's decisions).
