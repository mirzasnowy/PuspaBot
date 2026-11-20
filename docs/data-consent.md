# Data Consent

> **Status: minimal draft, version 0.1, 21 November 2026.**
> This document has NOT been reviewed by a lawyer. The legal review
> (ticket L-01) is still open. See `docs/legal.md`. The binding text
> is the one the product itself shows and records (section 2); this
> document explains it and must stay in sync with it.

## 1. What this is

Before Puspa stores your data, you agree to a short consent screen.
That screen is defined in code — `puspa/consent/consent.py`,
`TEXT_APPROVAL`, version `v1-2026-11-17` — and this document quotes
it exactly. If the code text and this document ever disagree, the
code text is the one you actually agreed to, and this document must
be fixed.

## 2. The consent text (quoted verbatim from the product)

```text
Puspa data consent (version v1-2026-11-17)

What data is stored: chats with your agent, memory (profile, history,
procedures), the customer numbers you grant, and your Gmail, Drive,
and Calendar tokens.

Where: on the Puspa server (PostgreSQL + encrypted vault). Tokens are
stored encrypted; the master key does not exist in the database.

How long: for as long as your account is active. Backups are kept for
14 days. Evidence screenshots are deleted after 30 days.

Who can see it: only you and your agent. Operators only see metadata
and cost, not chat content or memory. Support access needs your
permission per case and is recorded.

Export and delete: you can export all your data at any time, and
delete your account (memory, workflows, modules, and tokens are
deleted with it; audit records remain as tombstones without content).

Shared graph: public price facts from your comparisons go into the
shared graph WITHOUT your identity.

By agreeing, you allow Puspa to store and use the data above to run
your tasks.
```

Notes on the quoted text, for honesty:

- "Audit records remain as tombstones without content" describes the
  intent. What the code does today (`puspa/consent/account.py`) is
  narrower: ledger events of a deleted user REMAIN, and their content
  may contain user data. The tombstone event type that would make
  the quoted sentence fully true is proposed (revision 8 / Phase 2)
  and not built yet. The quoted text is kept verbatim because it is
  what users see; this note records the gap instead of silently
  rewording the quote.
- "On the Puspa server" means: on your own machine when you
  self-host (Free plan), or on the operator's cloud machine when you
  use a hosted plan. See `docs/privacy-policy.md`, section 1.

## 3. How consent is recorded

Giving consent is recorded twice (`record_consent` in
`puspa/consent/consent.py`):

1. In the `data_consents` table: your user id, the text version
   (`v1-2026-11-17`), the time, and the ledger event id.
2. In the append-only ledger as a `data_consent` event, so the fact
   and the version you agreed to cannot later be rewritten.

`has_consented` and `list_consents` let the product (and you, via
export) check the history of what you agreed to and when.

## 4. What you agree to, in plain terms

- Puspa may store your chats, your memory, the bill customer
  numbers you grant, and your Gmail, Drive, and Calendar tokens.
- Puspa may use that data only to run your tasks.
- Public price facts from your comparisons may go into the shared
  graph without your identity.
- Your tokens are stored encrypted; the master key is never in the
  database (`puspa/accounts/vault.py`).

Consent covers storage and use for running your tasks. It does NOT
cover: moving money (that needs a separate approval each time, and
the agent cannot execute payments at all — see
`docs/terms-of-service.md`, section 3); sharing your data with
anyone other than the model provider that processes your prompts
(see `docs/privacy-policy.md`, section 3); or any use outside your
tasks.

## 5. Withdrawing consent

There is no per-feature consent withdrawal in the product yet, and
no revoke button. The way to withdraw consent today is to stop
using Puspa and delete your account: that deletes your memory,
procedures, summaries, onboarding, consents, sessions, and vault
(`delete_account` in `puspa/consent/account.py`). The ledger limit
in `docs/privacy-policy.md`, section 7, applies: ledger event
content is not deleted by account deletion yet.

If you only want to correct or forget a single memory — rather than
delete everything — use the Memory screen: correct a fact, or forget
it, without withdrawing consent as a whole
(`docs/memory-screen-p01.md`).

## 6. When this text changes

The consent text is versioned (`TEKS_VERSI`). If the text changes,
the version changes with it, and users are asked to agree to the new
version. The old version and the record of who agreed to it stay in
the `data_consents` table and the ledger. A new text version needs
the same legal review as this one before it is shown to users
outside the close circle.
