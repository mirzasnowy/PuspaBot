# Contributing to Puspa

Thank you for wanting to help. Puspa is a personal AI agent for
Indonesian users; the roadmap and the 15 MVP gates live in
`docs/`, and open issues are the best place to start. If you are
unsure whether an idea fits, open an issue first and ask — a short
discussion saves a wasted pull request.

## How to contribute

1. **Pick an issue.** Comment on it so two people do not build the
   same fix. If no issue fits your idea, open one first.
2. **Work on a branch** off `main`, one change per pull request.
3. **Follow the repo's own rules.** All code, docs, and commit
   messages are in English. Run the tests your change touches (see
   "Checking your change" below) and keep them green.
4. **Open the pull request** against `main`. Describe what changed,
   why, and how you tested it. Link the issue it closes, if any.
5. **Sign the CLA** (next section). The bot checks every pull
   request; a pull request without a signature cannot merge.

## Signing the Contributor License Agreement (CLA)

Every contributor signs the Puspa Contributor License Agreement in
[`CLA.md`](CLA.md). It is a short, lightweight agreement: you keep
your copyright and give the Project a broad license to use your
work, including the right to relicense and sublicense it. There is
no transfer of ownership. Read `CLA.md` before you sign; it is
short on purpose.

**To sign, post this exact sentence as a comment on your pull request:**

```
I have read the Puspa Contributor License Agreement (CLA.md) and I agree to its terms.
```

That is the whole signing process: one comment per pull request.
A new pull request needs a new comment, because the bot checks the
comments on that pull request only.

### What the bot does

A GitHub Actions workflow (`.github/workflows/cla.yml`) runs on
every pull request and on every new comment, and:

- passes when your signing comment is on the pull request;
- fails, adds the `cla: missing` label, and posts a short guidance
  comment when no signing comment from you is found yet;
- passes automatically for the Maintainer and for bot accounts
  (they never need to sign).

### If the bot still says you have not signed

- Check the comment is on the pull request itself, posted by the
  same GitHub account that opened the pull request, and that the
  sentence matches word for word — a shorter "I agree to the CLA"
  does not count, because it does not name the agreement.
- Post the sentence again (or push a new commit): either one makes
  the bot check again.
- If it still fails after that, comment on the pull request and a
  maintainer will look.

## Checking your change

The full test suite needs Postgres; for a small change, run the
tests your change touches plus the linter and the formatter:

```sh
.venv/bin/python -m pytest tests/test_<area>.py -q
.venv/bin/ruff check <files you changed>
.venv/bin/ruff format --check <files you changed>
```

Keep new code covered by tests, and do not add broad `except
Exception` handlers: the repo's blind-except ratchet
(`tests/test_blind_except_ratchet.py`) rejects them, so name the
failure mode you are handling.

## Questions

Open an issue. For a security problem, do not open a public issue:
contact the Maintainer directly instead.
