# Shared anti-fraud and price graphs, per-user privacy (B3)

Some graphs get stronger with more users: a bad seller flagged
once protects everyone, and more price checks make fair-price
estimates more accurate. Each user's own data stays theirs:
per-user visibility, never pooled.

The implementation is `puspa.graphs.shared`, on the existing
Postgres graph tables in `infra/schema_graphs.sql` (created by
`puspa.graphs.create_schema`). It reuses the B1 record types
for its inputs and the B2 outcome records for its audit trail;
it does not build a second graph store.

## The boundary

| Side | What lives there | Who can read it |
| --- | --- | --- |
| Shared | `shared_seller_flags`, `shared_price_observations` | Any user, aggregates only |
| Private | `shared_graph_private_records` | Only the owning user |

The boundary is structural. The shared tables have **no
`user_id` column at all**, so no shared query can return a
user's rows: there is nothing to join back to a user. A
contribution is stored with a per-(user, subject) pseudonym,
`sha256(user_id + ":" + subject_key)`, used only to count
distinct contributors and to stop one user flagging the same
seller (or contributing the same price from the same source)
twice. The hash is never returned by any read function, and
because the subject key is part of the hash, contributions
about different sellers cannot be linked to the same user.

Private records live in their own table, namespaced by
`user_id`. Every private read filters on the requesting
user: `list_private_records` returns only that user's rows,
and `get_private_record` returns `None` when the record
belongs to someone else (or does not exist — the caller
cannot tell which). The table also has a row-level-security
policy (`app.current_user_id = user_id`) for connections as
the `puspa_app` role; the application-level filter is the
enforcement the tests rely on, because test connections run
as a superuser that bypasses row-level security.

## Aggregation rules

### Seller reputation

- One flag per user per seller. Flagging twice is a no-op;
  the count never inflates.
- `flagged` is true from the first flag: one flag protects
  every other user.
- The shared read returns aggregates only: flag count, first
  flag time, latest flag time. No reporter identity, no flag
  reason text — the reason a flagger wrote is stored but
  never shown to another user, so free text cannot leak
  private detail.

```python
flag_seller(dsn, user_id=ana, seller_name="Murah Banget Shop",
            flag_reason="payment taken, no goods shipped")
seller_reputation(dsn, "Murah Banget Shop")
# SellerReputation(seller_key="murah banget shop",
#                   flag_count=1, flagged=True, ...)
```

### Fair-price estimate

- One contribution per user per product per source. A repeat
  contribution keeps the first row.
- The estimate is the **median** of all shared observations
  (robust to one outlier listing), with the mean, the sample
  standard deviation, and the **standard error**
  (`stdev / sqrt(n)`) alongside it. The standard error is the
  measurable form of "more checks make the estimate more
  accurate": with the same spread it falls like `1/sqrt(n)`.
- A single observation has no standard error and no
  confidence interval — there is no spread to measure.
- Prices must be finite and positive; anything else is
  rejected before it is stored, so a bad contribution cannot
  skew the shared estimate.

```python
contribute_price(dsn, user_id=ana, product_key=LINK,
                 source="Shopee", price=12_499_000)
shared_fair_price(dsn, LINK)
# FairPriceEstimate(value=12_499_000, observations=1,
#                   standard_error=None, ...)
```

`estimate_fair_price(product_key, prices, sources)` is the
pure form of the same math (no database), so the improvement
with more observations can be tested without any I/O.

### Private records

```python
record_private(dsn, user_id=ana, kind="seller_note",
               subject_key="gadget store",
               payload={"note": "slow to reply"})
list_private_records(dsn, ana)   # only Ana's rows, newest first
get_private_record(dsn, record_id, budi)  # None: not Budi's
```

Shared aggregates never read the private table.

## Wiring into B1

`check_before_buy(..., shared_dsn=...)` consults the shared
graphs; without `shared_dsn` the answer is exactly the B1
recorded-only answer.

- **Seller:** when the local store says nothing about the
  seller, or says the seller is clean, and the shared graph
  has a flag, the seller section becomes `flagged` with
  `reports` equal to the distinct-flagger count and the note
  "flagged in the shared graph by N user(s); the flagger is
  not shown". Advice becomes `do_not_act`. A local flag still
  wins when present; the shared flag reason is never shown.
- **Fair price:** when the local store has no price
  observations at all, the shared estimate is offered as an
  `uncertain` hint (median value, shared sources listed, basis
  stating it is a shared estimate). It never upgrades to
  `verified`: a shared hint is not the user's own verified
  evidence, and the outcome writer still writes no evidence
  rows for it.

`handle_check_before_buy(..., shared_dsn=...)` passes the
parameter through. A shared-database failure with
`shared_dsn` given is raised to the caller, not silently
treated as a clean seller.

## Privacy proof

`tests/test_shared_graphs_b3.py` proves all three acceptance
criteria:

1. **Flag once, protect all:** Ana flags a seller; Budi's
   `seller_reputation` sees it, and Budi's B1 answer flips
   to `flagged` / `do_not_act` with no `shared_dsn`-free
   change in behaviour.
2. **Estimates improve measurably:** with seeded noisy prices
   around a known true price, the median error falls from
   5 observations to 50, and the standard error falls with
   it — both in the pure estimator and through the database.
   A repeated-spread check shows the standard error falling
   faster than `1/1.5` when observations quadruple.
3. **No cross-user reads:** Ana's and Budi's private records
   are listed separately; fetching Ana's record as Budi
   returns `None`; and a marker string planted in Ana's
   private payload never appears in any shared read
   (`seller_reputation`, `shared_fair_price`,
   `list_shared_price_points`) or in Budi's private listing.
   Shared reads are also asserted to expose exactly their
   documented keys — no user id, no reporter hash.

## Honest limits

- **Pseudonym, not anonymity.** Anyone who already knows a
  user's UUID could test whether that user flagged one
  specific seller by recomputing the hash. They cannot
  enumerate flaggers, read private rows, or link
  contributions across subjects — but the membership test
  against a known UUID is real. True anonymity would need a
  server-side secret (a pepper) that Puspa does not have yet.
- **No Sybil defence.** One account is one flagger. A user
  with many accounts could inflate a flag count; there is no
  reputation weighting, no flag expiry, and no dispute or
  un-flag path yet.
- **No live data.** Contributions come from recorded checks
  (B1) and explicit calls; nothing here fetches a live price
  or a live seller report, and live price execution is still
  held under decision 15.
- **No encryption at rest for private records.** Privacy is
  enforced by namespacing plus row-level security, not by
  per-user encryption; the ticket's "per-user encryption"
  phrase is not implemented as cryptographic encryption.
- **Shared price points are visible per observation.**
  `list_shared_price_points` returns individual (product,
  source, price) triples with no identity attached; the
  aggregate estimate is the intended read, and the point list
  exists for auditability.
