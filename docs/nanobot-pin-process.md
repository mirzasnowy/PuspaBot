# Nanobot pin process (nanobot pin process, plan section 9.4)

PuspaBot uses nanobot (HKUDS/nanobot, MIT) as its agent chassis through a
git pin, not a fork: zero lines of nanobot upstream code are changed,
and all PuspaBot behaviour lives in the wrapper interface in
`puspa/chassis/` (`PuspaTool`, `PuspaRegistry`, `PuspaHook`,
`PuspaSkillsLoader`, `SandboxTool`, `assemble`). This document is the
process that keeps that pin honest: how it moves, what must pass
before it moves, and when the pin policy stops and the chassis becomes
a fork.

## Current state (honest, 2026-11-20)

- Current pin: `e767174` (see `requirements.txt` and
  `puspa/chassis/pin.json`; the two must agree — the bump script
  refuses to run when they disagree).
- The monthly bump workflow (`.github/workflows/nanobot-bump.yml`)
  and the weekly advisory watch
  (`.github/workflows/nanobot-advisory-watch.yml`) have never run on
  GitHub yet. **No live bump PR exists.** The upstream state in
  `pin.json` was fetched live once, by hand, while nanobot pin process was built
  (2026-11-20): latest upstream commit
  `3e9024ea043e30b7e5e40167f6b69b194247c822`, so a bump is available,
  and one open advisory affects the pin, `GHSA-434r-7c99-hwf3`
  (medium, published 2026-06-01, SSRF in the `web_fetch` tool),
  which lags more than 30 days. Only one patch lags, so the fork
  rule has not fired. The bump itself — the PR — still waits for
  the monthly workflow and a human merge.
- Monkeypatch audit (2026-11-20): zero monkeypatch sites in `puspa/`
  (no attribute assignment on an imported nanobot object, no
  `setattr` on one). The wrapper uses only documented extension
  points: constructor parameters, hooks, the registry, and subclassing.

## 1. The monthly bump bot

Schedule: the 1st of each month, 03:00 UTC
(`.github/workflows/nanobot-bump.yml`, plus manual dispatch).

What the bot does, in order:

1. Runs the chassis contract lane against the CURRENT pin. The lane
   must be green before the pin moves; a red lane means the current
   pin is already broken, and bumping on top of it hides the break.
2. Runs `scripts/nanobot_bump.py --check`, which fetches the newest
   commit on upstream `main` and the public security advisories for
   the `nanobot-ai` package from the GitHub Advisory Database, and
   prints a JSON report (bump available? advisories open? advisories
   lagging? fork rule fired?).
3. When a bump is available, applies it with
   `scripts/nanobot_bump.py --apply` — the pin line in
   `requirements.txt` and the record in `puspa/chassis/pin.json`
   (new pin, new `pinned_on`, new `last_checked_on`, advisories
   recorded with the new pin as their fix) — and opens a pull request
   to the new pin.
4. A human merges the PR. The bump PR never merges itself. Merge
   requires the chassis contract lane green on the PR's pin (the
   `chassis-contract` workflow runs on the PR automatically) and the
   merge checklist in section 4 below.

The bot opens exactly one PR per month, to the latest upstream commit
at that time. It does not chase every upstream commit, and it does not
rebase an open bump PR: if a bump PR is still open when the next month
arrives, the new run closes the stale one first (its pin is already
out of date) and opens a fresh one.

Local use: `python scripts/nanobot_bump.py --check` reports without
changing anything; `--apply` moves the pin; `--open-pr` (with
`--branch`) opens the PR through the `gh` CLI when it is available
and authenticated, and otherwise prints a warning and leaves the
branch for a human to open from.

## 2. Upstream security advisory watch

Schedule: every Monday, 03:00 UTC
(`.github/workflows/nanobot-advisory-watch.yml`, plus manual dispatch).

The watch runs `scripts/nanobot_bump.py --advisories-only`: it
fetches the public advisories affecting `nanobot-ai` and reports how
many are open and how many lag. It watches; it does not bump — the
monthly workflow owns the pin.

- No open advisories: the run passes quietly.
- Open advisories, none lagging: the run passes; the report in the log
  tells the next monthly bump what to pick up.
- The fork rule fires (section 5): the run opens an issue, because
  the pin policy has stopped and a human has to decide.

Advisory state is also recorded in `puspa/chassis/pin.json`
(`advisories_open`), so the record — not anyone's memory — is where
the next bump looks first.

## 3. The chassis contract lane

The lane is a CI job that runs ONLY the wrapper-interface tests
against a candidate pin, before the full suite
(`.github/workflows/chassis-contract.yml`, list in
`puspa/chassis/contract.py`, runner
`scripts/run_chassis_contract.py`):

| Surface | What it proves | Test files |
|---|---|---|
| `PuspaTool` wrapping | Permission token, skill signature, outbound gate, audit, injection scan | `tests/test_tool_result_compression.py`, `tests/test_plugin_wiring.py` |
| Backstop registry | `PuspaRegistry.prepare_call` refuses unlisted tools; `get_definitions` hides them | `tests/test_plugin_wiring.py`, `tests/test_integration.py` |
| Ledger hook | `PuspaHook` cycle and task records reach the ledger | `tests/test_guardrails.py`, `tests/test_integration.py` |
| Skill loader | `PuspaSkillsLoader` signature checks and revocation | `tests/test_skill_revocation.py`, `tests/test_skills_hooks.py` |
| Composition root | `assemble` / `build_registry` / `build_hook_factory` wire the above together | `tests/test_factory_rails.py`, `tests/test_integration.py` |

The lane runs on every pull request that touches the chassis, the
pin, or the lane itself, and on manual dispatch with a candidate pin
(`nanobot_pin` input): the workflow installs that pin over the pinned
one and runs the lane against it. The lane is the gate for a bump PR:
green means the wrapper interface still holds against the new pin;
red means the pin does not move, and the failure is diagnosed before
anything else (usually an upstream change to a hook signature, a tool
base class, or the registry — see the changelog checklist).

Local use: `python scripts/run_chassis_contract.py` runs the same
set. The database DSN comes from the environment (`PUSPA_TEST_DSN`),
exactly as the full suite reads it.

## 4. Merge checklist for a bump PR

Before a human merges a bump PR, every item must hold:

- [ ] The chassis contract lane is green on the PR's pin.
- [ ] The full suite is green on the PR (the coordinator's central
      run, not just the lane).
- [ ] The changelog checklist (`docs/nanobot-changelog-checklist.md`)
      is filled in for the upstream range: tools, sessions, and hooks
      changes reviewed, even when the answer is "no change".
- [ ] `puspa/chassis/pin.json` matches the PR's pin and records the
      advisories the bump picks up.
- [ ] The canary procedure (`docs/nanobot-canary-rollback.md`) is
      scheduled: one container first, then the rest.
- [ ] No new monkeypatch site was needed to make the lane pass. If
      one was, count it toward the fork rule (section 5) instead of
      hiding it.

## 5. Fork rule

The pin policy stops — and the chassis becomes an admitted fork —
when either condition holds:

1. **Two or more upstream security patches lag more than 30 days.**
   A patch lags when its advisory is published, unfixed in our pin,
   and older than 30 days. The count is computed by
   `puspa/chassis/pin.py:evaluate_fork_rule` from the advisory list
   in `pin.json`; the bump report quotes its reasons.
2. **The wrapper needs monkeypatches in more than 5 places.** A
   monkeypatch is runtime patching of a nanobot object (attribute
   assignment on an imported nanobot module, class, or instance) from
   PuspaBot code — as opposed to a documented extension point. Five is
   deliberately small: a chassis that needs six separate patches is
   no longer a clean dependency, it is a fork hiding behind a pin.
   The bump script counts sites heuristically
   (`count_monkeypatch_sites`); the manual audit in this document is
   the authority, and a reviewer passes the audited count with
   `--monkeypatch-sites`.

When the rule fires:

1. Stop bumping. The bump workflow opens an issue instead of a PR,
   and the advisory watch does the same.
2. Create a clearly-named vendor subtree, `vendor/nanobot/`, copied
   from the last good upstream commit, with its own `README.md` that
   states: this is a fork, which upstream commit it came from, and
   why the pin policy stopped (quote the fork-rule reasons).
3. Set `forked: true` and `vendor_path: "vendor/nanobot"` in
   `puspa/chassis/pin.json`, and record the decision in the status
   doc. Do not keep calling it a pin once it is a fork.
4. Re-pin the dependency against the subtree (a path install in
   `requirements.txt`) and keep the chassis contract lane running
   against it — the lane is what proves the fork still honours the
   wrapper interface.

The fork is a deliberate, recorded decision, never a silent drift.
Until the rule fires, there is no fork and no vendor subtree: do not
create one "just in case".

## Files

- `puspa/chassis/pin.py` — pin record, advisory lag, fork rule.
- `puspa/chassis/pin.json` — the recorded pin state.
- `puspa/chassis/contract.py` — the chassis contract test list.
- `scripts/nanobot_bump.py` — the bump bot (check / apply / watch).
- `scripts/run_chassis_contract.py` — the contract lane runner.
- `.github/workflows/nanobot-bump.yml` — monthly bump.
- `.github/workflows/nanobot-advisory-watch.yml` — weekly watch.
- `.github/workflows/chassis-contract.yml` — the contract lane.
- `docs/nanobot-changelog-checklist.md` — per-bump changelog checklist.
- `docs/nanobot-canary-rollback.md` — canary-first rollout and rollback.
