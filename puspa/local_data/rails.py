"""Naming the correct Indonesian payment rail (ticket B5).

Given a payment mention in everyday Indonesian ("bayar pake
qris", "tf bca", "va mandiri", "gopay aja"), this module names
the rail it belongs to: QRIS, virtual account, bank transfer,
GoPay, OVO, DANA, or cash on delivery. Naming only — it never
prepares a payment, never calls a gateway, never moves money.
Actual payment preparation is ticket B8; this is the data asset
B8 will read from.

Detection is keyword-based and conservative: it only names a
rail the text actually mentions. No mention means ``None`` —
never a guessed default rail. When the text names a bank
(``va bca``, ``transfer mandiri``) the bank is carried on the
rail so the caller can say "virtual account BCA", not just
"virtual account".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Rail kinds. ``e_wallet`` covers GoPay, OVO, and DANA; each
#: wallet is its own rail because the user pays through a
#: different app and a different balance.
RAIL_QRIS = "qris"
RAIL_VIRTUAL_ACCOUNT = "virtual_account"
RAIL_BANK_TRANSFER = "bank_transfer"
RAIL_E_WALLET = "e_wallet"
RAIL_CASH_ON_DELIVERY = "cash_on_delivery"


@dataclass(frozen=True)
class PaymentRail:
    """One named Indonesian payment rail."""

    rail_id: str  # "qris" | "gopay" | "ovo" | "dana" | "virtual_account" | ...
    display_name: str  # what to show the user, e.g. "QRIS" or "Virtual account BCA"
    kind: str  # one of the RAIL_* constants above
    bank: str = ""  # bank name, when the mention names one (VA / transfer)
    note: str = ""  # one plain sentence about the rail, for the reply


#: Indonesian banks users name in VA / transfer mentions.
_BANKS: tuple[str, ...] = (
    "BCA",
    "Mandiri",
    "BRI",
    "BNI",
    "CIMB Niaga",
    "Permata",
    "Danamon",
    "BSI",
    "BTN",
    "CIMB",
)

_BANK_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(bank) for bank in sorted(_BANKS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

#: QRIS is one national standard: any bank app or e-wallet can
#: scan it, so it is named on its own, before any wallet check.
_QRIS_PATTERN = re.compile(r"\bqris\b", re.IGNORECASE)

_VA_PATTERNS = (
    # The "-nya" suffix is everyday Indonesian ("virtual
    # accountnya" = "the virtual account"): tolerate it.
    re.compile(r"\bvirtual\s+acc(?:ount)?(?:nya)?\b", re.IGNORECASE),
    re.compile(r"\bva\b", re.IGNORECASE),
)

_WALLET_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\bgopay\b", re.IGNORECASE),
        "gopay",
        "GoPay",
    ),
    (
        re.compile(r"\bovo\b", re.IGNORECASE),
        "ovo",
        "OVO",
    ),
)

#: DANA needs payment context: ``dana`` is also the ordinary word
#: for "funds" ("butuh dana" is not the wallet), so it only counts
#: next to a payment word ("pake dana", "via dana", "saldo dana").
_DANA_PATTERN = re.compile(
    r"\b(?:pake|pakai|via|lewat|bayar|saldo)\b[^.]{0,12}\bdana\b", re.IGNORECASE
)

_TRANSFER_PATTERNS = (
    re.compile(r"\btransfer\b", re.IGNORECASE),
    re.compile(r"\btf\b", re.IGNORECASE),
    re.compile(r"\btrf\b", re.IGNORECASE),
)

_COD_PATTERN = re.compile(r"\bcod\b", re.IGNORECASE)


def _bank_in(text: str) -> str:
    """Bank name mentioned in the text, canonical cased, else ""."""
    match = _BANK_PATTERN.search(text or "")
    if match is None:
        return ""
    found = match.group(1)
    for bank in _BANKS:
        if bank.lower() == found.lower():
            return bank
    return found


def detect_rail(text: str) -> PaymentRail | None:
    """Name the payment rail mentioned in ``text``, or None.

    Priority is deliberate: QRIS first (it is a standard, not a
    wallet), then virtual account, then a named wallet, then a
    plain bank transfer, then COD. A text naming two rails names
    the first in that priority — the caller can call again on a
    split text if it needs every rail.
    """
    text = text or ""
    if _QRIS_PATTERN.search(text):
        return PaymentRail(
            rail_id="qris",
            display_name="QRIS",
            kind=RAIL_QRIS,
            note="QRIS: scan one code from any bank app or e-wallet.",
        )
    if any(pattern.search(text) for pattern in _VA_PATTERNS):
        bank = _bank_in(text)
        display = f"Virtual account {bank}" if bank else "Virtual account"
        return PaymentRail(
            rail_id="virtual_account",
            display_name=display,
            kind=RAIL_VIRTUAL_ACCOUNT,
            bank=bank,
            note=(
                f"Virtual account{(' ' + bank) if bank else ''}: "
                "pay to the virtual account number the seller gives you."
            ),
        )
    for pattern, rail_id, display in _WALLET_PATTERNS:
        if pattern.search(text):
            return PaymentRail(
                rail_id=rail_id,
                display_name=display,
                kind=RAIL_E_WALLET,
                note=f"{display}: pay from your {display} balance.",
            )
    if _DANA_PATTERN.search(text):
        return PaymentRail(
            rail_id="dana",
            display_name="DANA",
            kind=RAIL_E_WALLET,
            note="DANA: pay from your DANA balance.",
        )
    if any(pattern.search(text) for pattern in _TRANSFER_PATTERNS):
        bank = _bank_in(text)
        display = f"Bank transfer ({bank})" if bank else "Bank transfer"
        return PaymentRail(
            rail_id="bank_transfer",
            display_name=display,
            kind=RAIL_BANK_TRANSFER,
            bank=bank,
            note=(
                f"Bank transfer{(' ' + bank) if bank else ''}: "
                "transfer to the seller's account number and keep the receipt."
            ),
        )
    if _COD_PATTERN.search(text):
        return PaymentRail(
            rail_id="cash_on_delivery",
            display_name="Cash on delivery (COD)",
            kind=RAIL_CASH_ON_DELIVERY,
            note="Cash on delivery: pay the courier in cash when the package arrives.",
        )
    return None


def rail_name(text: str) -> str | None:
    """Display name of the rail mentioned in ``text``, or None."""
    rail = detect_rail(text)
    return rail.display_name if rail is not None else None
