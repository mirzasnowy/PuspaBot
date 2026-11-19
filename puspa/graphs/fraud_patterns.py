"""Local fraud patterns (plan 15.5) as data for the anti-fraud graph.

Checklist 15.5 names the scam patterns Indonesian users
actually meet: a courier package with an APK file, bank or
OJK impersonation, "mama minta pulsa" and "new number",
part-time work paying for likes or subscribes, illegal
investment and lending, "wrong transfer, please return it",
mule accounts, fake COD, account trading, and online slots.
This module is the vocabulary for those patterns: each one
carries its Indonesian trigger phrases (data, matched
case-insensitively) and one plain-language warning.

The patterns are data, not a verdict: :func:`match_fraud_patterns`
reports which patterns the text matches, and the caller
decides what to do with the match. Ticket B1 consumes it:
a forwarded seller chat matching a pattern warns the user
and advises not to go ahead. The shared graph (B3) keeps
free-text flag reasons; this table is what lets a caller
classify a reason or a forwarded text against the known
patterns instead of matching free text by hand.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FraudPattern:
    """One known local scam pattern (plan 15.5)."""

    pattern_id: str
    title: str
    triggers: tuple[str, ...]
    warning: str


#: The 15.5 pattern table. Triggers are Indonesian phrases as
#: users actually write them; matching is substring, lowercased.
FRAUD_PATTERNS: tuple[FraudPattern, ...] = (
    FraudPattern(
        pattern_id="courier_apk",
        title="courier package with an APK file",
        triggers=(
            "apk",
            "paket kurir",
            "unduh apk",
            "download apk",
            "file apk",
            "cek resi apk",
            "instal apk",
            "install apk",
        ),
        warning=(
            "Never install a file from a delivery link. A real courier "
            "never asks you to install an app to track a parcel; check "
            "the parcel only in the courier's own app."
        ),
    ),
    FraudPattern(
        pattern_id="bank_ojk_impersonation",
        title="bank or OJK impersonation",
        triggers=(
            "ojk",
            "otoritas jasa keuangan",
            "pihak bank",
            "call center bank",
            "rekening diblokir",
            "akun diblokir",
            "verifikasi data bank",
            "rekening anda diblokir",
            "rekening kamu diblokir",
        ),
        warning=(
            "A real bank or OJK never asks for your PIN, OTP, or password "
            "by chat or phone. Hang up and call the bank's official number "
            "yourself."
        ),
    ),
    FraudPattern(
        pattern_id="mama_minta_pulsa",
        title="'mama minta pulsa' family impersonation",
        triggers=(
            "mama minta pulsa",
            "papa minta pulsa",
            "minta pulsa",
            "mama minta transfer",
            "ini mama",
            "ini papa",
            "tolong isikan pulsa",
        ),
        warning=(
            "A message claiming to be a parent asking for credit or a "
            "transfer is a classic impersonation. Call the family member "
            "on their known number before sending anything."
        ),
    ),
    FraudPattern(
        pattern_id="new_number",
        title="'new number' impersonation",
        triggers=(
            "ganti nomor",
            "nomor baru",
            "no baru",
            "nomor hp baru",
            "save nomor baru",
            "ini nomor baru",
            "aku ganti nomor",
        ),
        warning=(
            "A contact suddenly writing from a new number and asking for "
            "money is a classic impersonation. Verify on the old number "
            "or in person first."
        ),
    ),
    FraudPattern(
        pattern_id="part_time_like_subscribe",
        title="part-time work paying for likes or subscribes",
        triggers=(
            "tugas like",
            "like dan subscribe",
            "like subscribe dapat uang",
            "kerja paruh waktu",
            "tugas harian dapat uang",
            "like youtube dapat uang",
            "subscribe dapat uang",
        ),
        warning=(
            "Jobs that pay you for likes or subscribes and then ask for a "
            "deposit or 'activation fee' are a classic scam. Real work "
            "never asks you to pay to start."
        ),
    ),
    FraudPattern(
        pattern_id="illegal_investment_loan",
        title="illegal investment or lending",
        triggers=(
            "pinjol",
            "pinjaman online ilegal",
            "investasi return harian",
            "return harian",
            "cuan cepat",
            "dana cepat cair",
            "investasi pasti untung",
        ),
        warning=(
            "Offers of guaranteed daily returns or instant loans with no "
            "checks are illegal lending or investment fraud. Check that a "
            "lender or investment is registered with OJK before any money "
            "moves."
        ),
    ),
    FraudPattern(
        pattern_id="wrong_transfer_refund",
        title="'wrong transfer, please return it'",
        triggers=(
            "salah transfer",
            "tolong kembalikan",
            "salah kirim uang",
            "uang nyasar",
            "salah tf",
            "kembalikan uang saya",
        ),
        warning=(
            "A stranger claiming they sent money to you by mistake and "
            "asking you to return it is a classic scam. Do not send money "
            "back; let the bank handle a real mistaken transfer."
        ),
    ),
    FraudPattern(
        pattern_id="mule_account",
        title="mule account",
        triggers=(
            "rekening penampung",
            "sewa rekening",
            "pinjam rekening",
            "jual rekening",
            "rekening untuk terima uang",
        ),
        warning=(
            "Renting out or lending your bank account makes you part of "
            "the fraud. Never let anyone else use your account."
        ),
    ),
    FraudPattern(
        pattern_id="fake_cod",
        title="fake COD delivery",
        triggers=(
            "cod fiktif",
            "paket cod tidak dipesan",
            "barang cod tidak ada",
            "cod tidak dipesan",
        ),
        warning=(
            "A cash-on-delivery parcel you never ordered is a classic "
            "scam. Refuse a COD parcel you did not order."
        ),
    ),
    FraudPattern(
        pattern_id="account_trading",
        title="buying and selling accounts",
        triggers=(
            "jual beli akun",
            "jual akun",
            "beli akun",
            "jual akun game",
            "jual akun wa",
        ),
        warning=(
            "Buying or selling accounts is a common route to fraud and "
            "account theft. Do not buy an account from a stranger."
        ),
    ),
    FraudPattern(
        pattern_id="online_gambling",
        title="online slots and gambling",
        triggers=(
            "judi online",
            "judol",
            "slot gacor",
            "deposit slot",
            "situs slot",
            "main slot",
        ),
        warning=(
            "Online slots and gambling sites are illegal and designed to "
            "take your money. Puspa does not help with gambling."
        ),
    ),
)


@dataclass(frozen=True)
class FraudPatternMatch:
    """One pattern matched in a text, with the trigger that fired."""

    pattern_id: str
    title: str
    matched_trigger: str
    warning: str

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "title": self.title,
            "matched_trigger": self.matched_trigger,
            "warning": self.warning,
        }


def match_fraud_patterns(text: str) -> list[FraudPatternMatch]:
    """Patterns from the 15.5 table that ``text`` matches.

    One match per pattern (the first trigger that fires), in
    table order. No match means the text names no known
    pattern — not that the text is safe; unknown scams exist,
    and this table only knows the listed ones.
    """
    lowered = (text or "").lower()
    matches: list[FraudPatternMatch] = []
    for pattern in FRAUD_PATTERNS:
        for trigger in pattern.triggers:
            if trigger in lowered:
                matches.append(
                    FraudPatternMatch(
                        pattern_id=pattern.pattern_id,
                        title=pattern.title,
                        matched_trigger=trigger,
                        warning=pattern.warning,
                    )
                )
                break
    return matches


def classify_flag_reason(reason: str) -> list[str]:
    """Pattern ids a free-text flag reason matches, if any.

    Lets a caller tag a B3 flag reason against the known
    patterns instead of matching free text by hand.
    """
    return [match.pattern_id for match in match_fraud_patterns(reason)]
