# B12 — Group research and shopping

Source: plan section 8.7, ticket B12 in `docs/tickets-beta-plan.md`.
Module: `puspa/rooms/research.py` (service `ResearchService`),
links table `room_research_links` in migration `0010_rooms.sql`,
routing hook in `puspa/rooms/routing.py`
(`handle_group_inbound(..., research=...)`).
Tests: `tests/test_rooms_research_b12.py` (18 tests).

## What it does

A room collects product links; the agent compares price and
seller for all of them in one answer; the group decides in chat.

1. **Collect.** A member shares a link in the group chat
   (`extract_product_links` pulls allowlisted product URLs out
   of a message) or adds one directly
   (`ResearchService.add_link`). One link per option, stored
   once per room (same URL re-added is a no-op). Only room
   members may add, list, or remove links; the author or a room
   owner may remove one.
2. **Compare.** `ResearchService.compare` reads every collected
   link with an injected reader (same `ResultRead` shape as the
   B1 wedge), then re-reads each price with the independent
   verifier (`puspa/evidence/verifier.py`). Each evidence row
   is saved to the `evidence` table. `render_group_comparison`
   produces the one plain-text answer for the room: per item,
   price with its `evidence_id` and verdict, seller, store
   rating, shipping, and source link; the cheapest verified
   option; the spread; and the reason for every unchecked link.
3. **Decide.** `ResearchService.record_group_decision` records
   the decision as room memory of kind `decision`
   (`puspa/rooms/memory.py`, same as B10), naming who agreed
   and attaching the comparison task id.

## Rules enforced

- **Every number carries its evidence, never invented.** A
  price is shown only with its `evidence_id` and verdict. A
  link the reader cannot read is shown as unchecked with its
  reason — never as a guessed price.
- **Cheapest uses verified prices only.** Unlike B1, which
  ranks every price it read, a group decision must not rest on
  an unverified number: the cheapest item is the lowest price
  among items whose verdict is `Verified`. A price whose
  re-read disagrees is shown with its verdict but never wins.
- **Seller reported, never invented.** The reader may report a
  seller name (`ResultRead.seller`, extracted from generic
  JSON-LD `offers.seller` when the page reports one — see
  `puspa/pricing/readers.py`); when the reader reports none,
  the answer says the seller is unknown.
- **Agreement is explicit, never assumed.** Every agreer in
  `record_group_decision` must be a room member; a non-member
  in the list is rejected. An empty agreement list records the
  decision without claiming anyone agreed.
- **ToS gate (decision K-15) enforced.** In `live` mode a
  marketplace source that forbids automated reading (Tokopedia,
  Shopee, Blibli) is held: the reader is never called for it
  and the item is reported as held. `fixture` mode is always
  allowed. No live marketplace read is claimed.

## Group-chat routing

`handle_group_inbound` takes an optional `research` service.
When the message is a compare command ("@puspa compare",
"@puspa bandingkan harga", ...), the reply is the rendered
comparison and the agent is never called — the same hook shape
as the B9 board command. Any other addressed message still
goes to the agent. A non-member sender gets a short refusal
instead of the comparison.

## Honest limits

- **No live marketplace read is verified.** Live mode holds
  all three marketplaces behind the ToS gate (decision K-15,
  still open). The comparison works end to end only in
  `fixture` mode with injected readers, or against a source
  whose ToS allows live reading.
- **B3 (shared graphs) is not built here.** The group decision
  is stored as a room-memory `decision` row with the agreed-by
  user ids and the comparison task id, so B3 can adopt the
  record later; no shared price/seller graph is written by
  this ticket.
- **No reliability-score downgrade.** B1 lowers a verdict when
  the source's reliability score is below 80%, but that score
  is per category and group links carry no category, so the
  verdict here comes straight from the verifier. Adding a
  per-link category (or a per-source score) is a follow-up.
- **Seller coverage depends on the reader.** The generic
  JSON-LD extraction finds a seller only when the page reports
  one in `offers.seller`; most listings will report the seller
  as unknown until a reader extracts store names.
- **No live Telegram/WhatsApp delivery is verified.** Channel
  tests use fake adapters (same as B6/G14); WhatsApp groups
  stay a postponed surface (plan section 12.4).

## B14 (Indonesian context) notes

- Everyday Indonesian commands work ("bandingkan harga"),
  alongside English ("compare").
- Prices render the Indonesian way (Rp 12.499.000).
- Decisions are recorded without forcing a public vote: the
  agent records who agreed when the group says so, and an
  empty agreement list is a valid record, not an error.
