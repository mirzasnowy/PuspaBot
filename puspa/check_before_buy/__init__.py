"""Check Before You Buy (ticket B1): the beta slice.

One turn: the user forwards a product link, a seller chat, or an
account number, and Puspa returns one structured, evidence-backed
answer: a fair price, a seller track record, an account status, and
act / do-not-act advice.

LIVE-EXECUTION HOLD (decision 15, still open): live price reads from
Tokopedia, Shopee, and Blibli are HELD because each marketplace's
terms forbid automated reading (see ``puspa.pricing.tos``). This
slice therefore NEVER fetches a live price. Every number it reports
comes from recorded observations supplied by the caller (test
fixtures, recorded cassettes, or a store seeded from an approved
source). A missing observation produces an explicit
``cannot_verify`` entry, never a guessed number. Asking for
``mode="live"`` returns a held answer instead of reading anything.

The slice has no send path and no pay path: it only reads recorded
data, writes evidence rows and one ledger event, and returns text.
"""

from __future__ import annotations

from .answer import CheckAnswer, check_before_buy
from .classifier import ClassifiedInput, classify_input
from .records import (
    AccountRecord,
    PriceObservation,
    RecordStore,
    SellerRecord,
    load_cassette,
)
from .wiring import handle_check_before_buy

__all__ = [
    "AccountRecord",
    "CheckAnswer",
    "ClassifiedInput",
    "PriceObservation",
    "RecordStore",
    "SellerRecord",
    "check_before_buy",
    "classify_input",
    "handle_check_before_buy",
    "load_cassette",
]
