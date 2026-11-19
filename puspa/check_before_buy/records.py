"""Recorded data for the Check Before You Buy slice.

No live reads happen here. A :class:`RecordStore` holds observations
that were recorded earlier (test fixtures, a recorded cassette file,
or a store seeded from an approved source) and answers lookups
against them. When a lookup finds nothing, the caller reports
``cannot_verify``; nothing in this module ever invents a value.

A cassette is a plain JSON file::

    {
      "price_observations": [
        {"product_key": "...", "source": "Tokopedia",
         "price": 12499000, "source_url": "https://...",
         "excerpt": "[DATA: ...]"}
      ],
      "seller_records": [
        {"name": "Gadget Store", "flagged": false, "reports": 12,
         "note": "no adverse reports on record",
         "source_label": "recorded seller reports"}
      ],
      "account_records": [
        {"number": "1234567890", "flagged": false, "bank": "BCA",
         "status_note": "no adverse reports on record"}
      ]
    }

``product_key`` is the identity of the product being checked. For a
forwarded link it defaults to the normalised listing URL, so
observations recorded for that exact listing are the ones used.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PriceObservation:
    """One recorded price for one product at one source."""

    product_key: str
    source: str
    price: float
    source_url: str
    excerpt: str = ""
    observed_at: str = ""


@dataclass(frozen=True)
class SellerRecord:
    """One recorded track record for one seller name."""

    name: str
    flagged: bool = False
    flag_reason: str = ""
    reports: int = 0
    note: str = ""
    source_label: str = "recorded seller reports"


@dataclass(frozen=True)
class AccountRecord:
    """One recorded status for one bank account number."""

    number: str  # digits only
    flagged: bool = False
    flag_reason: str = ""
    bank: str = ""
    status_note: str = ""


def normalize_product_key(raw: str) -> str:
    """Normalise a product key: lowercased, no trailing slash, no query."""
    key = (raw or "").strip().lower()
    return re.split(r"[?#]", key, maxsplit=1)[0].rstrip("/")


def normalize_seller_name(name: str) -> str:
    """Seller lookup key: lowercased, collapsed whitespace."""
    return " ".join((name or "").lower().split())


class RecordStore:
    """In-memory recorded data. No network, no live reads."""

    def __init__(self) -> None:
        self._prices: dict[str, list[PriceObservation]] = {}
        self._sellers: dict[str, SellerRecord] = {}
        self._accounts: dict[str, AccountRecord] = {}

    def add_price_observation(self, observation: PriceObservation) -> None:
        key = normalize_product_key(observation.product_key)
        self._prices.setdefault(key, []).append(observation)

    def price_observations(self, product_key: str) -> list[PriceObservation]:
        return list(self._prices.get(normalize_product_key(product_key), []))

    def add_seller_record(self, record: SellerRecord) -> None:
        self._sellers[normalize_seller_name(record.name)] = record

    def seller_record(self, name: str | None) -> SellerRecord | None:
        if not name:
            return None
        return self._sellers.get(normalize_seller_name(name))

    def add_account_record(self, record: AccountRecord) -> None:
        digits = re.sub(r"\D", "", record.number)
        self._accounts[digits] = record

    def account_record(self, number: str | None) -> AccountRecord | None:
        if not number:
            return None
        return self._accounts.get(re.sub(r"\D", "", number))


def load_cassette(path: str | Path) -> RecordStore:
    """Load a recorded-data cassette (JSON) into a fresh store."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    store = RecordStore()
    for item in data.get("price_observations", []):
        store.add_price_observation(
            PriceObservation(
                product_key=str(item["product_key"]),
                source=str(item["source"]),
                price=float(item["price"]),
                source_url=str(item["source_url"]),
                excerpt=str(item.get("excerpt", "")),
                observed_at=str(item.get("observed_at", "")),
            )
        )
    for item in data.get("seller_records", []):
        store.add_seller_record(
            SellerRecord(
                name=str(item["name"]),
                flagged=bool(item.get("flagged", False)),
                flag_reason=str(item.get("flag_reason", "")),
                reports=int(item.get("reports", 0)),
                note=str(item.get("note", "")),
                source_label=str(item.get("source_label", "recorded seller reports")),
            )
        )
    for item in data.get("account_records", []):
        store.add_account_record(
            AccountRecord(
                number=str(item["number"]),
                flagged=bool(item.get("flagged", False)),
                flag_reason=str(item.get("flag_reason", "")),
                bank=str(item.get("bank", "")),
                status_note=str(item.get("status_note", "")),
            )
        )
    return store


def empty_store() -> RecordStore:
    """A store with no records: every lookup answers cannot-verify."""
    return RecordStore()
