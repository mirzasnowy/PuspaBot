# Check Before You Buy — beta slice (ticket price-check beta slice)

The one vertical slice for the limited beta: the user forwards a
product link, a seller chat, or an account number, and PuspaBot returns
one structured, evidence-backed answer in a single turn: a fair
price, a seller track record, an account status, and act /
do-not-act advice.

## Flow

1. **Classify** (`puspa/check_before_buy/classifier.py`). The
   forwarded text is classified as `product_link`, `seller_chat`,
   `account_number`, or `unknown`. A chat wins over a bare link when
   the text has chat markers. A `Rp12.499.000` price is never
   mistaken for an account number (account numbers are 10-16 digits
   with no dots or commas). A seller is only named when the text
   names one; a seller name is never guessed from a URL.
2. **Look up recorded data** (`puspa/check_before_buy/records.py`).
   A `RecordStore` holds price observations, seller records, and
   account records that were recorded earlier. It has no network
   access and performs no live reads. Cassettes are plain JSON files
   (see `cassettes/check_before_buy_sample.json`) loaded with
   `load_cassette`.
3. **Answer** (`puspa/check_before_buy/answer.py`). One
   `CheckAnswer` with three sections plus advice (see "Evidence
   rules" below) and a one-turn plain-language reply (`text`).
4. **Record** (`puspa/check_before_buy/outcome.py`). Price
   observations the answer rests on become evidence rows through the
   existing evidence path (`puspa.evidence`: record, independent
   re-read against the recorded store, save), and the whole answer
   is written as one `evidence_recorded` event on the existing
   append-only ledger (`puspa.ledger`). No second ledger is
   invented. Seller and account findings have no listing URL of
   their own, so they travel inside that ledger event's content.
5. **Wire in** (`puspa/check_before_buy/wiring.py` and
   `puspa/turns/intercepts.py`). The slice is the turn loop's fifth
   intercepted tool, `check_before_buy`: the loop asks
   `InterceptedTools` first, so the tool never reaches the registry.
   `InterceptedTools` takes optional `check_store` (recorded data;
   an empty store answers cannot-verify everywhere) and `check_dsn`
   (when set, outcomes are recorded; otherwise the answer is
   computed only).

## Evidence rules

- **Fair price**: the median of recorded price observations for the
  product, one per source. `verified` needs observations from at
  least two distinct sources; one source is `uncertain`; none is
  `cannot_verify` with no number at all.
- **Seller track record**: only what the recorded store says about
  the named seller. No record, or no seller name, is
  `cannot_verify`.
- **Account status**: only what the recorded store says about the
  exact account number. An unknown number is `cannot_verify`, never
  "looks safe". Numbers are masked in the answer (first two and last
  two digits only).
- **Advice**: `act` only when every check that applies is green
  (fair price verified, seller record clean, account record clean
  when an account is involved; for an account-only input, a clean
  recorded account status is enough). Any flagged seller or account
  gives `do_not_act`. Anything else gives `cannot_advise` with the
  missing pieces listed, so the user knows exactly what is
  unverified.

## Live-execution hold (decision 15, still open)

Live price execution stays HELD: Tokopedia, Shopee, and Blibli each
forbid automated reading in their terms (see `puspa/pricing/tos.py`,
`may_read`). This slice therefore NEVER fetches a live price from
any marketplace or any other source whose terms are unchecked.
Requesting `mode="live"` returns a held answer — every section
`cannot_verify`, advice `cannot_advise`, `live_execution_held`
true — and no reader, HTTP call, or marketplace fetch is attempted.
The package contains no network import and no send or pay path: it
only reads recorded data, writes evidence rows and one ledger event,
and returns text. Lifting the hold needs decision 15 to be decided
first; the code change then is to supply a store seeded from an
approved source, not to add a live reader here.

## What is NOT live / NOT verified

- No live price, seller, or account lookup exists in this slice.
  Every number comes from recorded data supplied by the caller.
- A `verified` fair price means "two recorded sources agree", not
  "checked live just now". Recorded observations can go stale; the
  cassette carries `observed_at` so staleness is visible.
- A `recorded_clean` seller or account means "no adverse reports on
  record", not "proven safe".
- There is no PWA API endpoint for the slice yet: the turn loop is
  the wired call path, and the PWA surface is the follow-up.
- There is no cross-user shared fraud graph (that is ticket shared anti-fraud and price graphs):
  one store serves one check, and records are not pooled across
  users by this slice.

## Tests

`tests/test_check_before_buy.py` (29 tests): classification of all
three input kinds plus unknown, price-vs-account-number confusion,
evidence-backed answer shape, missing evidence to `cannot_verify`,
live-mode hold, no network/send/pay path in the package source,
cassette loading, outcome recording (evidence rows + ledger event),
and turn-loop interception. Neighbour suites run alongside it:
`test_turn_intercepts.py` (including the updated intercepted-tool
set assertion), `test_evidence.py`, `test_compare.py`,
`test_ledger.py`, `test_catalog_tos.py`, `test_turn_loop.py`.
