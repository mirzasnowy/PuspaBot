# Indonesian context pass (B14)

Scope: plan section 15, items 15.1-15.12, applied only to the
tickets built in this queue — B1, B2, B3, B4, B5, B7, B8. B6
and B9-B13/B18 are out of this queue and were not re-passed.
Every "met" claim below points at code or a test that was read
during this pass; "not applicable" means the item belongs to
another ticket, a product decision, or a human panel, not that
it was skipped silently.

Four real gaps were found and fixed in this pass:

1. No code parsed the 15.1 money forms (`50rb`, `1,5 jt`,
   `seratus ribu`, `dua jeti`) — new `puspa/local_data/money.py`.
2. The B1 reply rendered Rupiah with comma separators
   (`Rp12,999,000`) — now dot separators (`Rp12.999.000`).
3. The 15.5 local fraud patterns existed nowhere in code — new
   `puspa/graphs/fraud_patterns.py`, wired into the B1 answer.
4. The B8 approval surface showed no consequences (15.2) —
   consequences now ride in the approval record and the render.

Out of scope by the brief, recorded as such below: 15.8
device/offline mode and HP-OTP login, the 15.12 20-person test
panel, Hijriah dates (15.7 option), parent-companion mode
(15.11), and anything touching culture (B15/B16/B17).

## B1 — "Cek Sebelum Beli" beta slice

- **15.1 language.** Met. Everyday Indonesian and mixed
  Indonesian-English input is normalised by
  `puspa/local_data/language.py:151` (`normalize_id_text`,
  phrase map `cek harga` / `ongkir` / `bisa nego` plus the
  word map, address words dropped) and the classifier
  recognises forwarded seller chat by its markers
  (`puspa/check_before_buy/classifier.py:108`). Tests:
  `test_everyday_indonesian_input_is_understood` in
  `tests/test_local_data.py`.
- **15.1 money parsing.** Gap found and fixed. No code in the
  slice understood `50rb`, `1,5 jt`, `seratus ribu`, or
  `dua jeti`: the only Rupiah parsers (`puspa/pricing/readers.py`
  `_parse_rupiah`, `puspa/billing/billing.py` `_parse_amount`)
  handled `Rp1.250.000` only. New
  `puspa/local_data/money.py:263` (`parse_rupiah_amount`)
  parses all four forms plus `Rp1.250.000`, `Rp 1.250.000,50`,
  `500 ribu`, `satu juta lima ratus ribu`, and returns `None`
  for bare digit runs (an account number is never read as
  money). Tests: `test_parse_rupiah_amount_*` in
  `tests/test_local_data.py`.
- **15.1 timezones.** Not applicable to this ticket. No profile
  timezone field exists yet (checked `puspa/memory/profile.py`,
  `puspa/accounts/users.py` — no timezone storage); WIB/WITA/WIT
  per profile belongs to the scheduler/profile work, not the
  price-check slice. Recorded as future.
- **15.1 register per contact.** Not applicable to this ticket.
  No salutation/register storage exists in memory yet; storing
  Pak/Bu/Kak/Mas/Mbak per contact with confidence is memory
  work (G7/G24 area), not a B1 change. Recorded as future.
- **15.2 sungkan / approval consequences.** Met in part. The
  answer never claims a go-ahead it cannot prove: anything
  unverified is `cannot_advise` with the missing pieces listed
  (`puspa/check_before_buy/answer.py`, `_decide_advice`), and
  the reply states gaps rather than hiding them
  (`render_text`). The approval-card half of 15.2 belongs to
  B8; see below.
- **15.5 local fraud patterns.** Gap found and fixed. The
  patterns existed nowhere in code. New
  `puspa/graphs/fraud_patterns.py:251`
  (`match_fraud_patterns`) covers all eleven 15.5 patterns
  (courier APK, bank/OJK impersonation, `mama minta pulsa`,
  new number, like/subscribe part-time work, illegal
  investment/lending, wrong-transfer refund, mule account,
  fake COD, account trading, online gambling). B1 consumes it:
  a forwarded text matching a pattern gives `do_not_act` with
  the pattern named and its plain-language warning in the
  reply (`puspa/check_before_buy/answer.py`, fraud block in
  `check_before_buy`; `fraud_patterns` field on `CheckAnswer`
  and in `to_dict`). Tests:
  `test_forwarded_chat_matching_scam_pattern_is_do_not_act`,
  `test_wrong_transfer_pattern_is_do_not_act_and_named`,
  `test_fraud_patterns_ride_in_answer_dict` in
  `tests/test_check_before_buy.py`; table coverage in
  `tests/test_shared_graphs_b3.py`.
- **15.6 money and payments.** Met. The reply names the
  payment rail the input mentions (QRIS, virtual account, bank
  transfer, GoPay, OVO, DANA, COD) via
  `puspa/local_data/rails.py:116` (`detect_rail`), naming only,
  never preparing a payment. Shipping quotes carry real ongkir
  with COD availability (`answer.py`, `_shipping_section`).
  Gap found and fixed: the reply rendered Rupiah with US comma
  separators (`Rp12,999,000`); it now uses
  `format_rupiah` (`puspa/local_data/money.py:283`, dot
  thousands) for fair price and shipping. Test:
  `test_reply_renders_rupiah_with_dot_thousands`; one
  pre-existing assertion (`test_price_check_quotes_shipping_and_cod`)
  was updated from the old comma form to `Rp12.000`.
- **15.9 privacy.** Met. Account numbers are masked to first
  two / last two digits (`answer.py`, `mask_account_number`);
  an unknown account is `cannot_verify`, never "looks safe".
- **15.10 sensitive topics.** Not applicable to this ticket.
  Refusing gambling and illegal lending with an explanation is
  guardrail work, not a price-check change; the fraud-pattern
  warning for online gambling (15.5) is the part that lives
  here, and it is met above.
- **15.3, 15.4, 15.7, 15.8, 15.11, 15.12.** Not applicable:
  group decisions belong to B6/B11, trust-through-proof to B4,
  calendar rhythms to the scheduler, offline/HP-OTP and the
  test panel and parent-companion mode are out of scope for
  this pass (see the header).

**Sign-off B1: pass.** Two gaps found and fixed (money
parsing, fraud patterns, Rupiah display — three fixes, two
checklist items plus display).

## B2 — Verified outcome records

B2 is a write path, not a user-facing surface, so most of
section 15 does not touch it directly. What applies:

- **15.3 gotong royong / collective decisions.** Met.
  Outcome records are queryable per user and per group (room):
  `puspa/evidence/outcomes.py`, `list_outcomes_for_user` and
  `list_outcomes_for_group`. A shared record (arisan, iuran,
  jastip, committee cash) is visible to the room it belongs
  to, and a record without evidence is rejected by the writer
  (`_require_evidence`), so a group decision always rests on
  proof. Tests: `tests/test_outcome_records_b2.py`.
- **15.4 trust.** Met in the B2 sense: the record carries its
  evidence and receipts, and the writer refuses evidence-free
  records — a claim without proof cannot enter the shared
  record at all.
- **15.1, 15.2, 15.5, 15.6, 15.7, 15.8, 15.9, 15.10, 15.11,
  15.12.** Not applicable: the writer stores whatever the
  caller passes; language, money forms, fraud patterns, and
  approval wording live in the calling tickets (B1, B5, B8),
  and no outcome type in this ticket names a calendar,
  device, or family mode.

**Sign-off B2: pass.** No gap found; no code changed.

## B3 — Shared anti-fraud and price graphs

- **15.5 local fraud patterns.** Gap found and fixed. The
  shared graph had no fraud-pattern vocabulary at all:
  `flag_seller` (`puspa/graphs/shared.py`) stores a free-text
  `flag_reason` and nothing classifies it. New
  `puspa/graphs/fraud_patterns.py` is the 15.5 table (eleven
  patterns, Indonesian trigger phrases, plain-language
  warnings) with `match_fraud_patterns` and
  `classify_flag_reason` for tagging a free-text reason
  against the known patterns. B1 is the first consumer (see
  above); the shared flag tables themselves were left
  untouched, because changing stored flag shapes is a schema
  decision, not a checklist fix. Tests:
  `test_fraud_pattern_table_covers_plan_15_5`,
  `test_match_fraud_patterns_*`,
  `test_classify_flag_reason_tags_free_text` in
  `tests/test_shared_graphs_b3.py`.
- **15.4 trust and referrals.** Met. One flag protects every
  other user (`flag_seller` / `seller_reputation` in
  `puspa/graphs/shared.py`), and shared reads return
  aggregates only — no reporter identity is ever exposed, so
  the "12 people in this community use it" style of social
  proof cannot leak who flagged whom. Test:
  `test_flag_once_protects_every_other_user`,
  `test_shared_reputation_exposes_no_identity`.
- **15.6 money.** Met. The fair-price estimate is a median in
  integer Rupiah (`estimate_fair_price`), robust to one
  outlier listing, with a standard error that shrinks as
  observations grow. Test:
  `test_shared_fair_price_improves_with_more_checks`.
- **15.1, 15.2, 15.3, 15.7, 15.8, 15.9, 15.10, 15.11, 15.12.**
  Not applicable: the graph stores keys, prices, and flags;
  language, register, approval wording, calendar, and device
  concerns live in the tickets that read it. Per-user privacy
  (15.9 in spirit) is already the module's structural rule —
  private records are namespaced by `user_id` and shared
  tables carry no user id at all.

**Sign-off B3: pass.** One gap found and fixed (fraud-pattern
vocabulary).

## B4 — Trust / proof surface

- **15.4 trust through proof.** Met — this ticket is the
  mechanism. Every money or send action gets a human-readable
  receipt with its hash-chain position and verification
  (`puspa/evidence/proof.py`, `Receipt`, `build_receipt`,
  `verify_receipt`); answers carry their evidence or are
  flagged `proven: False` (`attach_proof`); a user can export
  their own audit trail (`build_audit_trail_export`), served
  by `puspa/pwa/proof_api.py` (receipt, outcome, audit-trail,
  and export endpoints). Tests: `tests/test_proof_surface.py`,
  `tests/test_pwa_proof_api.py`.
- **15.9 privacy and feeling safe.** Met. The audit-trail
  export states what it leaves out (`AUDIT_TRAIL_EXCLUDED`:
  other users' events, vault ciphertext, password/session
  hashes, redacted secret-looking text), receipts never claim
  an action was harmless — an unrecognised action is
  `"other"`, not "safe" (`classify_receipt_action`) — and a
  receipt proves the event is on the ledger unchanged, never
  that the claim behind it is true (stated in the export
  notes). The 14.5 fraud-label rule (neutral, factual labels,
  never "scammer" as fact) is honoured by construction: B4
  shows flag counts, never a verdict word.
- **15.2 approval consequences.** Met in the B4 sense: the
  receipt shows what happened and its evidence; the
  consequences of approving live on the approval surface
  itself (B8, see below).
- **15.1, 15.3, 15.5, 15.6, 15.7, 15.8, 15.10, 15.11, 15.12.**
  Not applicable: B4 renders the ledger, it does not parse
  language, money, or fraud patterns, and it owns no calendar,
  device, or family surface. (Receipt copy is English; an
  Indonesian copy of the proof surface is i18n work for a
  later ticket, not a B4 fix.)

**Sign-off B4: pass.** No gap found; no code changed.

## B5 — Local Indonesian data layer

- **15.1 language and register.** Met. `normalize_id_text`
  (`puspa/local_data/language.py:151`) handles everyday
  Indonesian, mixed Indonesian-English, and commerce shorthand
  (`ongkir`, `COD`, `PO`, `DP`, `cek harga`, `bisa nego ga`),
  with unknown words passing through unchanged. City and
  courier spellings come from one alias table
  (`puspa/local_data/rates.py`, `CITY_ALIASES` /
  `COURIER_ALIASES`, including `jkt`, `bdg`, `jogja`,
  `tangsel`, `jnt`, `sicepat`). Register storage (Pak/Bu/Kak
  per contact) is not in this layer — recorded as future under
  B1 above. Tests: `tests/test_local_data.py`.
- **15.1 money parsing.** Gap found and fixed — new
  `puspa/local_data/money.py` (see B1). `parse_rupiah_amount`
  and `format_rupiah` are exported from `puspa.local_data`.
  Tests: `test_parse_rupiah_amount_*`,
  `test_format_rupiah_uses_dot_thousands`.
- **15.1 timezones.** Not applicable to this ticket. No
  profile timezone storage exists; see B1.
- **15.6 money and payments.** Met. Rail naming covers QRIS,
  virtual account, bank transfer, GoPay, OVO, DANA, and COD
  (`puspa/local_data/rails.py:116`), with banks carried on VA
  and transfer mentions, and DANA guarded against the ordinary
  word for "funds" (needs payment context). Shipping rates are
  Rupiah per kg with COD flags and provenance on every row
  (`puspa/local_data/rates.py`, `SEED_SOURCE_LABEL`, seed date
  2026-11-19, origin Jakarta) and fail loud on unknown city or
  courier instead of guessing. Admin fees (15.6: "don't hide
  admin fees") are not modelled anywhere in B5/B8 — there is
  no fee data to show; recorded as future, needing a fee data
  source and a product decision, not a parser fix.
- **15.7 calendar rhythms.** Not applicable to this ticket:
  B5 owns no calendar. (The seasonal half of 15.7 that touches
  B5's consumers — Ramadan as peak fake-seller season — is
  covered by the 15.5 fraud warnings in B1.)
- **15.2, 15.3, 15.4, 15.5, 15.8, 15.9, 15.10, 15.11, 15.12.**
  Not applicable: B5 is data (rates, rails, language); it
  neither asks questions, nor approves, nor stores group or
  family state. The 15.5 patterns live next to the graph
  (B3), not in this layer.

**Sign-off B5: pass.** One gap found and fixed (money
parsing/formatting).

## B7 — Prompt to visual mockup, drag and drop

- **15.1 everyday Indonesian prompts.** Met. Template
  detection keys on Indonesian words (`undangan`, `hajatan`,
  `warung`, `katalog`, `lebaran`, `ramadan` —
  `puspa/mockup/generate.py:82`), and word-change patches are
  parsed from Indonesian instructions (`besarkan harga`,
  `ganti warna jadi hijau`, `ganti teks ... jadi ...` —
  `parse_word_patch`), with Indonesian colour words. Gap found
  and fixed: price extraction only understood `Rp...` digits,
  so a prompt saying `harga 50rb` or `1,5 jt` left the price
  slot empty; `extract_fields` now uses
  `parse_rupiah_amount` and renders the price with
  `format_rupiah` (`generate.py:101`). Tests:
  `test_extract_fields_understands_shorthand_money` in
  `tests/test_mockup_b7.py`; existing
  `test_detect_template_and_extract_fields` still passes.
- **15.6 UMKM first.** Met. Templates are UMKM-shaped
  (warung menu, chat catalogue, promo banner, hajatan
  invitation) with Rupiah defaults (`Rp25.000`, `Rp15.000`
  in `puspa/mockup/templates.py`).
- **15.7 calendar rhythms.** Met in part. The `lebaran_card`
  template exists (`templates.py:335`, keywords `lebaran` /
  `idul fitri` / `ramadan`), so the seasonal greeting-card
  ask works from a plain prompt. Harbolnas/THR/mudik
  templates do not exist; they are new templates, not a
  checklist fix — recorded as future.
- **15.2, 15.3, 15.4, 15.5, 15.8, 15.9, 15.10, 15.11, 15.12.**
  Not applicable: the mockup editor edits a design document;
  it asks nothing, approves nothing, and stores no money,
  group, or family state. (A mockup is also not a place for
  fraud warnings — the 15.5 patterns belong to forwarded
  chats and payments, not to a poster prompt.)

**Sign-off B7: pass.** One gap found and fixed (shorthand
money in prompt price extraction).

## B8 — Agent payments, phase 1: prepare only

- **15.2 approval shows consequences.** Gap found and fixed.
  The approval record carried recipient, amount, rail, and a
  note, but no consequences; the render stated status and
  "Puspa never pays" but not what approving means. Now the
  `approval_requested` ledger content carries a
  `consequences` list (approving makes the payment payable;
  it never moves money by itself; the user pays in their own
  bank/e-wallet app to the named recipient only; it can still
  be rejected until expiry —
  `puspa/payments/prepare.py`, `approval_content`), and
  `render_prepared_payment`
  (`puspa/payments/models.py:334`) states the consequence in
  plain language for both the awaiting-approval and the
  approved states. Tests:
  `test_render_states_consequences_of_approving`,
  `test_render_after_approval_states_consequence`,
  `test_approval_record_carries_consequences` in
  `tests/test_payments_b8.py`.
- **15.6 money and payments.** Met. Rails are the Indonesian
  ones (QRIS, virtual account, bank transfer, GoPay, OVO,
  DANA) via `resolve_rail`; COD is refused with a reason (a
  prepared payment needs an account to pay into). Amounts are
  integer Rupiah, rendered `Rp150.000` (dot thousands —
  `format_rupiah_amount`, `models.py:169`). Nothing executes:
  the Rp0 limit is architectural, and every preparation is
  approval-gated and ledger-recorded. Admin fees are not
  modelled — see B5, recorded as future.
- **15.4 gradual trust.** Met by construction: phase 1
  prepares only; the user pays in their own app, so the agent
  earns trust one prepared payment at a time and never moves
  money itself (`PAY_NOTICE`, `models.py`).
- **15.1, 15.3, 15.5, 15.7, 15.8, 15.9, 15.10, 15.11, 15.12.**
  Not applicable: B8 takes a verified recipient, an integer
  amount, and a rail — it does not parse free text (money
  forms are B5's job), own a calendar or device, or store
  family state. Room preparations (`room_id`) support the
  15.3 group-payment shape at the record level; the group
  approval flow itself belongs to B6/B9, not to this ticket.

**Sign-off B8: pass.** One gap found and fixed (approval
consequences).

## Recorded as future (not built here)

- Per-contact register (Pak/Bu/Kak/Mas/Mbak) with confidence,
  and profile timezone (WIB/WITA/WIT) — need memory/profile
  schema work; owner: memory tickets (G7/G24 area).
- Admin-fee display — needs a fee data source and a product
  decision; owner: B5/B8 follow-up.
- Harbolnas / THR / mudik mockup templates — new B7 templates.
- Hijriah date option (15.7), 15.8 device/offline mode and
  HP-OTP login, 15.12 20-person test panel, 15.11
  parent-companion mode — out of scope for this pass by the
  brief; parent-companion mode needs its own ticket.
- Indonesian copy for the B4 proof surface — i18n work, later
  ticket.
- Concrete-question wording on approval cards (15.2: "send now
  or tomorrow morning?" instead of closed yes/no questions) —
  the approval content now states consequences; rewording the
  card's question itself is a PWA copy decision, not a B8
  code fix.
- The 15.5 pattern table is a starting vocabulary, not a
  verdict: a text matching no pattern is not "safe", and the
  table should grow from the 8.5 attack corpus as new local
  patterns are confirmed.

## Verification

- Module + neighbour tests, DB `puspa_test_w1`,
  `NO_PROXY=localhost,127.0.0.1`: 165 passed (5 module files:
  `test_local_data.py`, `test_mockup_b7.py`,
  `test_check_before_buy.py`, `test_shared_graphs_b3.py`,
  `test_payments_b8.py`); with neighbours
  (`test_pwa_payments_api.py`, `test_pwa_proof_api.py`,
  `test_outcome_records_b2.py`, `test_mockup_api_b7.py`,
  `test_proof_surface.py`): 230 passed. No failures.
- Ruff check + format clean, mypy clean on all new/changed
  files. No TODO/FIXME/NotImplementedError in shipped code.
- The full suite was not run (the coordinator runs the central
  suite in `build/`); only module + neighbour tests ran here.

## Files changed

- New: `puspa/local_data/money.py` (15.1 money parsing +
  Rupiah formatting), `puspa/graphs/fraud_patterns.py` (15.5
  pattern table + matcher), `docs/indonesian-context-pass.md`
  (this document).
- Changed: `puspa/local_data/__init__.py` (export money
  helpers), `puspa/check_before_buy/answer.py` (fraud-pattern
  warning + `do_not_act`, Rupiah dot formatting),
  `puspa/mockup/generate.py` (shorthand money in price
  extraction), `puspa/payments/models.py` (approval
  consequences in render), `puspa/payments/prepare.py`
  (`consequences` in the approval record).
- Tests: `tests/test_local_data.py` (money tests; one
  pre-existing assertion updated from `Rp12,000` to
  `Rp12.000`), `tests/test_check_before_buy.py` (fraud +
  Rupiah tests), `tests/test_shared_graphs_b3.py` (pattern
  table tests), `tests/test_mockup_b7.py` (shorthand price
  tests), `tests/test_payments_b8.py` (consequences tests).
