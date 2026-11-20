"""Tests for the national-holidays client (P-07, plan section 12.8).

Nager.Date is a free public source (no key). All tests use
``httpx.MockTransport`` — no live network. The live behaviour was
verified by hand from this VM on 20 Nov 2026
(``/PublicHolidays/2026/ID`` answers Indonesian holidays with
Indonesian local names).
"""

import datetime

import httpx
import pytest

from puspa.morning.holidays import (
    HolidaySourceError,
    fetch_public_holidays,
    holidays_on_date,
    upcoming_holidays,
)

PAYLOAD = [
    {"date": "2026-01-01", "localName": "Tahun Baru Masehi", "name": "New Year's Day"},
    {"date": "2026-04-03", "localName": "Wafat Isa Almasih", "name": "Good Friday"},
    {"date": "2026-05-01", "localName": "Hari Buruh", "name": "Labour Day"},
    {"date": "2026-12-25", "localName": "Hari Raya Natal", "name": "Christmas Day"},
]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_fetch_returns_sorted_holidays_with_local_names():
    def handler(request):
        assert request.url.path == "/api/v3/PublicHolidays/2026/ID"
        return httpx.Response(200, json=list(reversed(PAYLOAD)))

    holidays = fetch_public_holidays(2026, http_client=_client(handler))
    assert [item["date"] for item in holidays] == [
        "2026-01-01",
        "2026-04-03",
        "2026-05-01",
        "2026-12-25",
    ]
    assert holidays[0]["local_name"] == "Tahun Baru Masehi"
    assert holidays[0]["name"] == "New Year's Day"


def test_fetch_failures_raise():
    def fail_handler(request):
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(HolidaySourceError):
        fetch_public_holidays(2026, http_client=_client(fail_handler))

    def bad_json_handler(request):
        return httpx.Response(200, content=b"nope")

    with pytest.raises(HolidaySourceError):
        fetch_public_holidays(2026, http_client=_client(bad_json_handler))

    def network_handler(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(HolidaySourceError):
        fetch_public_holidays(2026, http_client=_client(network_handler))

    with pytest.raises(HolidaySourceError):
        fetch_public_holidays(1800, http_client=_client(fail_handler))


def test_holidays_on_date_and_upcoming():
    holidays = PAYLOAD
    assert holidays_on_date(holidays, datetime.date(2026, 4, 3)) == [PAYLOAD[1]]
    assert holidays_on_date(holidays, datetime.date(2026, 4, 4)) == []
    upcoming = upcoming_holidays(holidays, datetime.date(2026, 3, 20))
    assert [item["date"] for item in upcoming] == ["2026-04-03"]
    # A wider window reaches May too.
    upcoming = upcoming_holidays(holidays, datetime.date(2026, 3, 20), days=60)
    assert [item["date"] for item in upcoming] == ["2026-04-03", "2026-05-01"]
    # Today itself is not "upcoming" — it belongs in the today section.
    upcoming = upcoming_holidays(holidays, datetime.date(2026, 4, 3))
    assert [item["date"] for item in upcoming] == ["2026-05-01"]
    # The 30-day window cuts off December from March.
    assert upcoming_holidays(holidays, datetime.date(2026, 3, 20), days=30) == [PAYLOAD[1]]
    assert upcoming_holidays(holidays, datetime.date(2026, 3, 20), limit=1) == [PAYLOAD[1]]
