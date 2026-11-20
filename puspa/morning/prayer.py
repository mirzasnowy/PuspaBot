"""Prayer times from a public source (P-07, plan section 12.8).

Source: the Aladhan API (``https://api.aladhan.com/v1``), a free
public prayer-times API — no key, no account. Method 20 is the
Indonesian Ministry of Religious Affairs (Kementerian Agama RI)
calculation method, so the times match the method Indonesian users
expect. The same response carries the Hijri date, which is how
the summary knows whether today is Ramadan: imsak is only shown
during Ramadan (plan 12.8 lists "prayer times and imsak during
Ramadan"), outside Ramadan the summary says so instead of showing
an imsak time nobody fasts by.

Verified live from this VM on 20 Nov 2026: the dated
``timingsByCity/{DD-MM-YYYY}`` endpoint answers 200 for Jakarta
and ``gToH`` answers the Hijri date. Note the undated
``timingsByCity`` endpoint 302-redirects to the dated one, and
following that redirect with the same httpx client fails through
this VM's egress proxy (the proxy closes the reused connection:
``RemoteProtocolError: Server disconnected``), while curl follows
it fine — so this module calls the dated endpoint directly and
never depends on a redirect. If the API ever stops answering,
:func:`fetch_prayer_times` raises :class:`PrayerSourceError` and
the summary reports the prayer section as unavailable; no time
is ever invented.

The HTTP client is injected (``httpx.Client`` with
``follow_redirects=True``); tests use ``httpx.MockTransport``
and never touch the network.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import httpx

__all__ = [
    "ALADHAN_BASE_URL",
    "JAKARTA_TZ",
    "PRAYER_METHOD",
    "PRAYER_METHOD_NAME",
    "PrayerSourceError",
    "fetch_prayer_times",
    "is_ramadan",
]

#: Public Aladhan API base (no key needed).
ALADHAN_BASE_URL = "https://api.aladhan.com/v1"

#: Calculation method 20: Kementerian Agama RI (Indonesia).
PRAYER_METHOD = 20
PRAYER_METHOD_NAME = "Kementerian Agama RI"

#: The user's day is counted in Jakarta time.
JAKARTA_TZ = ZoneInfo("Asia/Jakarta")

#: Hijri month number for Ramadan.
RAMADAN_MONTH = 9

#: Prayer names the summary shows, in order through the day.
PRAYER_NAMES = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")

#: Aladhan timing keys, in the same order as :data:`PRAYER_NAMES`
#: plus imsak (shown only during Ramadan).
_TIMING_KEYS = {
    "fajr": "Fajr",
    "sunrise": "Sunrise",
    "dhuhr": "Dhuhr",
    "asr": "Asr",
    "maghrib": "Maghrib",
    "isha": "Isha",
    "imsak": "Imsak",
}


class PrayerSourceError(Exception):
    """The prayer-times source did not answer or answered unusable data."""


def is_ramadan(hijri_month_number: int) -> bool:
    """True when the Hijri month is Ramadan (month 9)."""
    return hijri_month_number == RAMADAN_MONTH


def _default_client() -> httpx.Client:
    # Aladhan answers timingsByCity with a 302 first; follow it.
    return httpx.Client(timeout=10.0, follow_redirects=True)


def fetch_prayer_times(
    *,
    city: str = "Jakarta",
    country: str = "Indonesia",
    day: datetime.date | None = None,
    http_client: httpx.Client | None = None,
) -> dict:
    """Prayer times for one city and day, plus the Hijri date.

    Returns ``{city, country, date, timings, hijri, is_ramadan,
    method, method_name, source}`` where ``timings`` maps the
    lowercase prayer names (plus ``imsak``) to ``"HH:MM"`` strings
    and ``hijri`` carries ``{date, month, month_number, year}``.
    ``day`` defaults to today in Jakarta time. Raises
    :class:`PrayerSourceError` on any failure — the caller reports
    the prayer section as unavailable instead of guessing times.
    """
    city = (city or "Jakarta").strip() or "Jakarta"
    country = (country or "Indonesia").strip() or "Indonesia"
    day = day or datetime.datetime.now(JAKARTA_TZ).date()
    client = http_client or _default_client()
    try:
        # The dated endpoint (no redirect): the undated one
        # 302-redirects to it, and following that redirect with
        # the same client fails through this VM's egress proxy
        # (the proxy closes the reused connection), while the
        # dated URL answers directly.
        dated = day.strftime("%d-%m-%Y")
        resp = client.get(
            f"{ALADHAN_BASE_URL}/timingsByCity/{dated}",
            params={"city": city, "country": country, "method": PRAYER_METHOD},
        )
    except httpx.HTTPError as exc:
        raise PrayerSourceError(f"prayer-times source unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise PrayerSourceError(f"prayer-times source answered HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise PrayerSourceError("prayer-times source answered invalid JSON") from exc
    try:
        if payload.get("code") != 200:
            raise PrayerSourceError(
                f"prayer-times source error: {payload.get('status', 'unknown')}"
            )
        data = payload["data"]
        timings_in = data["timings"]
        timings = {name: str(timings_in[key]) for name, key in _TIMING_KEYS.items()}
        hijri = data["date"]["hijri"]
        hijri_out = {
            "date": str(hijri["date"]),
            "month": str(hijri["month"]["en"]),
            "month_number": int(hijri["month"]["number"]),
            "year": str(hijri["year"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise PrayerSourceError(f"prayer-times source answered an unexpected shape: {exc}") from exc
    return {
        "city": city,
        "country": country,
        "date": day.isoformat(),
        "timings": timings,
        "hijri": hijri_out,
        "is_ramadan": is_ramadan(hijri_out["month_number"]),
        "method": PRAYER_METHOD,
        "method_name": PRAYER_METHOD_NAME,
        "source": "Aladhan API",
    }
