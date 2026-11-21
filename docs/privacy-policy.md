# Privacy Policy

> **Status: minimal draft, version 0.1, 21 November 2026.**
> This document has NOT been reviewed by a lawyer. The legal review
> (ticket legal consultation) is still open. No specific data-protection law
> compliance is claimed anywhere in this document: it describes what
> the product actually does, and leaves the legal judgement to legal consultation.
> See `docs/legal.md`. If any statement here conflicts with the
> code, the code is the truth and this document must be fixed.

## 1. Who controls your data: self-hosted vs hosted

PuspaBot has one codebase and two ways of running it
(`docs/modes-decision.md`):

- **Self-hosted (Free plan).** You run PuspaBot on your own computer or
  VPS. Your data lives on hardware you control. The operator has no
  access to it.
- **Hosted (Plus, Pro, Team).** The operator runs a cloud machine
  for you. Your data lives on that machine, in PostgreSQL plus an
  encrypted vault (section 4). The operator can see usage metadata
  and cost, not your chat content or memory (section 5).

Either way, the same code runs, and the same consent text applies
(`docs/data-consent.md`).

## 2. Data collected

| Data | Why | Where it comes from |
| --- | --- | --- |
| Google account identity (name, email) | Sign-in | Google login |
| Chats with your agent | Running your tasks | You, on PWA chat, Telegram, or WhatsApp |
| Memory: profile facts, history, procedures, style examples, proposed facts | Remembering what you taught the agent | You, and the agent's observations of your tasks, subject to your correction |
| Bill customer numbers you grant (PLN, PDAM, mobile, data) | Checking bills and preparing payments | You |
| Connector tokens (Gmail, Drive, Calendar) | Reading your mail, files, and calendar on your behalf | OAuth grants you approve; stored encrypted, never in plain text |
| Your tasks, approvals, and their outcomes | Audit trail: what the agent did, why, and what you approved | The agent, written to the append-only ledger |
| Usage and cost metadata (messages, credits, model cost) | Billing, limits, and operator visibility | The system |
| Public price facts from your comparisons | The shared price graph | Public marketplace pages, stored WITHOUT your identity |

PuspaBot does not collect data from any other source. It does not buy,
scrape, or infer data about you from third parties, and it does not
sell your data.

## 3. How your data is used

- To run the tasks you ask for: price comparison, bill checks and
  reminders, drafting, and the modules you install.
- To remember context between sessions (memory), so you do not have
  to repeat yourself.
- To enforce approvals, spending limits, and the audit trail: every
  consequential action is recorded in the ledger with its evidence.
- To bill you: credits spent and tier entitlements, from the usage
  ledger (`puspa/billing/`).
- Public price facts (price, seller, product — no identity) go into
  the shared graph so other users' comparisons can use them.

Your prompts are sent to the model provider configured for your
deployment (for example OpenRouter, or your own provider key if you
bring your own). That is how the agent thinks: the text you send the
agent is processed by that provider's model. The agent never sees
your provider API key — it lives in your encrypted vault
(`docs/local-data-layer.md` describes the same boundary from the
engineering side).

## 4. How your data is protected

- **Vault.** Connector tokens are stored encrypted with a per-user
  key (envelope encryption): a 32-byte master key derives a
  per-user data key via HKDF-SHA256, encrypted with Fernet
  (`puspa/accounts/vault.py`). The master key is NEVER written to
  the database, comes from an environment variable or a file with
  mode 0600, and there is no default key: without a configured key
  every vault operation fails.
- **Ledger.** The action ledger is append-only: no `UPDATE` or
  `DELETE` on the event table, and the hash chain makes tampering
  detectable (`docs/audit-trail.md`, `docs/causal-ledger.md`).
- **Secrets.** Tokens and keys are redacted from logs and from agent
  outputs by the secret-output rail (`puspa/guardrails/rails.py`).
- **Access.** On hosted deployments, operators see metadata and
  cost only. Support access to your content needs your permission
  per case and is recorded (consent text
  `v2-2026-11-21`). There is no bulk export of user data for the
  operator.

Honest limit: these are the protections the code implements. No
independent security audit has been done, and no penetration test
has been run. The beta is small and closed for exactly that reason.

## 5. Who can see your data

- **Only you and your agent**, for chat content, memory, and tokens.
- **The operator**, for metadata and cost only (hosted plans).
- **Support**, only with your per-case permission, recorded.
- **The model provider** configured for your deployment, for the
  prompts the agent sends it (section 3).
- **Nobody else.** No advertisers, no data brokers, no analytics
  company. Public price facts in the shared graph carry no identity.

## 6. How long data is kept

- For as long as your account is active.
- Backups are kept for 14 days.
- Evidence screenshots are deleted after 30 days.
- (Consent text `v2-2026-11-21`, `puspa/consent/consent.py`.)

## 7. Your rights: access, correction, export, deletion

These are implemented in the product, not just promised here
(`docs/memory-screen.md`, `puspa/consent/account.py`):

- **Access and correction.** The Memory screen shows your stored
  facts with source and confidence. You can correct a fact, or
  forget it (`PATCH` / `DELETE /api/memory/facts/{id}`). Corrections
  and forgets are themselves recorded in the audit trail.
- **Export.** You can export all your data at any time
  (`export_account`): profile, fact history, episodes, procedures,
  summaries, style examples, consents, onboarding, and session list.
  The export does NOT include vault secrets (connector tokens) and
  does NOT include raw ledger content.
- **Deletion.** Deleting your account deletes your memory, style
  examples, procedures, summaries, slices, onboarding, consents,
  sessions, and vault (`delete_account`, via
  `users.delete_user`). **Honest limit:** ledger events that belong
  to a deleted user REMAIN — their content may contain user data.
  Deleting ledger content needs a special tombstone event type
  (proposed revision 8 / Phase 2), which is not built yet. Account
  deletion today deletes all your data OUTSIDE the ledger, not
  inside it. This limit is stated in the code itself
  (`puspa/consent/account.py` module docstring).

There is no per-feature consent withdrawal in the product yet: the
way to withdraw consent today is to stop using PuspaBot and delete
your account (see `docs/data-consent.md`).

## 8. Changes to this policy

The version number and date at the top change with the policy, and
the change is recorded. Material changes are also told to you
directly while the beta is small and closed.

## 9. Open questions for the legal review (legal consultation)

- Whether this beta needs any registration, notice, or filing
  before users outside the close circle are admitted (plan §14.10,
  question 3: obligations for the beta).
- Whether the retention and deletion behaviour in section 6 and 7
  is sufficient, including the ledger-content limit.
- Whether an age rule is needed.
- What the published contact for privacy questions should be (no
  published support contact exists yet).
