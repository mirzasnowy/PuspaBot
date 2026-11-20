"""Document-kind detection for receipt photos.

Identity cards (KTP), family cards (KK), and official letters
are out of scope by the owner's decision on identity documents
and bureaucracy: when the text of a photo looks like one of
those, the upload is refused with an explanation, and nothing
is stored. This module only classifies — it never extracts or
keeps identity data.

Detection is deliberately conservative: a single word is never
enough. An identity document needs one strong marker (for
example ``KTP`` next to ``NIK``, or ``KARTU TANDA PENDUDUK``)
or two weaker markers together. A warung receipt that happens
to contain the word "surat" in a shop name is not refused.
"""

from __future__ import annotations

import re

__all__ = [
    "DocumentKind",
    "classify_document",
    "detect_out_of_scope",
]

DocumentKind = str

#: Strong markers: any one of these refuses the photo.
_STRONG_MARKERS = (
    re.compile(r"\bKARTU TANDA PENDUDUK\b", re.IGNORECASE),
    re.compile(r"\bKARTU KELUARGA\b", re.IGNORECASE),
    re.compile(r"\bNIK\s*:?\s*\d{6,}", re.IGNORECASE),
    re.compile(r"\bPASPOR\b|\bPASSPORT\b", re.IGNORECASE),
    re.compile(r"\bSURAT IZIN MENGEMUDI\b", re.IGNORECASE),
    re.compile(r"\bAKTA (KELAHIRAN|PERKAWINAN|KEMATIAN)\b", re.IGNORECASE),
    re.compile(r"\bIJAZAH\b", re.IGNORECASE),
)

#: Weaker markers: two of these together refuse the photo.
_WEAK_MARKERS = (
    re.compile(r"\bKTP\b", re.IGNORECASE),
    re.compile(r"\bKK\b", re.IGNORECASE),
    re.compile(r"\bNIK\b", re.IGNORECASE),
    re.compile(r"\bNAMA LENGKAP\b", re.IGNORECASE),
    re.compile(r"\bTEMPAT[/ ]TGL LAHIR\b", re.IGNORECASE),
    re.compile(r"\bPEMERINTAH REPUBLIK INDONESIA\b", re.IGNORECASE),
    re.compile(r"\bKEPALA KELUARGA\b", re.IGNORECASE),
    re.compile(r"\bSURAT KETERANGAN\b", re.IGNORECASE),
    re.compile(r"\bSURAT RESMI\b", re.IGNORECASE),
    re.compile(r"\bNOMOR INDUK\b", re.IGNORECASE),
)

#: Words that mark a photo as a receipt, note, bill, or menu.
_RECEIPT_WORDS = (
    re.compile(r"\bTOTAL\b|\bGRAND TOTAL\b|\bSUBTOTAL\b", re.IGNORECASE),
    re.compile(r"\bKEMBALIAN\b|\bKEMBALI\b|\bTUNAI\b|\bCASH\b", re.IGNORECASE),
    re.compile(r"\bSTRUK\b|\bNOTA\b|\bKWITANSI\b", re.IGNORECASE),
    re.compile(r"\bTAGIHAN\b|\bBILL\b|\bINVOICE\b", re.IGNORECASE),
    re.compile(r"\bMENU\b|\bHARGA\b", re.IGNORECASE),
    re.compile(r"\bRESI\b|\bRECEIPT\b", re.IGNORECASE),
)


def _strong_hit(text: str) -> str | None:
    for pattern in _STRONG_MARKERS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _weak_hits(text: str) -> list[str]:
    return [pattern.pattern for pattern in _WEAK_MARKERS if pattern.search(text)]


def detect_out_of_scope(text: str) -> tuple[bool, str | None]:
    """Refuse identity and official documents.

    Returns ``(True, reason)`` when the text looks like a KTP,
    KK, passport, driving licence, civil record, diploma, or
    official letter, and ``(False, None)`` otherwise. The reason
    names the marker that refused it, so the user can see why.
    """
    strong = _strong_hit(text)
    if strong is not None:
        return True, (
            "This looks like an identity or official document "
            f"(matched {strong.strip()!r}). Puspa does not process "
            "identity cards, family cards, or official letters; "
            "only receipts, shop notes, bills, delivery slips, and menus."
        )
    weak = _weak_hits(text)
    if len(weak) >= 2:
        return True, (
            "This looks like an identity or official document "
            "(matched two identity markers). Puspa does not process "
            "identity cards, family cards, or official letters; "
            "only receipts, shop notes, bills, delivery slips, and menus."
        )
    return False, None


def classify_document(text: str) -> DocumentKind:
    """Kind of the photo from its text: ``receipt``, ``note``,
    ``bill``, ``menu``, ``id_document``, ``official_letter``, or
    ``unknown``.

    The kind is a hint for the confirmation screen; it never
    decides anything by itself — refusal is decided by
    :func:`detect_out_of_scope`.
    """
    refused, _ = detect_out_of_scope(text)
    if refused:
        if re.search(r"\bSURAT\b", text, re.IGNORECASE) and not re.search(
            r"\bKTP\b|\bNIK\b|\bKARTU\b|\bPASPOR\b", text, re.IGNORECASE
        ):
            return "official_letter"
        return "id_document"
    lowered = text.lower()
    if "menu" in lowered and "total" not in lowered:
        return "menu"
    if any(pattern.search(text) for pattern in _RECEIPT_WORDS):
        if re.search(r"\btagihan\b|\bbill\b|\binvoice\b", text, re.IGNORECASE):
            return "bill"
        if re.search(r"\bnota\b|\bkwitansi\b", text, re.IGNORECASE):
            return "note"
        return "receipt"
    if re.search(r"\bresi\b|\breceipt\b", text, re.IGNORECASE):
        return "receipt"
    return "unknown"
