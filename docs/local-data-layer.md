# Local Indonesian data layer (local Indonesian data layer)

Indonesian prices, sellers, and payment rails as data assets, plus
Indonesian language handling. The layer lives in `puspa/local_data/`
and is the first consumer's data source for the Check Before You Buy
slice (price-check beta slice).

## What the layer holds

| Module | Holds |
| --- | --- |
| `puspa/local_data/rates.py` | Shipping rates by city and courier, with a COD flag |
| `puspa/local_data/rails.py` | Naming of Indonesian payment rails |
| `puspa/local_data/language.py` | Everyday Indonesian / mixed ID-EN normalisation |

No seller data is invented here. Seller track records stay in
`puspa/check_before_buy/records.py` and are only ever read from
recorded data. No live fetch happens anywhere in this package.

## Shipping rates: seeded sample data, not live tariffs

The rates shipped in `rates.py` are **seeded sample data**: 10
cities (Jakarta, Bandung, Surabaya, Yogyakarta, Semarang, Medan,
Makassar, Denpasar, Tangerang, Bekasi) and 8 couriers (JNE, J&T,
SiCepat, AnterAja, POS Indonesia, Ninja Xpress, GoSend,
GrabExpress), all from a Jakarta origin hub, priced per started
kilogram. Every row carries its provenance (origin + destination +
courier + rate + COD flag + source label + seed date), and every
quote carries it too: the source label is
`seeded sample rate (local Indonesian data layer)`, seeded 2026-11-19.

The price-check beta slice answer renders the label next to the number ("sample
rates, not a live tariff"), so a user never mistakes a seed rate
for a courier's current price. A real courier tariff changes
daily; the seed is an illustrative stand-in.

Lookup rules:

- `RateStore.quote(origin, destination, courier, weight_kg=1.0)`
  bills per started kilogram (2.5 kg bills as 3 kg) and is
  deterministic: the same lookup always gives the same number.
- An unknown city raises `UnknownCityError`; an unknown courier
  raises `UnknownCourierError`. A courier with no rate on that
  lane (for example GoSend outside Jakarta, an instant courier)
  also raises `UnknownCourierError` — never a guessed rate.
- `cheapest(origin, destination)` and
  `cod_couriers(origin, destination)` answer the two questions a
  buyer actually asks: what is cheapest, and who offers COD.
- City and courier spellings are normalised through alias tables
  (`bdg` -> Bandung, `jogja` -> Yogyakarta, `jnt` -> J&T), so
  the spellings users actually type resolve to one canonical name.

## Refresh seam

`RateLoader` is the interface a real data source implements:
`load()` returns complete `ShippingRate` rows. `SeedRateLoader`
serves the seed in `rates.py`; `JsonRateLoader(path)` serves the
same rows from a JSON file, so a refreshed rate file replaces the
seed without touching any caller:

```json
{"rates": [
  {"origin": "Jakarta", "destination": "Bandung", "courier": "JNE",
   "rate_per_kg_idr": 12000, "cod_available": true,
   "source_label": "courier tariff 2026-11", "seeded_at": "2026-11-19"}
]}
```

A row with a missing or malformed field raises `ValueError` at
load time, and duplicate rows are rejected at store construction:
a half-loaded rate table must never serve quotes. Quotes built
from a JSON file carry that file's `source_label` instead of the
seed label. Wiring a live courier feed is follow-up work: it
implements `RateLoader` against a real tariff source and is
passed to `RateStore` (and through `check_before_buy`'s
`rate_store` parameter).

## Payment rails: naming only

`detect_rail(text)` names the rail a payment mention belongs to:

- QRIS (one national standard, named before any wallet)
- Virtual account, with the bank when named (`va BCA` ->
  "Virtual account BCA")
- Bank transfer, with the bank when named (`tf mandiri`)
- E-wallets: GoPay, OVO, DANA
- Cash on delivery (COD)

No mention means `None` — never a guessed default rail. DANA
needs payment context (`pake dana`, `via dana`, `saldo dana`)
because `dana` is also the ordinary word for "funds" ("butuh
dana" is not the wallet).

Naming only: this module never prepares a payment, never calls a
gateway, never moves money. Actual payment preparation is ticket
agent payments (prepare-only phase); this is the data asset agent payments (prepare-only phase) will read from.

## Language handling

`normalize_id_text(text)` rewrites everyday Indonesian and mixed
Indonesian-English input into the canonical English terms the
intent engine and the price-check flow already understand:

- `ongkir` -> "shipping cost", `cek harga` -> "check price"
- `COD` -> "cash on delivery", `PO` -> "pre-order",
  `DP` -> "down payment"
- `bisa nego` / `harga pas` -> "negotiable" / "fixed price"
- `ready` -> "in stock", `tf` -> "transfer"
- Address words with no content meaning (`kak`, `gan`, `bang`,
  `min`, `mas`, `mbak`) are dropped

It returns the canonical text plus `terms_found` (which source
terms were rewritten), so a caller can see what was understood.
Terms it does not know pass through unchanged — normalisation
never invents a meaning for a word it has not seen.

`extract_destination_city(text)` and `extract_courier(text)`
pull the shipping lane out of the same text, preferring a city
after `ke` / `kirim` ("ongkir ke Bandung"). They return `None`
when nothing known is named, never a guess.

## Wiring into price-check beta slice

`check_before_buy` gains two sections on `CheckAnswer`:

- `shipping`: `verified` with city, courier, cost for 1 kg from
  Jakarta, COD flag, and source label; `cannot_verify` when the
  lane or courier has no rate on record; `not_applicable` when
  no destination city is named. With no courier named, the
  cheapest courier on the lane is quoted and named.
- `payment_rail`: the rail named in the input, or `None`.

Both are in `CheckAnswer.to_dict()` and in the rendered reply
text. The shipping line always carries the sample-rate label.
`handle_check_before_buy` accepts an optional `rate_store` and
forwards it; the default is the seeded store. Advice
(act / do-not-act / cannot-advise) is unchanged: shipping and
rail are information, not verdict inputs — a missing shipping
quote never flips advice, it only adds `shipping` to
`cannot_verify`.

## Limits (honest)

- Rates are seeded samples, not live tariffs. They are wrong
  the moment a courier changes its price, and the seed only
  covers 10 cities from one Jakarta origin.
- Quotes are for 1 kg by default; the price-check beta slice path does not extract
  a weight from the input yet (the store itself supports any
  weight).
- Language normalisation is a hand-written term map, not a
  model: it understands the commerce terms listed above and
  passes everything else through. Regional terms beyond the
  map (for example Javanese or Sundanese commerce words) are
  not normalised yet.
- Rail detection is keyword-based: a text naming two rails
  names the first in priority order (QRIS, virtual account,
  wallet, transfer, COD).
- No real courier or payment integration exists yet. The
  refresh seam (`RateLoader` / `JsonRateLoader`) and the rail
  registry are the places that work lands.
