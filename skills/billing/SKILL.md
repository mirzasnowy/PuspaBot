---
name: "Billing"
description: "Quotes Puspa's public prices: the Free, Plus, Pro, and Team tiers and the credit packs. Prices are public data; recording or settling money lives in the hosted service, not here."
author: "Puspa Team"
---

# Billing

This skill quotes Puspa's prices. The prices themselves are
public data, and this package is their single source — every
price in the product (quotes, invoices, entitlements) reads from
here, never from a second price table anywhere in the repo.

## When to Use This Skill

Use this skill when the user:

- Asks how much Puspa costs, what a tier includes, or how many
  credits they get
- Asks to compare tiers or credit packs
- Asks what happens when credits run out

## The Prices

```python
from puspa.billing import default_config

config = default_config()
tier = config.tier("plus")              # one tier by id
pack = config.credit_pack("pack-100")   # one credit pack by id
allowance = config.monthly_credit_allowance("pro")
```

### Tiers

| Tier | Price / month | Monthly credits | Cloud machine |
| ---- | ------------- | --------------- | ------------- |
| `free` | Rp0 | 15 | none — own computer (PC or VPS), all light features |
| `plus` | Rp99.000 | 60 | 2 vCPU, 4 GB RAM, 20 GB disk (idle-sleeping microVM) |
| `pro` | Rp199.000 | 150 | 4 vCPU, 8 GB RAM, 60 GB disk, priority, single-user work licence |
| `team` | Rp499.000 | 400 | 4 vCPU, 8 GB RAM, 60 GB disk, 2 seats; each extra seat Rp99.000/month |

### Credit Packs

| Pack | Credits | Price |
| ---- | ------- | ----- |
| `pack-20` | 20 | Rp9.000 |
| `pack-100` | 100 | Rp35.000 |
| `pack-500` | 500 | Rp150.000 |

`puspa.billing.config.billing_model_notes()` returns the model
notes (what the numbers above mean). Quote from
`default_config()` — never from memory, and never from a price
seen in a chat or a screenshot.

## Limits — Read These First

- **Quoting only.** This package holds prices. Everything that
  records or settles money (charges, invoices, refunds,
  entitlements) lives in the hosted service. Never claim a
  payment went through because a price was quoted.
- **No invented prices.** No discounts, no custom tiers, no
  "special price for you". If the user asks for a price that is
  not in the config, say it does not exist and offer the closest
  listed tier or pack.
- **The billing model is the owner's decision.** Whether Puspa
  sells credits, subscriptions, or something else is decided by
  the owner, not by the agent. Quote the current model; do not
  propose a different one as if it were live.
- **Credits are monthly allowance plus packs.** A tier's monthly
  credits are an allowance, not a balance that rolls over
  silently — if the user asks whether unused credits carry over,
  say the config does not state that, instead of guessing.

## Example

User: "Pro itu dapat apa aja, berapa?"

1. `config.tier("pro")` — Rp199.000/month, 150 monthly credits,
   4 vCPU, 8 GB RAM, 60 GB disk, priority, single-user work
   licence.
2. Present it plainly, and offer the comparison with Plus
   (Rp99.000, 60 credits, 2 vCPU / 4 GB / 20 GB) if the user is
   choosing.
