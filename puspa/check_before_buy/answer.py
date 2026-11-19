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
  ``do_not_act``. A forwarded text matching a known local scam
  pattern (plan 15.5, ``puspa.graphs.fraud_patterns``) also gives
  ``do_not_act``, with the pattern named. Anything else gives
  ``cannot_advise`` with the missing pieces listed, so the user
  knows exactly what is unverified.

``mode="live"`` is refused outright: live price execution is held
under decision 15 (see the package docstring), so a live request
returns a held answer with every section ``cannot_verify`` and no
reader, HTTP call, or marketplace fetch is attempted.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from puspa.graphs.fraud_patterns import match_fraud_patterns
from puspa.graphs.shared import seller_reputation, shared_fair_price
from puspa.local_data.language import extract_courier, extract_destination_city
from puspa.local_data.money import format_rupiah
from puspa.local_data.rails import PaymentRail, detect_rail
from puspa.local_data.rates import (
    RateStore,
    ShippingQuote,
    UnknownCityError,
    UnknownCourierError,
)

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
class PaymentRailSection:
    """The Indonesian payment rail named in the input, if any.

    Naming only: this section says which rail the seller or user
    mentioned (QRIS, virtual account, bank transfer, GoPay, OVO,
    DANA, COD). It never prepares or executes a payment — that is
    ticket B8.
    """

    rail_id: str
    display_name: str
    kind: str
    bank: str = ""
    note: str = ""

    @classmethod
    def from_rail(cls, rail: PaymentRail) -> PaymentRailSection:
        return cls(
            rail_id=rail.rail_id,
            display_name=rail.display_name,
            kind=rail.kind,
            bank=rail.bank,
            note=rail.note,
        )

    def to_dict(self) -> dict:
        return {
            "rail_id": self.rail_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "bank": self.bank,
            "note": self.note,
        }


@dataclass
class ShippingSection:
    """Shipping cost for the lane named in the input, if any.

    The cost comes from the local data layer's seeded sample
    rates (see ``puspa.local_data.rates``): the quote always
    carries its provenance, and a lane or courier with no rate
    on record is ``cannot_verify``, never a guessed number.
    """

    status: str  # "verified" | "cannot_verify" | "not_applicable"
    city: str | None = None
    courier: str | None = None
    cost_idr: int | None = None
    cod_available: bool | None = None
    source_label: str = ""
    reason: str = ""

    @classmethod
    def from_quote(cls, quote: ShippingQuote) -> ShippingSection:
        return cls(
            status=STATUS_VERIFIED,
            city=quote.destination,
            courier=quote.courier,
            cost_idr=quote.cost_idr,
            cod_available=quote.cod_available,
            source_label=quote.source_label,
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "city": self.city,
            "courier": self.courier,
            "cost_idr": self.cost_idr,
            "cod_available": self.cod_available,
            "source_label": self.source_label,
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
    # Shipping quote for the lane named in the input (B5), and
    # the payment rail named in the input (B5, naming only).
    shipping: ShippingSection = field(
        default_factory=lambda: ShippingSection(status="not_applicable")
    )
    payment_rail: PaymentRailSection | None = None
    # Recorded observations the fair price rests on. Kept out of
    # to_dict(); the outcome writer turns them into evidence rows.
    observations: list[PriceObservation] = field(default_factory=list, repr=False)
    # Known local scam patterns (plan 15.5) the forwarded text
    # matches, as dicts (pattern_id / title / matched_trigger /
    # warning). Empty when the text matches none.
    fraud_patterns: list[dict] = field(default_factory=list)

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
            "shipping": self.shipping.to_dict(),
            "payment_rail": self.payment_rail.to_dict() if self.payment_rail else None,
            "fraud_patterns": [dict(entry) for entry in self.fraud_patterns],
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
        shipping=ShippingSection(status=STATUS_CANNOT_VERIFY, reason=LIVE_EXECUTION_HELD_REASON),
    )
    answer.text = render_text(answer)
    return answer


def _shipping_section(text: str, rate_store: RateStore) -> ShippingSection:
    """Shipping quote for the lane named in ``text`` (B5).

    No destination city means the section does not apply. A
    named city with no rate on record (or a courier with no rate
    on that lane) is ``cannot_verify`` — the store's fail-loud
    errors are reported, never swallowed into a guessed number.
    With no courier named, the cheapest courier on the lane is
    quoted and named. The quote is for 1 kg from Jakarta, the
    seed's origin hub; the section carries its provenance.
    """
    city = extract_destination_city(text)
    if city is None:
        return ShippingSection(
            status=STATUS_NOT_APPLICABLE,
            reason="no destination city in the forwarded input",
        )
    courier = extract_courier(text)
    try:
        if courier is not None:
            quote = rate_store.quote(
                origin="Jakarta", destination=city, courier=courier, weight_kg=1.0
            )
        else:
            cheapest = rate_store.cheapest(origin="Jakarta", destination=city)
            quote = rate_store.quote(
                origin="Jakarta", destination=city, courier=cheapest.courier, weight_kg=1.0
            )
    except (UnknownCityError, UnknownCourierError) as exc:
        return ShippingSection(
            status=STATUS_CANNOT_VERIFY, city=city, courier=courier, reason=str(exc)
        )
    return ShippingSection.from_quote(quote)


def check_before_buy(
    text: str,
    *,
    store: RecordStore | None = None,
    product_key: str | None = None,
    mode: str = "recorded",
    shared_dsn: str | None = None,
    rate_store: RateStore | None = None,
) -> CheckAnswer:
    """Answer one forwarded input from recorded data only.

    ``mode="recorded"`` (the default) uses the supplied store.
    ``mode="live"`` returns a held answer and reads nothing.
    ``shared_dsn`` (ticket B3) consults the shared graphs: a
    seller flagged by another user flags the seller here too,
    and when the local store has no price observations at all,
    the shared fair-price estimate is offered as an
    ``uncertain`` hint. Without it the answer is exactly the
    B1 recorded-only answer; the shared flag reason is never
    shown, only the aggregate count.
    ``rate_store`` (ticket B5) supplies the shipping rates for
    the lane named in the input; it defaults to the seeded
    sample rates. The answer also names the payment rail the
    input mentions (naming only, no payment is prepared).
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
    if fair_price.status == STATUS_CANNOT_VERIFY and key and shared_dsn is not None:
        shared = shared_fair_price(shared_dsn, key)
        if shared is not None:
            fair_price = FairPrice(
                status=STATUS_UNCERTAIN,
                value=shared.value,
                sources=list(shared.sources),
                basis=(
                    f"shared fair-price estimate from {shared.observations} "
                    "observations across users (median)"
                ),
                reason="shared estimate only; no recorded observations of your own",
            )

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

    # B3: a seller flagged once in the shared graph is flagged
    # for every other user, even when the local store says
    # nothing (cannot_verify) or clean. The shared flag reason
    # is never shown: only the aggregate flag count reaches
    # this answer, so no flagger is identified.
    if (
        seller.status != STATUS_FLAGGED
        and classified.seller_name is not None
        and shared_dsn is not None
    ):
        reputation = seller_reputation(shared_dsn, classified.seller_name)
        if reputation is not None and reputation.flagged:
            seller = SellerSection(
                status=STATUS_FLAGGED,
                name=classified.seller_name,
                reports=reputation.flag_count,
                note=(
                    f"flagged in the shared graph by {reputation.flag_count} "
                    "user(s); the flagger is not shown"
                ),
                reason="seller is flagged in the shared graph",
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

    # 15.5: a forwarded text matching a known local scam pattern
    # is a do-not-act answer on its own, whatever the other
    # sections say — the pattern is the warning, and the user
    # must see it named.
    fraud_matches = match_fraud_patterns(text)
    if fraud_matches:
        advice = ADVICE_DO_NOT_ACT
        reasons = [
            f"matches a known local scam pattern: {match.title} — {match.warning}"
            for match in fraud_matches
        ]

    # B5: shipping quote for the lane named in the input, and
    # the payment rail the input mentions (naming only).
    shipping = _shipping_section(text, rate_store or RateStore())
    rail = detect_rail(text)
    payment_rail = PaymentRailSection.from_rail(rail) if rail is not None else None
    if shipping.status == STATUS_CANNOT_VERIFY:
        cannot_verify.append("shipping")

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
        fraud_patterns=[match.to_dict() for match in fraud_matches],
        shipping=shipping,
        payment_rail=payment_rail,
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
            f"Fair price: {format_rupiah(round(price.value))} ({price.basis}; "
            f"sources: {', '.join(price.sources)})."
        )
    elif price.status == STATUS_UNCERTAIN and price.value is not None:
        lines.append(
            f"Fair price, not yet verified: {format_rupiah(round(price.value))} "
            f"({price.basis}). Treat it as a hint, not a verdict."
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

    shipping = answer.shipping
    if shipping.status == STATUS_VERIFIED and shipping.cost_idr is not None:
        cod = "COD is available" if shipping.cod_available else "COD is not available"
        lines.append(
            f"Shipping to {shipping.city} via {shipping.courier}: "
            f"{format_rupiah(shipping.cost_idr)} for 1 kg from Jakarta. {cod}. "
            f"({shipping.source_label}; sample rates, not a live tariff.)"
        )
    elif shipping.status == STATUS_CANNOT_VERIFY:
        lines.append(f"Shipping: I could not verify this. {shipping.reason}")

    if answer.payment_rail is not None:
        lines.append(f"Payment: {answer.payment_rail.note}")

    for pattern in answer.fraud_patterns:
        lines.append(
            f"Warning: this matches a known local scam pattern "
            f"({pattern['title']}). {pattern['warning']}"
        )

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
