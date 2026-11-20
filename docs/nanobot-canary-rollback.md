# Nanobot canary-first rollout and rollback (E-19, plan section 9.4)

Every nanobot pin bump rolls out canary-first: one container runs the
new pin while the rest stay on the old one, and the rollout only
continues when the canary proves itself. Rollback is a return to the
previous pin — the same mechanism as the bump, in reverse, with no
new code.

## 0. Terms

- **Old pin**: the pin in production before the bump (the commit in
  `puspa/chassis/pin.json` on `main` before the bump PR merges).
- **New pin**: the pin in the merged bump PR.
- **Canary**: one production container running the new pin. One
  container, not one user and not one region: the canary is a whole
  container so the chassis, the sandbox, and the ledger all run the
  new pin together.
- **Fleet**: every other production container, still on the old pin
  until the canary passes.

## 1. Canary-first procedure

### 1.1 Before the canary starts

1. The bump PR is merged: the chassis contract lane and the full
   suite are green on the new pin, and the changelog checklist
   (`docs/nanobot-changelog-checklist.md`) is filled in.
2. Write down the old pin (from `pin.json` on `main` before the
   merge) and the new pin. The rollback in section 2 needs the old
   pin exactly; do not rely on remembering it.
3. Confirm a fresh backup exists (the backup drill in
   `docs/backup-restore-drills.md` is the reference). A pin rollback
   does not need a data restore, but a rollout never starts without
   a known-good backup.

### 1.2 Start the canary

1. Build the deploy image from the merged `main` (the same
   `Dockerfile` the CI sandbox lane builds) and start exactly one
   container from it, beside the fleet — do not replace a fleet
   container with it.
2. Route a small share of traffic to the canary (about 5% of new
   turns, or one operator account if traffic splitting is not
   available). The canary must serve real turns: a canary that only
   answers health checks proves nothing.
3. Tag the canary's ledger events with the new pin (the ledger sink
   in `puspa/chassis/context.py` already records the tool calls; the
   pin goes in the deploy record, not in every event — the deploy
   record is what ties a time window to a pin).

### 1.3 Watch the canary (minimum 24 hours)

Check each of these, for the canary only, against the same window on
the fleet:

| Signal | Where to look | Pass bar |
|---|---|---|
| Tool refusals | Ledger `tool_dipanggil` / `tool_selesai` denial reasons | No new denial reason appears on the canary that does not appear on the fleet |
| Ledger hook writes | Ledger task records (`PuspaHook` cycle writes) | No missing cycle or task records for canary turns |
| Skill loads | Skill loader rejections | No signed skill rejected, no unsigned skill admitted |
| Turn errors | Turn loop failures, hook exceptions | No error class that is new to the canary |
| Latency | Turn completion time | No sustained regression vs the fleet |

Any row that fails is a canary failure, not a tuning note. Do not
"watch it a bit longer" past 24 hours without a written reason: a
canary that needs more time is a canary that has not passed.

### 1.4 Promote or roll back

- **All rows pass for 24 hours**: roll the new pin out to the fleet
  one container at a time, watching the same signals after each
  container. When the last fleet container is on the new pin, update
  `pin.json`'s `last_checked_on` (the bump already set it) and close
  the bump PR's rollout note.
- **Any row fails**: roll back immediately (section 2). Do not try
  to fix the wrapper against the new pin while the canary is serving
  traffic.

## 2. Rollback procedure (back to the previous pin)

Rollback is the bump in reverse: the previous pin is already known,
already tested, and already recorded. It takes one command sequence,
no new code, and no data migration.

1. Stop the canary container. The fleet is still on the old pin, so
   stopping the canary restores the whole service to the old pin at
   once.
2. Record the rollback: open a revert PR that sets the pin in
   `requirements.txt` and `puspa/chassis/pin.json` back to the old
   pin, with the canary failure (which signal, which window, what the
   fleet showed) in the PR body. If the fleet was already partly
   promoted, the revert PR is what moves those containers back: build
   the image from the revert PR and replace the promoted containers
   one at a time, as in section 1.4.
3. Do not re-attempt the same new pin until the failure is
   understood: file the upstream issue or the wrapper fix first, and
   let the next monthly bump (or a manual dispatch of the bump
   workflow) carry the corrected pin. A pin that failed canary twice
   in a row is treated as a fork-rule signal — count the workarounds
   it needed toward the monkeypatch limit in
   `docs/nanobot-pin-process.md` section 5.

## 3. What rollback is not

- It is not a data restore: the pin change touches no stored data
  (sessions, ledger, memory), so rolling back the pin needs no
  restore. If stored data is itself the problem, that is a different
  incident — use the backup restore runbook, not this procedure.
- It is not a fork: returning to the previous pin keeps the pin
  policy. The fork rule (two or more security patches lagging over
  30 days, or more than 5 monkeypatch sites) is a separate decision
  with its own procedure in `docs/nanobot-pin-process.md` section 5.

## Honest limits

- This procedure has never been run against a real container host:
  there is no production fleet yet, and the canary has never been
  started. The signals table names real ledger events and real
  wrapper surfaces, but the 5% traffic split and the 24-hour window
  are choices to validate on the first real rollout, not measurements
  from one.
- The deploy image build referenced here is the one the CI sandbox
  lane builds (`Dockerfile`, E-05); the container host itself (the
  Contabo VPS decision) is still open.
