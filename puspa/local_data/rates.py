"""Seeded shipping rates by city and courier (ticket B5).

Honesty first: the rates in this module are SEEDED SAMPLE data.
Each row carries its provenance (origin + destination + courier +
rate + COD flag + source label + seed date) and every quote says
so. They are computed deterministically from the seed — the same
lookup always gives the same number — but they are NOT live
courier tariffs, and no code in this package may present them as
such. A real courier tariff changes daily; the seed is an
illustrative stand-in until the refresh seam below is wired to
real data.

The refresh seam: :class:`RateLoader` is the interface a real
data source implements. :class:`SeedRateLoader` serves the seed
in this file; :class:`JsonRateLoader` serves the same rows from a
JSON file, so a refreshed rate file can replace the seed without
touching any caller. :class:`RateStore` takes any loader.

Fail-loud rule: an unknown city or courier raises
:class:`UnknownCityError` / :class:`UnknownCourierError` instead
of returning a guessed rate or a silent default. The B1 answer
path catches these and reports ``cannot_verify`` for the shipping
section; the store itself never guesses.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

#: Provenance label stamped on every seeded rate row and every
#: quote built from one. Any surface that shows a rate must show
#: this (or a sentence carrying it) next to the number.
SEED_SOURCE_LABEL = "seeded sample rate (B5)"
#: Date the seed values were written down. Not a tariff date: the
#: seed is illustrative, and this date says when the illustration
#: was made, not when any courier charged these prices.
SEED_DATE = "2026-11-19"
#: The seed's origin hub. All seeded rates are from Jakarta; a
#: quote from any other origin needs a loader with real data.
SEED_ORIGIN = "Jakarta"


class UnknownCityError(ValueError):
    """A city name no rate data covers."""


class UnknownCourierError(ValueError):
    """A courier name no rate data covers."""


@dataclass(frozen=True)
class ShippingRate:
    """One seeded rate row: one lane, one courier, one price per kg."""

    origin: str
    destination: str
    courier: str
    rate_per_kg_idr: int
    cod_available: bool
    source_label: str = SEED_SOURCE_LABEL
    seeded_at: str = SEED_DATE


@dataclass(frozen=True)
class ShippingQuote:
    """A deterministic quote for one shipment, from seeded rates."""

    origin: str
    destination: str
    courier: str
    weight_kg: float
    billed_kg: int
    cost_idr: int
    cod_available: bool
    source_label: str
    seeded_at: str

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "courier": self.courier,
            "weight_kg": self.weight_kg,
            "billed_kg": self.billed_kg,
            "cost_idr": self.cost_idr,
            "cod_available": self.cod_available,
            "source_label": self.source_label,
            "seeded_at": self.seeded_at,
        }


class RateLoader(Protocol):
    """Refresh seam: anything that can supply rate rows.

    A future real-courier loader implements this same method and
    replaces the seed without touching :class:`RateStore` or any
    caller. It must return complete rows (every field filled);
    the store validates them and fails loudly on a bad row.
    """

    def load(self) -> list[ShippingRate]: ...


#: Canonical city names the seed covers, with the everyday
#: spellings and abbreviations users actually type.
CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Jakarta": (
        "jakarta",
        "jkt",
        "jakarta pusat",
        "jakarta selatan",
        "jakarta barat",
        "jakarta timur",
        "jakarta utara",
        "jakpus",
        "jaksel",
        "jakbar",
        "jaktim",
        "jakut",
        "dki jakarta",
    ),
    "Bandung": ("bandung", "bdg", "kota bandung"),
    "Surabaya": ("surabaya", "sby", "kota surabaya"),
    "Yogyakarta": ("yogyakarta", "jogja", "jogjakarta", "yogya", "diy"),
    "Semarang": ("semarang", "smg", "kota semarang"),
    "Medan": ("medan", "mdn", "kota medan"),
    "Makassar": ("makassar", "mks", "ujung pandang"),
    "Denpasar": ("denpasar", "dps", "bali"),
    "Tangerang": ("tangerang", "tgr", "kota tangerang", "tangerang selatan", "tangsel"),
    "Bekasi": ("bekasi", "bks", "kota bekasi"),
}

#: Canonical courier names the seed covers, with the spellings
#: users actually type.
COURIER_ALIASES: dict[str, tuple[str, ...]] = {
    "JNE": ("jne", "j n e"),
    "J&T": ("j&t", "jnt", "j and t", "jnt express"),
    "SiCepat": ("sicepat", "si cepat", "sicepat ekspres"),
    "AnterAja": ("anteraja", "anter aja"),
    "POS Indonesia": ("pos", "pos indonesia", "pos kilat"),
    "Ninja Xpress": ("ninja", "ninja xpress", "ninja express"),
    "GoSend": ("gosend", "go send", "gojek"),
    "GrabExpress": ("grabexpress", "grab express", "grab"),
}

#: Seeded per-kg rates in Rupiah from Jakarta, with COD flags.
#: Illustrative sample values, NOT live tariffs. GoSend and
#: GrabExpress are same-city instant couriers: they only serve
#: Jakarta in this seed and never offer COD. POS Indonesia does
#: not offer COD in this seed. AnterAja and Ninja Xpress do not
#: offer COD to Medan or Makassar in this seed.
_SEED_RATES: tuple[tuple[str, str, int, bool], ...] = (
    # (destination, courier, rate_per_kg_idr, cod_available)
    ("Jakarta", "JNE", 9000, True),
    ("Jakarta", "J&T", 10000, True),
    ("Jakarta", "SiCepat", 9000, True),
    ("Jakarta", "AnterAja", 9000, True),
    ("Jakarta", "POS Indonesia", 8000, False),
    ("Jakarta", "Ninja Xpress", 10000, True),
    ("Jakarta", "GoSend", 15000, False),
    ("Jakarta", "GrabExpress", 15000, False),
    ("Bandung", "JNE", 12000, True),
    ("Bandung", "J&T", 12000, True),
    ("Bandung", "SiCepat", 11000, True),
    ("Bandung", "AnterAja", 11000, True),
    ("Bandung", "POS Indonesia", 10000, False),
    ("Bandung", "Ninja Xpress", 12000, True),
    ("Surabaya", "JNE", 20000, True),
    ("Surabaya", "J&T", 19000, True),
    ("Surabaya", "SiCepat", 18000, True),
    ("Surabaya", "AnterAja", 18000, True),
    ("Surabaya", "POS Indonesia", 15000, False),
    ("Surabaya", "Ninja Xpress", 19000, True),
    ("Yogyakarta", "JNE", 16000, True),
    ("Yogyakarta", "J&T", 15000, True),
    ("Yogyakarta", "SiCepat", 15000, True),
    ("Yogyakarta", "AnterAja", 14000, True),
    ("Yogyakarta", "POS Indonesia", 13000, False),
    ("Yogyakarta", "Ninja Xpress", 15000, True),
    ("Semarang", "JNE", 15000, True),
    ("Semarang", "J&T", 15000, True),
    ("Semarang", "SiCepat", 14000, True),
    ("Semarang", "AnterAja", 14000, True),
    ("Semarang", "POS Indonesia", 12000, False),
    ("Semarang", "Ninja Xpress", 15000, True),
    ("Medan", "JNE", 30000, True),
    ("Medan", "J&T", 29000, True),
    ("Medan", "SiCepat", 28000, True),
    ("Medan", "AnterAja", 28000, False),
    ("Medan", "POS Indonesia", 25000, False),
    ("Medan", "Ninja Xpress", 29000, False),
    ("Makassar", "JNE", 35000, True),
    ("Makassar", "J&T", 34000, True),
    ("Makassar", "SiCepat", 33000, True),
    ("Makassar", "AnterAja", 33000, False),
    ("Makassar", "POS Indonesia", 30000, False),
    ("Makassar", "Ninja Xpress", 34000, False),
    ("Denpasar", "JNE", 25000, True),
    ("Denpasar", "J&T", 24000, True),
    ("Denpasar", "SiCepat", 23000, True),
    ("Denpasar", "AnterAja", 23000, True),
    ("Denpasar", "POS Indonesia", 20000, False),
    ("Denpasar", "Ninja Xpress", 24000, True),
    ("Tangerang", "JNE", 10000, True),
    ("Tangerang", "J&T", 10000, True),
    ("Tangerang", "SiCepat", 10000, True),
    ("Tangerang", "AnterAja", 10000, True),
    ("Tangerang", "POS Indonesia", 9000, False),
    ("Tangerang", "Ninja Xpress", 10000, True),
    ("Bekasi", "JNE", 10000, True),
    ("Bekasi", "J&T", 10000, True),
    ("Bekasi", "SiCepat", 10000, True),
    ("Bekasi", "AnterAja", 10000, True),
    ("Bekasi", "POS Indonesia", 9000, False),
    ("Bekasi", "Ninja Xpress", 10000, True),
)


def _alias_index(aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Every known spelling -> its canonical name."""
    index: dict[str, str] = {}
    for canonical, spellings in aliases.items():
        index[canonical.lower()] = canonical
        for spelling in spellings:
            index[spelling.lower()] = canonical
    return index


_CITY_INDEX = _alias_index(CITY_ALIASES)
_COURIER_INDEX = _alias_index(COURIER_ALIASES)


def normalize_city(raw: str) -> str:
    """Canonical city name for a spelling, or raise UnknownCityError."""
    key = " ".join((raw or "").lower().split())
    canonical = _CITY_INDEX.get(key)
    if canonical is None:
        raise UnknownCityError(f"unknown city {raw!r}: no shipping rates on record for it")
    return canonical


def normalize_courier(raw: str) -> str:
    """Canonical courier name for a spelling, or raise UnknownCourierError."""
    key = " ".join((raw or "").lower().split())
    canonical = _COURIER_INDEX.get(key)
    if canonical is None:
        raise UnknownCourierError(f"unknown courier {raw!r}: no shipping rates on record for it")
    return canonical


def known_cities() -> tuple[str, ...]:
    """Canonical city names the seed covers."""
    return tuple(CITY_ALIASES)


def known_couriers() -> tuple[str, ...]:
    """Canonical courier names the seed covers."""
    return tuple(COURIER_ALIASES)


class SeedRateLoader:
    """The seeded sample rates in this module, as a loader."""

    def load(self) -> list[ShippingRate]:
        return [
            ShippingRate(
                origin=SEED_ORIGIN,
                destination=destination,
                courier=courier,
                rate_per_kg_idr=rate,
                cod_available=cod,
            )
            for destination, courier, rate, cod in _SEED_RATES
        ]


class JsonRateLoader:
    """Rate rows from a JSON file — the refresh seam.

    The file holds ``{"rates": [...]}`` with one object per row::

        {"origin": "Jakarta", "destination": "Bandung",
         "courier": "JNE", "rate_per_kg_idr": 12000,
         "cod_available": true, "source_label": "courier tariff 2026-11",
         "seeded_at": "2026-11-19"}

    ``source_label`` and ``seeded_at`` are optional per row and
    default to the seed labels. A row with a missing or malformed
    field raises ``ValueError`` at load time: a half-loaded rate
    table must never serve quotes. This is how real courier data
    replaces the seed later — drop in a refreshed file, no code
    change, and every quote then carries the file's provenance
    instead of the seed label.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[ShippingRate]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load rate file {self.path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("rates"), list):
            raise ValueError(f"rate file {self.path} must hold an object with a 'rates' list")
        rates: list[ShippingRate] = []
        for index, item in enumerate(data["rates"]):
            if not isinstance(item, dict):
                raise ValueError(f"rate file {self.path} row {index} is not an object")
            try:
                origin = str(item["origin"]).strip()
                destination = str(item["destination"]).strip()
                courier = str(item["courier"]).strip()
                rate = int(item["rate_per_kg_idr"])
                cod = bool(item["cod_available"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"rate file {self.path} row {index} is missing or has a bad field: {exc}"
                ) from exc
            if not origin or not destination or not courier or rate <= 0:
                raise ValueError(
                    f"rate file {self.path} row {index} has an empty name or a bad rate"
                )
            rates.append(
                ShippingRate(
                    origin=origin,
                    destination=destination,
                    courier=courier,
                    rate_per_kg_idr=rate,
                    cod_available=cod,
                    source_label=str(item.get("source_label", SEED_SOURCE_LABEL)),
                    seeded_at=str(item.get("seeded_at", SEED_DATE)),
                )
            )
        return rates


class RateStore:
    """Deterministic shipping quotes from a rate loader.

    The default loader is the seed. Pass another loader (a JSON
    file, later a real courier feed) to serve different rates
    through the same lookups. Lookups are exact: origin +
    destination + courier must all match a row, and an unknown
    city or courier raises instead of guessing.
    """

    def __init__(self, loader: RateLoader | None = None) -> None:
        self._rates: dict[tuple[str, str, str], ShippingRate] = {}
        for rate in (loader or SeedRateLoader()).load():
            key = (rate.origin, rate.destination, rate.courier)
            if key in self._rates:
                raise ValueError(f"duplicate rate row for {key}")
            self._rates[key] = rate

    def quote(
        self,
        *,
        origin: str,
        destination: str,
        courier: str,
        weight_kg: float = 1.0,
    ) -> ShippingQuote:
        """Quote one shipment. Weight bills per started kilogram.

        Raises :class:`UnknownCityError` for an unknown origin or
        destination and :class:`UnknownCourierError` for an unknown
        courier — never a guessed rate.
        """
        origin_name = normalize_city(origin)
        destination_name = normalize_city(destination)
        courier_name = normalize_courier(courier)
        if weight_kg <= 0:
            raise ValueError("weight_kg must be positive")
        rate = self._rates.get((origin_name, destination_name, courier_name))
        if rate is None:
            raise UnknownCourierError(
                f"courier {courier_name!r} has no rate on record "
                f"from {origin_name} to {destination_name}"
            )
        billed_kg = max(1, math.ceil(weight_kg))
        return ShippingQuote(
            origin=origin_name,
            destination=destination_name,
            courier=courier_name,
            weight_kg=weight_kg,
            billed_kg=billed_kg,
            cost_idr=rate.rate_per_kg_idr * billed_kg,
            cod_available=rate.cod_available,
            source_label=rate.source_label,
            seeded_at=rate.seeded_at,
        )

    def rates_for(self, *, origin: str, destination: str) -> list[ShippingRate]:
        """All courier rows for one lane, sorted by courier name."""
        origin_name = normalize_city(origin)
        destination_name = normalize_city(destination)
        rows = [
            rate
            for (lane_origin, lane_destination, _), rate in self._rates.items()
            if lane_origin == origin_name and lane_destination == destination_name
        ]
        return sorted(rows, key=lambda row: row.courier)

    def cheapest(self, *, origin: str, destination: str) -> ShippingRate:
        """Cheapest courier row for one lane (COD ignored)."""
        rows = self.rates_for(origin=origin, destination=destination)
        if not rows:
            raise UnknownCityError(
                f"no shipping rates on record from {origin!r} to {destination!r}"
            )
        return min(rows, key=lambda row: row.rate_per_kg_idr)

    def cod_couriers(self, *, origin: str, destination: str) -> list[str]:
        """Courier names on this lane that offer COD, sorted."""
        return sorted(
            row.courier
            for row in self.rates_for(origin=origin, destination=destination)
            if row.cod_available
        )


def _city_pattern() -> re.Pattern[str]:
    """One regex matching any known city spelling, longest first."""
    spellings = sorted(_CITY_INDEX, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(s) for s in spellings) + r")\b")


def _courier_pattern() -> re.Pattern[str]:
    """One regex matching any known courier spelling, longest first."""
    spellings = sorted(_COURIER_INDEX, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(s) for s in spellings) + r")\b")


CITY_PATTERN = _city_pattern()
COURIER_PATTERN = _courier_pattern()
