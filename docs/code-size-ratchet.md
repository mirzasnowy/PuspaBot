# Code-line size ratchet

The ratchet keeps files small by counting **code lines**, not total
lines, and refusing growth past a recorded budget. It replaces the old
total-line ratchet (2000-line hard cap / 1000-line soft cap), which
counted documentation along with code, so the best-documented files
looked the largest, and still let `provider_login.py`,
`openapi-pwa.yaml` and `hands.py` grow past it unrecorded.

Mechanism studied from nouverse/nouride `scripts/check-size.ts`
(FSL-1.1-ALv2, verified 22 Nov 2026) and rewritten as Puspa Python,
never verbatim. The TypeScript code itself was not taken: the FSL
forbids competing use, and Puspa's hosted side is competing use.

## The rule

- **Code lines, not total lines.** Blank lines and comment /
  docstring lines do not count. Counted per file kind:
  Python (tokenize plus docstring spans), JS/TS (a small scanner
  that skips comments and string contents, counting code inside
  template `${...}`), YAML (full-line `#` comments out), SQL
  (full-line `--` and `/* */` out), JSON and Markdown (non-blank
  lines, Markdown outside HTML comments).
- **Caps.** A source file may reach **400 code lines**, a test file
  **1000**, without a budget. A file over its cap without a budget
  fails CI; a new god file is refused outright.
- **Ratchet, not threshold.** Each oversized file is recorded at its
  current size in `scripts/size_budgets.py`: it may shrink, it may
  not grow. A budget more than 10 lines above its file is slack that
  must be lowered, and a file back under its cap loses its entry.
  Raising a budget is allowed, but its `why` must be rewritten to
  say what the lines are and why they belong in this file.
- **Every budget carries a short `why`** (at most 200 characters,
  one reason, today's). What the file was is in git.
- **`NEVER_BUDGETED`** lists files already split on their seams
  (for example `puspa/world/beliefs.py`, carved out of
  `model.py`, and the OpenAPI contracts split out of
  `openapi-pwa.yaml`). A budget would let them grow back into one
  god file, so past the cap the new part goes in a file of its own.

## Files

- `scripts/code_size.py` — the check: scope, caps, findings, CLI.
- `scripts/code_lines.py` — the counters, one function per file kind.
- `scripts/size_budgets.py` — the tables (`BUDGET`, `TEST_BUDGET`,
  `NEVER_BUDGETED`). This is the file that changes most.
- `tests/test_file_size_ratchet.py` — the CI enforcement plus unit
  tests for the counters and the ratchet rules.

## Running it

From the repo root:

```sh
python scripts/code_size.py            # exit 1 with findings
python scripts/code_size.py --report   # every file over a cap
python -m pytest tests/test_file_size_ratchet.py --noconftest -q
```

The test runs in CI's dependency-free `checks` lane
(`.github/workflows/ci.yml`), standard library only, no database.

## Adding a budget

When a file crosses its cap, either split it on a seam, or record
it at its current size with one short `why` in `BUDGET` (or
`TEST_BUDGET` for a test file). Measure the size first with
`--report` so the budget records the size the file is now, not a
guess. If the file was already split on its seam, do not budget
it: put it in `NEVER_BUDGETED` and split the new part out instead.
