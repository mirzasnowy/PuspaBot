"""Classify one forwarded input for the Check Before You Buy slice.

The three input kinds from ticket B1:

- ``product_link``: the text carries a marketplace product URL.
- ``seller_chat``: the text reads like a forwarded seller chat
  (speaker lines, haggling, a transfer request), with or without a
  product URL inside it.
- ``account_number``: the text carries a bank account number and
  nothing else checkable.
- ``unknown``: none of the above; the slice answers that it cannot
  tell what was forwarded instead of guessing.

Classification is heuristic and conservative: a chat wins over a
bare link when the text has chat markers, because the seller's words
are the thing to check. An account number is only recognised when
it looks like a bank account (10-16 digits, no dots or commas, so a
``Rp12.499.000`` price is never mistaken for one) and no product
URL is present. The classifier never invents a seller name from a
URL: a seller is only named when the text names one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MARKETPLACE_DOMAINS = ("tokopedia.com", "shopee.co.id", "blibli.com")

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
# A bank account number: 10-16 digits, allowing spaces or dashes
# between digit groups, but never dots or commas (those mark prices).
_ACCOUNT_RE = re.compile(r"\b\d[\d\s\-]{8,18}\d\b")
_SELLER_RE = re.compile(
    r"\b(?:toko|seller|store|shop|penjual)\b\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9 _\-.]{1,48})",
    re.IGNORECASE,
)
_CHAT_MARKERS = (
    "seller:",
    "buyer:",
    "penjual:",
    "pembeli:",
    "kak ",
    "gan ",
    "transfer",
    "tf ",
    "min,",
    "ready kak",
    "ready gan",
    "bisa nego",
    "harga pas",
    "chat with",
    "forwarded",
)


@dataclass
class ClassifiedInput:
    """What the forwarded text is, and what was extracted from it."""

    kind: str  # "product_link" | "seller_chat" | "account_number" | "unknown"
    text: str
    product_url: str | None = None
    account_number: str | None = None  # digits only, when recognised
    seller_name: str | None = None


def extract_product_url(text: str) -> str | None:
    """First marketplace product URL in the text, if any."""
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,);]")
        lowered = url.lower()
        if any(domain in lowered for domain in MARKETPLACE_DOMAINS):
            return url
    return None


def extract_account_number(text: str) -> str | None:
    """First bank-account-like digit run in the text, if any.

    Digit runs that sit next to ``Rp`` or that contain dots/commas
    are prices, not account numbers, and are ignored.
    """
    for match in _ACCOUNT_RE.finditer(text or ""):
        raw = match.group(0)
        start = match.start()
        before = text[max(0, start - 4) : start].lower()
        if "rp" in before:
            continue
        digits = re.sub(r"[\s\-]", "", raw)
        if 10 <= len(digits) <= 16 and digits.isdigit():
            return digits
    return None


def extract_seller_name(text: str) -> str | None:
    """Seller name when the text names one, else None (never guessed)."""
    match = _SELLER_RE.search(text or "")
    if not match:
        return None
    name = match.group(1).strip().rstrip(".,;:!?")
    # Drop a trailing clause ("toko X menjual ..." -> "X").
    name = re.split(r"\s+(menjual|sells|jual|ya|kak|gan)\b", name, flags=re.IGNORECASE)[0]
    name = " ".join(name.split())
    return name or None


def _looks_like_chat(text: str) -> bool:
    lowered = text.lower()
    if "\n" in text.strip() and sum(1 for m in _CHAT_MARKERS if m in lowered) >= 1:
        return True
    return sum(1 for m in _CHAT_MARKERS if m in lowered) >= 2


def classify_input(text: str) -> ClassifiedInput:
    """Classify one forwarded input into its kind plus extracted parts."""
    text = text or ""
    url = extract_product_url(text)
    account = extract_account_number(text)
    seller = extract_seller_name(text)
    if _looks_like_chat(text):
        return ClassifiedInput(
            kind="seller_chat",
            text=text,
            product_url=url,
            account_number=account,
            seller_name=seller,
        )
    if url is not None:
        return ClassifiedInput(
            kind="product_link",
            text=text,
            product_url=url,
            account_number=account,
            seller_name=seller,
        )
    if account is not None:
        return ClassifiedInput(
            kind="account_number",
            text=text,
            account_number=account,
            seller_name=seller,
        )
    return ClassifiedInput(kind="unknown", text=text, seller_name=seller)
