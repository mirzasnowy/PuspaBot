# Content Policy

> **Status: minimal draft, version 0.1, 21 November 2026.**
> This document has NOT been reviewed by a lawyer. The legal review
> (ticket legal consultation) is still open. See `docs/legal.md`. This policy
> states rules for the closed beta; it does not claim any automated
> content-moderation system exists beyond the technical rails named
> in section 4.

## 1. What this policy covers

What you may ask PuspaBot to do, what the agent will refuse, and what
happens when content is reported. It covers your instructions to
the agent, the agent's outputs to you, and content PuspaBot publishes
or shares on your behalf (messages, drafts, marketplace listings,
community gallery entries).

## 2. Prohibited uses

You must not use PuspaBot to:

- **Defraud or deceive.** Fake payment proofs, wrong-transfer
  ("salah transfer, kirim balik") schemes, impersonation of banks,
  regulators (for example OJK), couriers, or family members, fake
  investment or like-task schemes, or any instruction that moves
  money to a stranger without the owner's real approval. The
  product's own attack corpus (`puspa/eval/data/attack_corpus_v1.json`)
  lists these patterns as the fraud cases the agent is tested
  against.
- **Break the law.** Anything illegal under Indonesian law or the law
  where you are, including illicit trade, unauthorised access to
  systems, and evasion of platform rules.
- **Harm or harass.** Harassment, threats, hate, or content that
  targets a person or group.
- **Impersonate.** Pretending to be another person, a company, a
  bank, or a public official, in messages the agent sends for you.
- **Manipulate the agent against its user.** Hiding instructions in
  web pages, files, listings, messages, or images to make the agent
  skip approvals, reveal secrets, pay someone, or remember false
  facts — including instructions that claim the user "already
  approved" something. This covers instructions aimed at any user's
  agent, not only your own.
- **Abuse the service.** Burning cost on purpose (for example
  looping the same search hundreds of times), installing unsigned
  skills or modules from untrusted sources, or trying to disable
  the guardrails.

## 3. What the agent will not do

- **Never move money silently.** The agent prepares payments only;
  execution is impossible in code (`EXECUTION_LIMIT_RUPIAH = 0`),
  and every preparation needs your approval on a card you tap
  yourself (`docs/agent-payments.md`).
- **Never reveal secrets.** Vault contents, API keys, tokens, and
  OTPs are redacted from outputs by the secret-output rail and are
  never sent anywhere on an instruction found in third-party content.
- **Never act on hidden instructions.** Role-takeover and other
  injection patterns are blocked for every source, including the
  user (`puspa/guardrails/rails.py`). When a turn is refused, the
  agent answers: "Sorry, this request cannot be processed because
  it breaks a rule."
- **Never manipulate you.** The agent optimises for your reflective
  goals, not engagement or dependence (`docs/anti-manipulation.md`):
  when an action benefits another party (a seller, the provider)
  more than you, the conflict is shown to you instead of decided
  silently.
- **Never present a guess as a fact.** Price and bill numbers must
  come with evidence; the evidence-number rail blocks outputs whose
  numbers have no source.

## 4. How the rules are enforced (what exists, what does not)

What exists in the product today:

- **Guardrails** (`puspa/guardrails/rails.py`): injection rails,
  action-content rails, evidence-number rails, secret-output rails,
  and a spending rail. Rails are fail-closed: a broken rail blocks
  instead of passing.
- **Approval gates** (approvals taxonomy): consequential actions need your approval
  first, recorded in the ledger (`docs/approvals.md`).
- **Conflict gate** (`puspa/policy/conflicts.py`): actions that
  benefit another party more than you are surfaced, not hidden.
- **Attack testing** (attack corpus, benchmark, mutation and scorecard): a frozen corpus of attack payloads and
  a scorecard run against the rails (`docs/eval-e16.md`). A zero
  attack-success rate on that corpus means no payload in that corpus
  got through — not that the system is safe in general.
- **Fraud vocabulary** (Indonesian context pass): a starting table of fraud patterns,
  not a verdict. It flags; a human decides.

What does NOT exist yet:

- **No general content classifier.** There is no automated system
  that judges whether arbitrary user content is harassing, hateful,
  or otherwise prohibited. The rails above catch specific technical
  patterns (injection, secrets, spending, numbers without evidence),
  not general content categories.
- **No in-product report button.** Reporting content today means
  contacting the operator directly. There is no published support
  contact yet (open decision, see `docs/legal.md`).

## 5. Cultural content

The culture module is parked until other features mature, but its
rules are already written in the plan (§21) and apply whenever
cultural content is handled:

- Every cultural fact carries provenance (source, scope of consent,
  expiry). Contested claims are marked contested, never asserted.
- Motifs whose use is restricted by custom (for example forbidden
  keraton motifs) are marked as restricted and are never sold or
  shown as generic decoration.
- In the community gallery (community gallery (publish and rate cultural works), post-beta), each user may rate a
  work once.

## 6. Reporting content

Until an in-product report flow exists: contact the operator
directly about content that breaks this policy — content the agent
produced, content another user published through PuspaBot, or a
marketplace listing or gallery entry. Include what happened, where
you saw it, and when. Reports are handled by a human (the
operator); there is no automated takedown. The incident runbook
from plan §14.10 does not exist yet and is a follow-up for legal consultation.

## 7. Consequences

Breaking this policy can lead to a warning, suspension, or closure
of the account (see `docs/terms-of-service.md`, section 6). The
ledger keeps a record of the actions taken, including refusals and
violations, so enforcement is itself auditable.
