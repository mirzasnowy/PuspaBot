---
name: "Price Check"
description: "Answers one forwarded product link, seller chat, or account number with a fair price, seller track record, account status, and act or do-not-act advice — from recorded data only, never a live fetch."
author: "Puspa Team"
---

# Price Check

This skill answers one question: "should I buy this?" The user
forwards a product link, a seller chat, or an account number, and
Puspa returns one structured, evidence-backed answer: a fair
price, a seller track record, an account status, and act /
do-not-act advice.

## When to Use This Skill

Use this skill when the user:

- Forwards a product link and asks if the price is fair
- Forwards a seller chat and asks if the seller is trustworthy
- Forwards an account number and asks if it is safe to pay
- Asks "is this a good deal" about a specific listing

## How a Check Works

One call answers one forwarded input
(`puspa.check_before_buy.check_before_buy`):

```python
from puspa.check_before_buy import check_before_buy

answer = check_before_buy(
    text,                      # the forwarded link, chat, or account number
    store=store,               # recorded observations (required)
    product_key=product_key,   # which product the observations are about
    mode="recorded",           # the default; see the live hold below
)
```

`classify_input` first sorts the input (product link, seller
chat, or account number), then the answer is built from the
recorded observations in the store: past prices for the product,
the seller's recorded track record, and the account's recorded
status. The answer is a `CheckAnswer` with the fair price, the
evidence behind it, and the advice. Every check writes evidence
rows and one ledger event.

## The Live-Execution Hold — Read This First

**Live price reads from Tokopedia, Shopee, and Blibli are HELD.**
Each marketplace's terms forbid automated reading
(`puspa.pricing.tos`), so this skill NEVER fetches a live price:

- Every number reported comes from recorded observations
  supplied by the caller (test fixtures, recorded cassettes, or
  a store seeded from an approved source).
- A missing observation produces an explicit `cannot_verify`
  entry — never a guessed number.
- Asking for `mode="live"` returns a held answer instead of
  reading anything. Do not work around the hold, and do not
  present a recorded price as if it were fetched live just now.

## Limits

- **Recorded data only.** If the store has no observation for
  the product, seller, or account, say `cannot_verify` and say
  what is missing. Never fill the gap with a price remembered
  from training or a guess.
- **No send path, no pay path.** This skill only reads recorded
  data, writes evidence, and returns text. It cannot message the
  seller or pay anyone.
- **One input, one answer.** A new link or a new seller is a new
  check; do not reuse a previous answer for a different listing.

## Example

User forwards a Tokopedia link: "Harga ini wajar ga?"

1. `classify_input` confirms it is a product link.
2. `check_before_buy` with the store's recorded observations for
   that product.
3. Answer with the fair price range from the observations, the
   evidence (which observations, when recorded), and the advice.
4. If no observation exists for that product, answer
   `cannot_verify` and explain what would be needed — do not
   invent a price.
