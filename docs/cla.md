# CLA implementation (GitHub issue #28)

## Decision (locked 18 Nov 2026, do not reopen here)

The project uses a **CLA, not a DCO**. The CLA is lightweight and
carries **no copyright assignment**: the contributor keeps their
copyright and grants the project a broad license (use, derivative
works, sublicense) plus the right to relicense. The agreement text
is `CLA.md` at the repo root.

## Signing mechanism (one mechanism only)

**A signing comment on the pull request.** The PR author posts this
exact sentence as a comment on their own PR:

```
I have read the Puspa Contributor License Agreement (CLA.md) and I agree to its terms.
```

One signature covers that PR; a new PR needs a new comment. The bot
checks the comments on that PR only — there is no cross-PR memory,
by design (see "Why not a registry file" below).

### Why not a registry file

The alternative was a signatories file in the repo (`.github/
cla-signatures.json`) that a contributor edits in their PR. It was
rejected: anyone can add anyone else's name to a file, so a
signature in the file proves nothing about who agreed — the bot
would have to verify, for every entry, that the entry was added by a
commit authored by that same person, which is fragile and easy to
get wrong. A comment is posted by the GitHub account itself, so
GitHub's own authentication is the proof: only the account holder
could have posted it. The per-PR repetition is the price, and for a
project with few external contributors it is the honest price.

## Components

| File | What it is |
| --- | --- |
| `CLA.md` | The agreement text (version 1.0). |
| `CONTRIBUTING.md` | How to contribute, and how to sign. |
| `scripts/cla_check.py` | The checker: reads PR comments (GitHub REST shape), decides signed / exempt / not signed. Standard library only. |
| `.github/workflows/cla.yml` | The bot: runs the checker on every PR and on every new comment, labels and guides when the signature is missing. |
| `.github/cla-guidance.md` | The guidance comment the bot posts (once) when the signature is missing. |
| `tests/test_cla_check.py` | Unit tests for the checker, CLI exit codes, and the workflow YAML. |

## How the bot works

1. Trigger: `pull_request_target` (`opened`, `synchronize`,
   `reopened`) or `issue_comment` (`created`) on a pull request.
   The comment trigger exists so a signature posted after the PR
   opened is picked up without a new push.
2. The workflow resolves the PR number and author, fetches the PR
   comments with `gh api` (paginated), and runs
   `python3 scripts/cla_check.py --pr-author <login> --comments comments.json`.
3. Signed or exempt (maintainer, bot account): the job passes and
   the `cla: missing` label is removed if present.
4. Not signed: the job fails (a red check on the PR), the
   `cla: missing` label is added (created first if it does not exist
   yet), and the guidance comment is posted — once, guarded by the
   hidden `<!-- puspa-cla-guidance -->` marker, so re-runs never spam
   the thread.

Safety: `pull_request_target` runs the workflow from the base
branch, so PR code is never executed; the checkout takes the base
ref on purpose. No GitHub App and no hosted CLA service is needed:
the workflow runs entirely in-repo on the built-in `GITHUB_TOKEN`.

## The license gap (D-01, open — read this)

The repo has **no `LICENSE` file**: the project license has not
been chosen, and that choice belongs to the owner (decision D-01,
still open). This item does **not** choose a license, and no
license name appears anywhere in the CLA. `CLA.md` section 5 says
so plainly: until a license is chosen, the grants in the CLA itself
are the license for a contribution, and the CLA takes full effect as
a project-license grant once a license is chosen. Do not present
the CLA as complete project licensing until D-01 is closed.

## Verifying locally

```sh
# Unit tests for the checker (no database):
.venv/bin/python -m pytest tests/test_cla_check.py -q

# Simulated PR payloads (the test file does this too):
echo '[{"user": {"login": "alice"}, "body": "I have read the Puspa Contributor License Agreement (CLA.md) and I agree to its terms."}]' > /tmp/comments.json
.venv/bin/python scripts/cla_check.py --pr-author alice --comments /tmp/comments.json

# Workflow YAML parses:
.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/cla.yml')); print('cla.yml parses')"
```

## Honest limits

- **The workflow has never run on GitHub.** No CI has ever run in
  this repo; every check so far is local. The YAML is validated, the
  checker is unit-tested including simulated PR payloads, and every
  `gh` command is checked against the GitHub CLI shipped on the
  ubuntu-24.04 runner image — but the first real run happens on
  GitHub, on a real pull request. Until then, do not claim the bot
  works; claim only that it is built and locally verified.
- **No cross-PR memory.** A contributor signs once per PR. If that
  becomes annoying at volume, the follow-up is a signatories store
  with authorship verification — a new item, not a quiet change to
  this one.
- **Branch protection is separate.** Making this check required (so
  a PR cannot merge without it) is a maintainer step after the
  first green run on GitHub, not part of this item.
