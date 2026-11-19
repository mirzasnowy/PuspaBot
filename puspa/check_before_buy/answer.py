"""One structured, evidence-backed answer for Check Before You Buy.

Evidence rules (the slice never guesses):

- Fair price: the median of recorded price observations for the
  product, one per source. ``verified`` needs observations from at
  least two distinct sources; one source is ``uncertain``; none is
  ``cannot_verify`` with no number at all.
- Seller track record: only what the recorded store says about the
  named seller. No record, or no seller name, is ``cannot_verify``.
- Account status: only what the recorded store says about the exact
  account number. An unknown number is ``cannot_verify``, never
  "looks safe".
- Advice: ``act`` only when every check that applies is green
  (fair price verified, seller record clean, account record clean
  when an account is involved). Any flagged seller or account gives
  ``do_not_act``. Anything else gives ``cannot_advise`` with the
  missing pieces listed, so the user knows exactly what is unverified.

``mode="live"`` is refused outright: live price execution is held
under decision 15 (see the package docstring), so a live request
returns a held answer with every section ``cannot_verify`` and no
reader, HTTP call, or marketplace fetch is attempted.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .classifier import classify_input
from .records import PriceObservation, RecordStore, normalize_product_key

LIVE_EXECUTION_HELD_REASON = (
    "live price execution is held under decision 15: Tokopedia, Shopee, and "
    "Blibli forbid automated reading in their terms, so no live price was "
    "fetched. This answer uses recorded data only."
)

ADVICE_ACT = "act"
ADVICE_DO_NOT_ACT = "do_not_act"
ADVICE_CANNOT_ADVISE = "cannot_advise"

STATUS_VERIFIED = "verified"
STATUS_UNCERTAIN = "uncertain"
STATUS_CANNOT_VERIFY = "cannot_verify"
STATUS_RECORDED_CLEAN = "recorded_clean"
STATUS_FLAGGED = "flagged"
STATUS_NOT_APPLICABLE = "not_applicable"


@dataclass
class FairPrice:
    status: str
    value: float | None = None
    currency: str = "IDR"
    sources: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    basis: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "value": self.value,
            "currency": self.currency,
            "sources": list(self.sources),
            "evidence_ids": list(self.evidence_ids),
            "basis": self.basis,
            "reason": self.reason,
        }


@dataclass
class SellerSection:
    status: str
    name: str | None = None
    reports: int = 0
    note: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "name": self.name,
            "reports": self.reports,
            "note": self.note,
            "reason": self.reason,
        }


@dataclass
class AccountSection:
    status: str
    masked_number: str | None = None
    bank: str = ""
    note: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "masked_number": self.masked_number,
            "bank": self.bank,
            "note": self.note,
            "reason": self.reason,
        }


@dataclass
class CheckAnswer:
    """One complete answer to one forwarded input."""

    input_kind: str
    product_key: str | None
    product_url: str | None
    fair_price: FairPrice
    seller: SellerSection
    account: AccountSection
    advice: str
    advice_reasons: list[str] = field(default_factory=list)
    cannot_verify: list[str] = field(default_factory=list)
    live_execution_held: bool = False
    text: str = ""
    # Recorded observations the fair price rests on. Kept out of
    # to_dict(); the outcome writer turns them into evidence rows.
    observations: list[PriceObservation] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        return {
            "input_kind": self.input_kind,
            "product_key": self.product_key,
            "product_url": self.product_url,
            "fair_price": self.fair_price.to_dict(),
            "seller": self.seller.to_dict(),
            "account": self.account.to_dict(),
            "advice": self.advice,
            "advice_reasons": list(self.advice_reasons),
            "cannot_verify": list(self.cannot_verify),
            "live_execution_held": self.live_execution_held,
            "text": self.text,
        }


def mask_account_number(number: str) -> str:
    """Show only the first two and last two digits of an account number."""
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{digits[:2]}{'*' * (len(digits) - 4)}{digits[-2:]}"


def _fair_price(observations: list[PriceObservation], product_key: str | None) -> FairPrice:
    if not product_key or not observations:
        return FairPrice(
            status=STATUS_CANNOT_VERIFY,
            reason="no recorded price observations for this product",
        )
    sources = sorted({obs.source for obs in observations})
    median = statistics.median([obs.price for obs in observations])
    if len(sources) >= 2:
        return FairPrice(
            status=STATUS_VERIFIED,
            value=float(median),
            sources=sources,
            basis=(
                f"median of {len(observations)} recorded observations from {len(sources)} sources"
            ),
        )
    return FairPrice(
        status=STATUS_UNCERTAIN,
        value=float(median),
        sources=sources,
        basis="single recorded observation from one source",
        reason="only one source on record; a second source is needed to verify",
    )


def _held_answer(input_kind: str) -> CheckAnswer:
    answer = CheckAnswer(
        input_kind=input_kind,
        product_key=None,
        product_url=None,
        fair_price=FairPrice(status=STATUS_CANNOT_VERIFY, reason=LIVE_EXECUTION_HELD_REASON),
        seller=SellerSection(status=STATUS_CANNOT_VERIFY, reason=LIVE_EXECUTION_HELD_REASON),
        account=AccountSection(status=STATUS_CANNOT_VERIFY, reason=LIVE_EXECUTION_HELD_REASON),
        advice=ADVICE_CANNOT_ADVISE,
        advice_reasons=[LIVE_EXECUTION_HELD_REASON],
        cannot_verify=["fair_price", "seller", "account"],
        live_execution_held=True,
    )
    answer.text = render_text(answer)
    return answer


def check_before_buy(
    text: str,
    *,
    store: RecordStore | None = None,
    product_key: str | None = None,
    mode: str = "recorded",
) -> CheckAnswer:
    """Answer one forwarded input from recorded data only.

    ``mode="recorded"`` (the default) uses the supplied store.
    ``mode="live"`` returns a held answer and reads nothing.
    """
    classified = classify_input(text)
    if mode == "live":
        return _held_answer(classified.kind)

    store = store or RecordStore()
    key = normalize_product_key(product_key) if product_key else None
    if key is None and classified.product_url:
        key = normalize_product_key(classified.product_url)

    observations = store.price_observations(key) if key else []
    fair_price = _fair_price(observations, key)

    seller_record = store.seller_record(classified.seller_name)
    if classified.seller_name is None:
        seller = SellerSection(
            status=STATUS_CANNOT_VERIFY,
            reason="no seller name in the forwarded input",
        )
    elif seller_record is None:
        seller = SellerSection(
            status=STATUS_CANNOT_VERIFY,
            name=classified.seller_name,
            reason="no recorded track record for this seller",
        )
    elif seller_record.flagged:
        seller = SellerSection(
            status=STATUS_FLAGGED,
            name=classified.seller_name,
            reports=seller_record.reports,
            note=seller_record.flag_reason or seller_record.note,
            reason=seller_record.flag_reason or "seller is flagged on record",
        )
    else:
        seller = SellerSection(
            status=STATUS_RECORDED_CLEAN,
            name=classified.seller_name,
            reports=seller_record.reports,
            note=seller_record.note or "no adverse reports on record",
        )

    if classified.account_number is None:
        account = AccountSection(
            status=STATUS_NOT_APPLICABLE,
            reason="no account number in the forwarded input",
        )
    else:
        account_record = store.account_record(classified.account_number)
        masked = mask_account_number(classified.account_number)
        if account_record is None:
            account = AccountSection(
                status=STATUS_CANNOT_VERIFY,
                masked_number=masked,
                reason="no recorded status for this account number",
            )
        elif account_record.flagged:
            account = AccountSection(
                status=STATUS_FLAGGED,
                masked_number=masked,
                bank=account_record.bank,
                note=account_record.flag_reason or account_record.status_note,
                reason=account_record.flag_reason or "account is flagged on record",
            )
        else:
            account = AccountSection(
                status=STATUS_RECORDED_CLEAN,
                masked_number=masked,
                bank=account_record.bank,
                note=account_record.status_note or "no adverse reports on record",
            )

    cannot_verify = [
        name
        for name, section in (
            ("fair_price", fair_price),
            ("seller", seller),
            ("account", account),
        )
        if section.status == STATUS_CANNOT_VERIFY
    ]

    advice, reasons = _decide_advice(classified.kind, fair_price, seller, account)

    answer = CheckAnswer(
        input_kind=classified.kind,
        product_key=key,
        product_url=classified.product_url,
        fair_price=fair_price,
        seller=seller,
        account=account,
        advice=advice,
        advice_reasons=reasons,
        cannot_verify=cannot_verify,
        observations=observations,
    )
    answer.text = render_text(answer)
    return answer


def _decide_advice(
    kind: str, fair_price: FairPrice, seller: SellerSection, account: AccountSection
) -> tuple[str, list[str]]:
    """Act / do-not-act / cannot-advise from the three sections."""
    if seller.status == STATUS_FLAGGED:
        return ADVICE_DO_NOT_ACT, [
            f"seller {seller.name or ''} is flagged on record: {seller.reason}".strip()
        ]
    if account.status == STATUS_FLAGGED:
        return ADVICE_DO_NOT_ACT, [
            f"account {account.masked_number} is flagged on record: {account.reason}".strip()
        ]

    if kind == "account_number":
        if account.status == STATUS_RECORDED_CLEAN:
            return ADVICE_ACT, ["account has a clean recorded status"]
        return ADVICE_CANNOT_ADVISE, ["account status could not be verified"]

    if kind == "unknown":
        return ADVICE_CANNOT_ADVISE, [
            "could not tell what was forwarded: send a product link, a seller chat, or an account number"
        ]

    reasons: list[str] = []
    if fair_price.status != STATUS_VERIFIED:
        reasons.append("fair price could not be verified from recorded data")
    if seller.status != STATUS_RECORDED_CLEAN:
        reasons.append("seller track record could not be verified from recorded data")
    if account.status not in (STATUS_NOT_APPLICABLE, STATUS_RECORDED_CLEAN):
        reasons.append("account status could not be verified from recorded data")
    if reasons:
        return ADVICE_CANNOT_ADVISE, reasons
    return ADVICE_ACT, [
        "fair price is verified from recorded data and the seller has a clean recorded track record"
    ]


def render_text(answer: CheckAnswer) -> str:
    """One-turn reply in plain language, with gaps stated, not hidden."""
    lines = ["Here's what I could check before you buy:"]
    price = answer.fair_price
    if price.status == STATUS_VERIFIED and price.value is not None:
        lines.append(
            f"Fair price: Rp{price.value:,.0f} ({price.basis}; "
            f"sources: {', '.join(price.sources)})."
        )
    elif price.status == STATUS_UNCERTAIN and price.value is not None:
        lines.append(
            f"Fair price, not yet verified: Rp{price.value:,.0f} ({price.basis}). "
            "Treat it as a hint, not a verdict."
        )
    else:
        lines.append(f"Fair price: I could not verify this. {price.reason}")

    seller = answer.seller
    if seller.status == STATUS_FLAGGED:
        lines.append(f"Seller: flagged on record. {seller.note}")
    elif seller.status == STATUS_RECORDED_CLEAN:
        lines.append(f"Seller {seller.name}: clean on record ({seller.reports} reports).")
    else:
        lines.append(f"Seller: I could not verify this. {seller.reason}")

    account = answer.account
    if account.status == STATUS_FLAGGED:
        lines.append(f"Account {account.masked_number}: flagged on record. {account.note}")
    elif account.status == STATUS_RECORDED_CLEAN:
        lines.append(f"Account {account.masked_number}: clean on record.")
    elif account.status == STATUS_CANNOT_VERIFY:
        lines.append(f"Account {account.masked_number}: I could not verify this. {account.reason}")

    if answer.advice == ADVICE_ACT:
        lines.append("Advice: you can go ahead. " + "; ".join(answer.advice_reasons) + ".")
    elif answer.advice == ADVICE_DO_NOT_ACT:
        lines.append("Advice: do not go ahead. " + "; ".join(answer.advice_reasons) + ".")
    else:
        lines.append(
            "Advice: I cannot give a go-ahead yet. " + "; ".join(answer.advice_reasons) + "."
        )
    lines.append("I did not send any message or money for this check.")
    return "\n".join(lines)
