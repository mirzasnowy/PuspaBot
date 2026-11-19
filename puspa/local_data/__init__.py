"""Local Indonesian data layer (ticket B5).

Indonesian prices, sellers, and payment rails as data assets, plus
Indonesian language handling. The layer holds three things:

- :mod:`puspa.local_data.rates`: shipping rates by city and
  courier, with a COD flag. The rates shipped here are SEEDED
  SAMPLE data with provenance (city + courier + rate + COD flag),
  computed deterministically — they are never presented as live
  courier tariffs. A loader interface (:class:`rates.RateLoader`)
  is the refresh seam for real courier data later.
- :mod:`puspa.local_data.rails`: naming the correct Indonesian
  payment rail (QRIS, virtual account, bank transfer, GoPay, OVO,
  DANA, COD) for a payment mention. Naming only: no payment is
  ever prepared or executed here (that is ticket B8).
- :mod:`puspa.local_data.language`: everyday Indonesian and
  mixed Indonesian-English input (``ongkir``, ``COD``, ``PO``,
  ``DP``, ...) normalised into the canonical terms the
  intent and price-check flow already understand.
- :mod:`puspa.local_data.money`: money amounts as users write
  them (``Rp1.250.000``, ``50rb``, ``1,5 jt``, ``seratus
  ribu``) parsed into integer Rupiah, and integer Rupiah
  rendered the local way (``Rp1.250.000``).

No seller data is invented here: seller track records stay in
``puspa.check_before_buy.records`` and are only ever read from
recorded data. No live fetch happens anywhere in this package.
"""

from __future__ import annotations

from .language import NormalizedText, extract_courier, extract_destination_city, normalize_id_text
from .money import format_rupiah, parse_rupiah_amount
from .rails import PaymentRail, detect_rail, rail_name
from .rates import (
    JsonRateLoader,
    RateLoader,
    RateStore,
    SeedRateLoader,
    ShippingQuote,
    ShippingRate,
    UnknownCityError,
    UnknownCourierError,
)

__all__ = [
    "JsonRateLoader",
    "NormalizedText",
    "PaymentRail",
    "RateLoader",
    "RateStore",
    "SeedRateLoader",
    "ShippingQuote",
    "ShippingRate",
    "UnknownCityError",
    "UnknownCourierError",
    "detect_rail",
    "extract_courier",
    "extract_destination_city",
    "format_rupiah",
    "normalize_id_text",
    "parse_rupiah_amount",
    "rail_name",
]
