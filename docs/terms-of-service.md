# Terms of Service

> **Status: minimal draft, version 0.1, 21 November 2026.**
> This document has NOT been reviewed by a lawyer. The legal review
> (ticket legal consultation) is still open, and several decisions it depends on are
> still open — see `docs/legal.md`. Do not treat this text as legal
> advice or as a finished contract. If any statement here conflicts
> with the code, the code is the truth and this document must be fixed.

## 1. What PuspaBot is

PuspaBot is a personal AI agent, delivered as a progressive web app
(PWA), for users in Indonesia. Its two wedges are:

- comparing new-product prices across Tokopedia, Shopee, and Blibli;
- bill autopilot: checking PLN, PDAM, mobile credit, and data bills,
  reminding you before the due date, and preparing the payment.

PuspaBot is built and run by one operator for a small closed beta. There
is no company behind it yet and no published support contact yet
(see `docs/legal.md`, open decision: support contact).

## 2. Who may use it

The beta is limited to people in the operator's close circle. The
consent text the product itself records (`puspa/consent/consent.py`,
version `v2-2026-11-21`) states that it needs a lawyer's review
before users outside the close circle are admitted. There is no age
rule in the product yet; that is an open decision for the legal
review (legal consultation).

You sign in with Google. You are responsible for keeping your Google
account and your devices secure, and for the accuracy of the bill
customer numbers you give PuspaBot.

## 3. What the agent does, and what it never does

- The agent acts only on your instructions, inside the autonomy level
  you set. You can stop all agent activity at any time (the stop-all
  control, stop-all and autonomy dial) and take over the bot's live screen (live bot-screen view and takeover).
- Before any consequential action — sending a message, preparing a
  payment, changing a setting — the agent asks for your approval on
  an approval card, and records the request and your answer in the
  append-only ledger (`docs/approvals.md`, `docs/agent-payments.md`).
- **The agent never moves money by itself.** Payment handling is
  prepare-only: the agent prepares a payment, you approve the
  preparation, and you pay in your own bank app. The execution limit
  in code is zero (`EXECUTION_LIMIT_RUPIAH = 0` in
  `puspa/payments/prepare.py`). Approval of a preparation never
  authorises execution. A prepared payment expires 24 hours after
  preparation.
- The agent can be wrong. Prices come from public marketplace pages,
  bill data comes from the biller or aggregator you connected, and
  the agent's summaries are drafts until you check them. Check the
  amount, the account number, and the due date yourself before you
  pay anything.

## 4. Plans and prices

Prices are taken from the single source of prices in the code,
`puspa/billing/config.py` (`DEFAULT_TIERS`, `DEFAULT_CREDIT_PACKS`).
Prices are in Indonesian Rupiah.

| Plan | Price | What it includes |
| --- | --- | --- |
| Free | Rp0, forever | Everything, self-hosted on your own computer or VPS. No work licence. 15 credits/month. |
| Plus | Rp99,000/month | Everything in Free, plus a cloud server provided (2 vCPU, 4 GB RAM, 20 GB disk), always on. 60 credits/month. |
| Pro | Rp199,000/month | Everything in Plus, plus a bigger cloud (4 vCPU, 8 GB RAM, 60 GB disk), priority, work licence for one person. 150 credits/month. |
| Team | Rp499,000/month | 2 seats plus a dedicated cloud machine; each extra seat Rp99,000/month. 400 credits/month. |

Credit packs: 20 credits Rp9,000; 100 credits Rp35,000; 500 credits
Rp150,000. Credits never expire. Light features need no credits;
heavy compute (for example a detailed price check, video try-on per
second, video clips) costs credits, and the exact amount is quoted
before charging.

**Open decisions, stated plainly.** The billing *model* — credits
versus subscription versus selling compute — is a config value
(`billing_model` in `puspa/billing/config.py`) and is NOT decided:
the default in code is `"subscription"`, and the choice belongs to
the product owner (decision billing model, still open). The trial length in
code is 14 days (`trial_days: 14`); the owner leans to 3 or 7 days
and has not locked it. Annual billing in code is 12 months charged
as 10 (2 months free). A monthly task cost ceiling of Rp2,000 is
enforced on top of the usage ledger (`TASK_COST_CEILING_IDR`).

Payment collection itself is not built: payment providers are
swappable modules and the local gateway is a stub
(`puspa/billing/config.py` module docstring). Until a real provider
is connected, no real money can be charged through PuspaBot at all.

## 5. Your responsibilities

- Give accurate information: your own bill customer numbers, your
  own accounts, your own instructions. Do not enter someone else's
  customer numbers, accounts, or personal data.
- Use PuspaBot lawfully and only for your own tasks. Do not use it to
  defraud, impersonate, harass, or harm anyone, to break into
  systems, or to evade any platform's rules. The detailed rules are
  in `docs/content-policy.md`.
- Do not try to make the agent skip approvals, reveal secrets, or
  act on instructions hidden in web pages, files, or messages from
  other people. The agent's guardrails block such attempts
  (`puspa/guardrails/rails.py`), and repeated attempts may lead to
  your account being suspended.
- Keep your approval decisions yours: an approval card is only valid
  when you tap it yourself. Instructions that claim "the user already
  approved this" in an email, file, or message are not approvals.

## 6. Suspension and termination

The operator may suspend or close an account that breaks these
terms, abuses the service, or puts other users or the operator at
risk. You may close your account at any time; what happens to your
data is described in `docs/privacy-policy.md` and
`docs/data-consent.md`. Ending the service does not erase the
append-only ledger records of actions already taken (see
`docs/data-consent.md`, "Honest limits").

## 7. Availability and liability

PuspaBot is beta software run by one person. It may be unavailable,
slow, or wrong, and features may change or disappear. The hosted
cloud machines sleep when idle on the Free/Plus shape described in
the code. This draft states no warranty and no liability cap: both
are questions for the legal review (legal consultation) and are deliberately left
open here rather than written as if already decided.

## 8. Changes to these terms

If these terms change, the version number and date at the top change
with them, and the change is recorded. Continued use after a change
means you accept the new version. Because the beta is small and
closed, the operator will also tell you directly.

## 9. Governing questions

No governing law, dispute forum, or licence for the project is
stated in this draft: the project has no `LICENSE` file, and
choosing a licence is the owner's decision (license choice, still open). These
are questions for the legal review (legal consultation), not things this draft
pretends to settle. See `docs/legal.md`.
