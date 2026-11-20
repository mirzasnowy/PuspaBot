"""National holidays from a public source (P-07, plan section 12.8).

Source: Nager.Date (``https://date.nager.at/api/v3``), a free
public-holidays API — no key, no account. Verified live from this
VM on 20 Nov 2026: ``/PublicHolidays/2026/ID`` answers the
Indonesian holidays for 2026, with Indonesian local names
(``localName``). If the API ever stops answering,
:func:`fetch_public_holidays` raises :class:`HolidaySourceError`
and the summary reports the holidays section as unavailable; no
holiday is ever invented.

The HTTP client is injected (``httpx.Client``); tests use
``httpx.MockTransport`` and never touch the network.
"""

from __future__ import annotations

import datetime

import httpx

__all__ = [
    "NAGER_BASE_URL",
    "HolidaySourceError",
    "fetch_public_holidays",
    "holidays_on_date",
    "upcoming_holidays",
]

#: Public Nager.Date API base (no key needed).
NAGER_BASE_URL = "https://date.nager.at/api/v3"

#: How far ahead the morning summary looks for holidays.
UPCOMING_DAYS = 30

#: How many upcoming holidays the summary lists at most.
MAX_UPCOMING = 5


class HolidaySourceError(Exception):
    """The holidays source did not answer or answered unusable data."""


def _default_client() -> httpx.Client:
    return httpx.Client(timeout=10.0, follow_redirects=True)


def fetch_public_holidays(
    year: int,
    *,
    country: str = "ID",
    http_client: httpx.Client | None = None,
) -> list[dict]:
    """All public holidays for one country and year.

    Returns a list of ``{date, local_name, name}`` sorted by date
    (``date`` is ``YYYY-MM-DD``). Raises :class:`HolidaySourceError`
    on any failure — the caller reports the holidays section as
    unavailable instead of guessing.
    """
    if not 1900 <= year <= 2200:
        raise HolidaySourceError(f"year out of range: {year}")
    client = http_client or _default_client()
    try:
        resp = client.get(f"{NAGER_BASE_URL}/PublicHolidays/{year}/{country}")
    except httpx.HTTPError as exc:
        raise HolidaySourceError(f"holidays source unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise HolidaySourceError(f"holidays source answered HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise HolidaySourceError("holidays source answered invalid JSON") from exc
    try:
        holidays = [
            {
                "date": str(item["date"]),
                "local_name": str(item["localName"]),
                "name": str(item["name"]),
            }
            for item in payload
        ]
    except (KeyError, TypeError) as exc:
        raise HolidaySourceError(f"holidays source answered an unexpected shape: {exc}") from exc
    holidays.sort(key=lambda item: item["date"])
    return holidays


def holidays_on_date(holidays: list[dict], day: datetime.date) -> list[dict]:
    """Holidays falling on one day (usually zero or one)."""
    wanted = day.isoformat()
    return [item for item in holidays if item["date"] == wanted]


def upcoming_holidays(
    holidays: list[dict],
    today: datetime.date,
    *,
    days: int = UPCOMING_DAYS,
    limit: int = MAX_UPCOMING,
) -> list[dict]:
    """Holidays from tomorrow up to ``days`` days ahead, soonest first.

    Pure function: ``holidays`` is the full-year list from
    :func:`fetch_public_holidays`. Today itself is excluded —
    it belongs in the "today" section, not the upcoming one.
    """
    last = today + datetime.timedelta(days=days)
    upcoming = [item for item in holidays if today.isoformat() < item["date"] <= last.isoformat()]
    upcoming.sort(key=lambda item: item["date"])
    return upcoming[:limit]
