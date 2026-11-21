# Legal Documents: Status and Open Decisions

> **Status: minimal draft set, version 0.1, 21 November 2026.**
> Nothing in this folder is legal advice, and none of it has been
> reviewed by a lawyer. The legal review (ticket legal consultation) is still
> open and needs a human. Until it happens, these documents are a
> good-faith description of what the product actually does — written
> from the code, not from a template — for a small closed beta.

## 1. The documents

| Document | What it is | Version |
| --- | --- | --- |
| `docs/terms-of-service.md` | Terms of use: what PuspaBot is, what the agent never does, plans and prices, your responsibilities, suspension. | 0.1, 21 Nov 2026 |
| `docs/privacy-policy.md` | What data is collected, how it is used and protected, who can see it, retention, your rights. | 0.1, 21 Nov 2026 |
| `docs/data-consent.md` | The consent text the product shows and records, quoted verbatim from `puspa/consent/consent.py` (`v1-2026-11-17`), plus how consent is recorded and withdrawn. | 0.1, 21 Nov 2026 |
| `docs/content-policy.md` | Prohibited uses, what the agent refuses, how the rules are enforced, and how to report content. | 0.1, 21 Nov 2026 |
| `docs/legal.md` | This file: status of the set and the decisions still open. | 0.1, 21 Nov 2026 |

Scope note (plan §19, ticket minimal legal documents (terms of service, privacy, data consent, content policy)): this ticket covers the four
documents above plus this status file. The incident runbook named
in plan §14.10 is NOT part of minimal legal documents (terms of service, privacy, data consent, content policy) and does not exist yet.

## 2. How these documents were written

Every factual claim is grounded in the code or in a design doc in
this repo, and each document names its sources. Where the code and
a natural-language claim disagree, the code wins and the document
must be fixed. Known honest limits are written into the documents
themselves instead of smoothed over — most importantly:

- Account deletion deletes all user data OUTSIDE the ledger; ledger
  event content of a deleted user remains, because the tombstone
  event type is proposed but not built (`puspa/consent/account.py`).
- There is no per-feature consent withdrawal and no in-product
  content-report button yet.
- There is no general content classifier; the guardrails catch
  specific technical patterns only.
- Payment collection is not built: the billing gateway is a stub
  and no real money can be charged through PuspaBot today.

## 3. What is NOT claimed

- **No compliance claim.** Nothing here states that PuspaBot complies
  with any specific data-protection law. The documents describe
  practices; whether those practices satisfy the law is a question
  for the legal review (legal consultation).
- **No licence.** The project has no `LICENSE` file. Choosing a
  licence is the owner's decision (license choice, still open). These
  documents name no licence and do not state that the project's
  licensing is complete.
- **No settled billing model.** The billing model (credits vs
  subscription vs selling compute) is a config value and is NOT
  decided (billing model, still open). Prices in the terms are quoted from
  the code as they stand today, not as a promise that the model
  will stay that way.
- **No warranty or liability cap.** Both are left open deliberately
  rather than written as if already decided.

## 4. The legal review still needed (legal consultation)

Plan §14.10 lists the questions for one legal consultation session:

1. Whether the two wedges (price comparison, bill autopilot) need
   any permit.
2. The structure of any partnership with a payment provider or bill
   aggregator.
3. Data-protection obligations for the beta.
4. Wording of the terms of service and limits of liability.
5. Fraud-label risk (being mistaken for, or used for, fraud).
6. Trademark and licensing.
7. Tax on beta income.

Until that session happens, these documents must not be shown to
users outside the close circle as if they were finished.

## 5. Open decisions recorded in these documents

| Decision | Status | Where it is noted |
| --- | --- | --- |
| Licence of the project (license choice) | Open, owner's decision; repo has no `LICENSE` file | Terms §9, this file §3 |
| Billing model: credits vs subscription vs selling compute (billing model) | Open, owner's decision; code default is `"subscription"` | Terms §4, this file §3 |
| Trademark registration (trademark registration) | Open, needs a human | Terms §9 |
| Trial length | Code says 14 days; owner leans 3 or 7 days, not locked | Terms §4 |
| Published support / privacy contact | Does not exist yet | Terms §1, Privacy §9, Content §4/§6 |
| Age rule for users | No rule set yet | Terms §2, Privacy §9 |
| Content reporting channel | No in-product flow; contact the operator directly | Content §4/§6 |
| Incident runbook (plan §14.10) | Does not exist yet; follow-up for legal consultation | Content §6 |
| Ledger tombstone for deleted users (revision 8 / Phase 2) | Proposed, not built | Privacy §7, Consent §2 |
| Warranty and liability cap | Left open for legal consultation | Terms §7 |

## 6. Maintenance rule

When a fact in these documents changes in the code (a price, a
retention period, a new data source, a new enforcement mechanism),
the document changes in the same commit. `tests/test_legal_docs.py`
checks that the four documents plus this file exist, contain their
key sections, that the consent text version matches the code, and
that no licence name and no compliance claim has crept in.
