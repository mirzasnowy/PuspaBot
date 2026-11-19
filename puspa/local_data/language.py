"""Everyday Indonesian language handling (ticket B5).

Users write the way they talk: everyday Indonesian, mixed
Indonesian-English, and commerce shorthand — ``ongkir``, ``COD``,
``PO``, ``DP``, ``cek harga``, ``bisa nego ga``. This module
normalises that input into the canonical English terms the
intent engine and the price-check flow already understand, so a
user is never forced into formal language to be understood.

:func:`normalize_id_text` rewrites the text term by term and
reports which source terms it found, so a caller can see what
was understood and what was left untouched. Terms it does not
know pass through unchanged — normalisation never invents a
meaning for a word it has not seen.

:func:`extract_destination_city` and :func:`extract_courier`
pull the shipping lane out of the same text, using the alias
tables in :mod:`puspa.local_data.rates` (one source of truth
for city and courier spellings).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .rates import CITY_PATTERN, COURIER_PATTERN, normalize_city, normalize_courier

#: Multi-word phrases first (longest first at runtime), then
#: single words. Each maps an everyday Indonesian or mixed
#: expression to the canonical term the rest of Puspa uses.
_PHRASE_MAP: dict[str, str] = {
    "cek harga": "check price",
    "cek harganya": "check price",
    "cek hrg": "check price",
    "harga pas": "fixed price",
    "harga net": "fixed price",
    "bisa nego": "negotiable",
    "bisa kurang": "negotiable",
    "ongkos kirim": "shipping cost",
    "biaya kirim": "shipping cost",
    "ongkir": "shipping cost",
    "cash on delivery": "cash on delivery",
    "bayar di tempat": "cash on delivery",
    "bayar ditempat": "cash on delivery",
    "pre order": "pre-order",
    "po dulu": "pre-order",
    "down payment": "down payment",
    "uang muka": "down payment",
    "ready stock": "in stock",
    "ready stok": "in stock",
    "kirim ke": "ship to",
    "dikirim ke": "ship to",
    "berapa harganya": "how much is the price",
    "berapa harga": "how much is the price",
}

#: Single-word terms. "kak", "gan", "bang", "min", "mas", "mbak"
#: are address words with no content meaning: they are dropped
#: (mapped to the empty string) so they never pollute matching.
_WORD_MAP: dict[str, str] = {
    "cek": "check",
    "harga": "price",
    "harganya": "price",
    "hrg": "price",
    "berapa": "how much",
    "ongkir": "shipping cost",
    "cod": "cash on delivery",
    "po": "pre-order",
    "dp": "down payment",
    "nego": "negotiable",
    "ready": "in stock",
    "stok": "stock",
    "stock": "stock",
    "kirim": "ship",
    "dikirim": "shipped",
    "tf": "transfer",
    "trf": "transfer",
    "bayar": "pay",
    "pembayaran": "payment",
    "beli": "buy",
    "jual": "sell",
    "barang": "product",
    "toko": "store",
    "penjual": "seller",
    "pembeli": "buyer",
    "aman": "safe",
    "amanah": "trusted",
    "ori": "original",
    "kw": "replica",
    "palsu": "fake",
    "garansi": "warranty",
    "kak": "",
    "gan": "",
    "bang": "",
    "min": "",
    "mas": "",
    "mbak": "",
    "bro": "",
    "sis": "",
}

#: "ke <city>" / "kirim ... <city>" is the destination pattern.
#: Checked before the bare city scan so "kirim ke Bandung" wins
#: over a city named earlier in the text (e.g. the seller's city).
_DESTINATION_PATTERNS = (
    re.compile(r"\b(?:ke|kirim|dikirim|ship to|to)\s+([a-z]+(?:\s+[a-z]+)?)", re.IGNORECASE),
)


@dataclass(frozen=True)
class NormalizedText:
    """One normalised input: the canonical text plus what was found."""

    canonical: str
    terms_found: tuple[str, ...] = ()
    original: str = ""

    def to_dict(self) -> dict:
        return {
            "canonical": self.canonical,
            "terms_found": list(self.terms_found),
            "original": self.original,
        }


def _replace_phrases(text: str, found: list[str]) -> str:
    """Rewrite multi-word phrases, longest first."""
    for phrase in sorted(_PHRASE_MAP, key=len, reverse=True):
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        if pattern.search(text):
            found.append(phrase)
            text = pattern.sub(_PHRASE_MAP[phrase], text)
    return text


def _replace_words(text: str, found: list[str]) -> str:
    """Rewrite single words; address words are dropped."""

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        lowered = word.lower()
        if lowered in _WORD_MAP:
            found.append(lowered)
            return _WORD_MAP[lowered]
        return word

    return re.sub(r"[a-z]+", replace, text, flags=re.IGNORECASE)


def normalize_id_text(text: str) -> NormalizedText:
    """Normalise everyday Indonesian / mixed ID-EN input.

    Lowercases, expands commerce shorthand and common words to
    their canonical English terms, and drops address words
    (``kak``, ``gan``). Unknown words pass through unchanged.
    ``terms_found`` lists the source terms that were rewritten,
    in the order found (phrases first, then words).
    """
    original = text or ""
    found: list[str] = []
    lowered = original.lower()
    lowered = _replace_phrases(lowered, found)
    lowered = _replace_words(lowered, found)
    canonical = " ".join(lowered.split())
    return NormalizedText(
        canonical=canonical,
        terms_found=tuple(dict.fromkeys(found)),
        original=original,
    )


def extract_destination_city(text: str) -> str | None:
    """Destination city named in the text, canonical, or None.

    Prefers a city after ``ke`` / ``kirim`` ("ongkir ke Bandung",
    "kirim ke Surabaya"); otherwise any known city spelling in
    the text. Returns None when no known city is named — never a
    guess from an unknown name.
    """
    text = text or ""
    lowered = text.lower()
    for pattern in _DESTINATION_PATTERNS:
        for match in pattern.finditer(lowered):
            candidate = match.group(1).strip()
            # Try the full two-word candidate, then the first word.
            for attempt in (candidate, candidate.split()[0]):
                try:
                    return normalize_city(attempt)
                except ValueError:
                    continue
    found = CITY_PATTERN.search(lowered)
    if found is None:
        return None
    try:
        return normalize_city(found.group(1))
    except ValueError:  # pragma: no cover - pattern only matches known spellings
        return None


def extract_courier(text: str) -> str | None:
    """Courier named in the text, canonical, or None."""
    match = COURIER_PATTERN.search((text or "").lower())
    if match is None:
        return None
    try:
        return normalize_courier(match.group(1))
    except ValueError:  # pragma: no cover - pattern only matches known spellings
        return None


def extract_shipping_lane(text: str) -> tuple[str | None, str | None]:
    """(destination city, courier) named in the text, each or None."""
    return extract_destination_city(text), extract_courier(text)
