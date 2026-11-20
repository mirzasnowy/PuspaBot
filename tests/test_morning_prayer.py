"""Tests for the prayer-times client (P-07, plan section 12.8).

The Aladhan API is a free public source (no key). All tests use
``httpx.MockTransport`` — no live network. The live behaviour was
verified by hand from this VM on 20 Nov 2026 (the dated
``timingsByCity/{DD-MM-YYYY}`` endpoint answers 200 for Jakarta;
method 20 is Kementerian Agama RI; the undated endpoint
302-redirects to the dated one, which httpx cannot follow through
this VM's egress proxy — see ``puspa.morning.prayer``).
"""

import datetime

import httpx
import pytest

from puspa.morning.prayer import (
    PRAYER_METHOD,
    PrayerSourceError,
    fetch_prayer_times,
    is_ramadan,
)

CITY = "Jakarta"


def _aladhan_payload(*, hijri_month=6, timings=None):
    return {
        "code": 200,
        "status": "OK",
        "data": {
            "timings": timings
            or {
                "Fajr": "04:03",
                "Sunrise": "05:26",
                "Dhuhr": "11:38",
                "Asr": "15:02",
                "Sunset": "17:51",
                "Maghrib": "17:51",
                "Isha": "19:05",
                "Imsak": "03:53",
                "Midnight": "23:38",
                "Firstthird": "21:42",
                "Lastthird": "01:34",
            },
            "date": {
                "readable": "20 Nov 2026",
                "timestamp": "1795132800",
                "hijri": {
                    "date": f"10-0{hijri_month}-1448",
                    "month": {"number": hijri_month, "en": "Jumādá al-ākhirah"},
                    "year": "1448",
                },
                "gregorian": {"date": "20-11-2026"},
            },
            "meta": {"method": {"id": 20, "name": "Kementerian Agama Republik Indonesia"}},
        },
    }


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_fetch_returns_timings_and_hijri_date():
    def handler(request):
        assert request.url.path == "/v1/timingsByCity/20-11-2026"
        assert request.url.params["city"] == CITY
        assert request.url.params["country"] == "Indonesia"
        assert request.url.params["method"] == str(PRAYER_METHOD)
        return httpx.Response(200, json=_aladhan_payload())

    result = fetch_prayer_times(
        city=CITY, day=datetime.date(2026, 11, 20), http_client=_client(handler)
    )
    assert result["city"] == CITY
    assert result["date"] == "2026-11-20"
    assert result["timings"]["fajr"] == "04:03"
    assert result["timings"]["dhuhr"] == "11:38"
    assert result["timings"]["isha"] == "19:05"
    assert result["timings"]["imsak"] == "03:53"
    assert result["hijri"]["month_number"] == 6
    assert result["is_ramadan"] is False
    assert result["method"] == PRAYER_METHOD
    assert result["method_name"] == "Kementerian Agama RI"
    assert result["source"] == "Aladhan API"


def test_ramadan_month_is_detected():
    def handler(request):
        return httpx.Response(200, json=_aladhan_payload(hijri_month=9))

    result = fetch_prayer_times(
        city=CITY, day=datetime.date(2027, 3, 10), http_client=_client(handler)
    )
    assert result["is_ramadan"] is True
    assert result["hijri"]["month_number"] == 9
    assert is_ramadan(9) is True
    assert is_ramadan(6) is False
    assert is_ramadan(10) is False


def test_fetch_calls_the_dated_endpoint_directly():
    """The undated endpoint 302-redirects to the dated one, and
    following that redirect with the same httpx client fails
    through this VM's egress proxy — so the client must call the
    dated endpoint directly and never depend on a redirect."""
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        assert request.url.params["city"] == CITY
        assert request.url.params["country"] == "Indonesia"
        assert request.url.params["method"] == str(PRAYER_METHOD)
        return httpx.Response(200, json=_aladhan_payload())

    result = fetch_prayer_times(
        city=CITY, day=datetime.date(2026, 11, 20), http_client=_client(handler)
    )
    assert seen["path"] == "/v1/timingsByCity/20-11-2026"
    assert result["timings"]["fajr"] == "04:03"


def test_non_200_and_bad_payload_raise():
    def fail_handler(request):
        return httpx.Response(503, json={"code": 503, "status": "error"})

    with pytest.raises(PrayerSourceError):
        fetch_prayer_times(city=CITY, http_client=_client(fail_handler))

    def bad_json_handler(request):
        return httpx.Response(200, content=b"not json")

    with pytest.raises(PrayerSourceError):
        fetch_prayer_times(city=CITY, http_client=_client(bad_json_handler))

    def wrong_shape_handler(request):
        return httpx.Response(200, json={"code": 200, "status": "OK", "data": {}})

    with pytest.raises(PrayerSourceError):
        fetch_prayer_times(city=CITY, http_client=_client(wrong_shape_handler))

    def network_handler(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(PrayerSourceError):
        fetch_prayer_times(city=CITY, http_client=_client(network_handler))


def test_error_code_in_payload_raises():
    def handler(request):
        return httpx.Response(200, json={"code": 400, "status": "Bad Request", "data": {}})

    with pytest.raises(PrayerSourceError):
        fetch_prayer_times(city=CITY, http_client=_client(handler))
