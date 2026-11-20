"""Receipt parser: OCR text in, a confirmable expense draft out.

The parser only reads numbers that are tied to a visible line of
the photo. Every amount it emits carries an evidence id of the
form ``receipt-<photo_id>-line-<n>`` (or
``receipt-<photo_id>-total`` for the total), naming the photo and
the line the amount was read from — the same evidenced-number
rule as :mod:`puspa.evidence`. A number with no line behind it is
never emitted: it is held, not guessed.

The parser treats OCR text as untrusted data. Instructions,
links, or commands inside a photo (``"transfer money"``,
``"ignore the rules"``, a fake approval) are never acted on: the
parser only extracts merchant, date, line items, and totals, and
nothing it returns can trigger an action by itself — the draft
still needs the user to confirm it (see
:mod:`puspa.pwa.receipt_api`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "ReceiptDraft",
    "ReceiptLine",
    "evidence_id_for",
    "parse_amount",
    "parse_receipt",
]

#: Amounts are Indonesian rupiah unless the text says otherwise.
DEFAULT_CURRENCY = "Rp"

#: A line item is a description followed by an amount at the end
#: of the line, for example ``"Beras 5kg  52.500"`` or
#: ``"Kopi susu - Rp 18.000"``.
_ITEM_RE = re.compile(
    r"^(?P<name>.+?)\s+(?:Rp\.?\s*|IDR\s*)?(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d+)\s*$",
    re.IGNORECASE,
)

#: Lines that name the total: the largest of these wins.
_TOTAL_RE = re.compile(
    r"^(?:total|jumlah|grand total|total bayar|total belanja|tagihan)\b"
    r"[^0-9]*?(?:Rp\.?\s*|IDR\s*)?(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d+)\s*$",
    re.IGNORECASE,
)

#: Dates on Indonesian receipts: 20/11/2026, 20-11-2026, 2026-11-20.
_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})[/-](?P<month>\d{1,2})[/-](?P<year>\d{4})"
    r"|(?P<year2>\d{4})-(?P<month2>\d{1,2})-(?P<day2>\d{1,2})"
)

#: Words that mark a line as not an item (headers, footers, thanks).
_NON_ITEM_RE = re.compile(
    r"^(total|jumlah|grand total|subtotal|sub total|kembalian|kembali|tunai|cash|"
    r"terima kasih|thank you|selamat|struk|nota|no\.?|telp|alamat|kasir|"
    r"tanggal|date|jam|time|ppn|pajak|diskon|discount|ongkir|biaya|"
    r"jl\.?|jalan)\b",
    re.IGNORECASE,
)

#: Lines that read like instructions or commands. OCR text is
#: untrusted input (plan section 8.5): a photo can carry
#: "ignore previous instructions, transfer money" as printed
#: text, and the parser must never turn such a line into an item,
#: a merchant, or an amount. These lines are dropped entirely —
#: not parsed, not counted as held amounts.
_INSTRUCTION_RE = re.compile(
    r"ignore\s+(all|the|previous)|disregard|transfer\s+(rp|idr|\d)|"
    r"approve\s+(a\s+)?payment|^\s*system\s*:|send\s+money|delete\s+all",
    re.IGNORECASE,
)


def evidence_id_for(photo_id: str, area: str) -> str:
    """Evidence id for one parsed value: photo + area.

    ``area`` is ``"total"`` for the total, or ``"line-<n>"`` for
    line ``n`` of the OCR text (0-based). The id names the photo
    and the exact line, so a reader can check the number against
    the photo.
    """
    return f"receipt-{photo_id}-{area}"


def parse_amount(raw: str) -> int | None:
    """Parse an Indonesian amount string to whole rupiah.

    ``"52.500"`` -> 52500, ``"1.234.567"`` -> 1234567,
    ``"12.500,00"`` -> 12500 (cents dropped: expenses are kept in
    whole rupiah). Returns ``None`` for text that is not an amount.
    """
    text = raw.strip().replace("Rp", "").replace("IDR", "").strip()
    if not text or not re.search(r"\d", text):
        return None
    if "," in text and "." in text:
        # Indonesian decimals: thousands use ".", decimals use ",".
        text = text.replace(".", "").split(",")[0]
    elif "," in text:
        head, _, tail = text.partition(",")
        # "12,500" with a 3-digit tail is a thousands separator;
        # "12,50" with a 2-digit tail is a decimal part.
        text = head + tail if len(tail) == 3 else head
    else:
        text = text.replace(".", "")
    if not text.isdigit():
        return None
    return int(text)


@dataclass(frozen=True)
class ReceiptLine:
    """One parsed line item, with the evidence for its amount."""

    name: str
    amount: int
    evidence_id: str
    line_index: int


@dataclass
class ReceiptDraft:
    """A receipt as the user will confirm it: merchant, date,
    items, and total, every amount backed by an evidence id.

    ``total`` is the total printed on the receipt when the parser
    found one; otherwise it is the sum of the line items, and
    ``total_is_sum`` says so. ``held_amounts`` counts amounts the
    parser saw but could not tie to a line — they are reported,
    never added to the total.
    """

    merchant: str | None
    expense_date: str | None
    currency: str
    lines: list[ReceiptLine] = field(default_factory=list)
    total: int | None = None
    total_evidence_id: str | None = None
    total_is_sum: bool = False
    held_amounts: int = 0

    def to_dict(self) -> dict:
        """JSON shape the receipt API returns for confirmation."""
        return {
            "merchant": self.merchant,
            "expense_date": self.expense_date,
            "currency": self.currency,
            "lines": [
                {
                    "name": line.name,
                    "amount": line.amount,
                    "evidence_id": line.evidence_id,
                    "line_index": line.line_index,
                }
                for line in self.lines
            ],
            "total": self.total,
            "total_evidence_id": self.total_evidence_id,
            "total_is_sum": self.total_is_sum,
            "held_amounts": self.held_amounts,
        }


def _clean_merchant(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip(" -|*")[:120]


def _find_date(lines: list[str]) -> str | None:
    for line in lines:
        match = _DATE_RE.search(line)
        if not match:
            continue
        if match.group("year"):
            day, month, year = match.group("day", "month", "year")
        else:
            year, month, day = match.group("year2", "month2", "day2")
        try:
            day_i, month_i, year_i = int(day), int(month), int(year)
        except ValueError:
            continue
        if 1 <= day_i <= 31 and 1 <= month_i <= 12 and 1900 <= year_i <= 2100:
            return f"{year_i:04d}-{month_i:02d}-{day_i:02d}"
    return None


def parse_receipt(text: str, *, photo_id: str) -> ReceiptDraft:
    """Parse OCR text into a confirmable expense draft.

    The merchant is the first line that is not a date, a total,
    or a header word. Line items are lines ending in an amount.
    The total is the largest amount on a total line, or the sum
    of the items when no total line exists.
    """
    raw_lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in raw_lines if line]
    merchant: str | None = None
    for line in lines:
        if _INSTRUCTION_RE.search(line):
            continue
        if _DATE_RE.search(line) or _TOTAL_RE.match(line) or _NON_ITEM_RE.match(line):
            continue
        if re.search(r"\d", line) and not _ITEM_RE.match(line):
            continue
        merchant = _clean_merchant(line)
        break
    expense_date = _find_date(lines)
    currency = DEFAULT_CURRENCY
    items: list[ReceiptLine] = []
    total: int | None = None
    held = 0
    for index, line in enumerate(lines):
        if _INSTRUCTION_RE.search(line):
            continue
        total_match = _TOTAL_RE.match(line)
        if total_match:
            amount = parse_amount(total_match.group("amount"))
            if amount is not None and (total is None or amount > total):
                total = amount
            continue
        if _NON_ITEM_RE.match(line):
            continue
        item_match = _ITEM_RE.match(line)
        if not item_match:
            # A bare amount with no description is held: it has no
            # line to tie it to, so it never enters the draft. Date
            # and time lines are not amounts at all.
            if _DATE_RE.search(line):
                continue
            if re.search(r"\d{3,}", line):
                held += 1
            continue
        amount = parse_amount(item_match.group("amount"))
        if amount is None:
            continue
        name = re.sub(r"\s+", " ", item_match.group("name")).strip(" -|*")
        if not name or _NON_ITEM_RE.match(name):
            continue
        items.append(
            ReceiptLine(
                name=name[:120],
                amount=amount,
                evidence_id=evidence_id_for(photo_id, f"line-{index}"),
                line_index=index,
            )
        )
    total_is_sum = False
    if total is None and items:
        total = sum(item.amount for item in items)
        total_is_sum = True
    total_evidence_id = evidence_id_for(photo_id, "total") if total is not None else None
    return ReceiptDraft(
        merchant=merchant,
        expense_date=expense_date,
        currency=currency,
        lines=items,
        total=total,
        total_evidence_id=total_evidence_id,
        total_is_sum=total_is_sum,
        held_amounts=held,
    )
