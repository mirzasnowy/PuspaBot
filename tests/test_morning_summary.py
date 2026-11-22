"""Tests for the morning summary assembly (P-07, plan section 12.8).

``build_morning_summary`` is pure: these tests need no network
and no database. The bill-candidate rules (keyword match only,
due hint only from printed words, never verified) are the honest
core of the bills section, so they are tested directly too.
"""

import datetime

import pytest

from puspa.morning.summary import (
    build_morning_summary,
    extract_due_hint,
    extract_email_subject,
    find_bill_candidates,
    select_morning_focus,
)

DAY = datetime.date(2026, 11, 20)


def _prayer(*, is_ramadan=False):
    return {
        "city": "Jakarta",
        "country": "Indonesia",
        "date": "2026-11-20",
        "timings": {
            "fajr": "04:03",
            "sunrise": "05:26",
            "dhuhr": "11:38",
            "asr": "15:02",
            "maghrib": "17:51",
            "isha": "19:05",
            "imsak": "03:53",
        },
        "hijri": {
            "date": "10-06-1448",
            "month": "Jumādá al-ākhirah",
            "month_number": 9 if is_ramadan else 6,
            "year": "1448",
        },
        "is_ramadan": is_ramadan,
        "method": 20,
        "method_name": "Kementerian Agama RI",
        "source": "Aladhan API",
    }


def _status(status="ok", detail="loaded"):
    return {"status": status, "detail": detail}


def _summary(**overrides):
    kwargs = {
        "day": DAY,
        "events": [{"summary": "Rapat", "start": "09:00", "end": "10:00", "location": ""}],
        "bill_candidates": [],
        "open_tasks": [
            {"task_id": "tugas-1", "purpose": "Cek harga", "status": "accepted", "created_at": None}
        ],
        "holidays": [
            {"date": "2026-11-20", "local_name": "Hari Ini", "name": "Today Holiday"},
            {"date": "2026-12-10", "local_name": "Hari Raya Contoh", "name": "Example Holiday"},
        ],
        "prayer": _prayer(),
        "source_status": {
            "calendar": _status(),
            "gmail": _status(),
            "tasks": _status(),
            "holidays": _status(),
            "prayer": _status(),
        },
    }
    kwargs.update(overrides)
    return build_morning_summary(**kwargs)


def test_summary_carries_every_section():
    result = _summary()
    assert result["date"] == "2026-11-20"
    assert result["weekday"] == "Friday"
    assert result["schedule"]["events"][0]["summary"] == "Rapat"
    assert result["tasks"]["open"][0]["purpose"] == "Cek harga"
    assert result["holidays"]["today"][0]["local_name"] == "Hari Ini"
    assert result["holidays"]["upcoming"][0]["local_name"] == "Hari Raya Contoh"
    assert result["prayer"]["timings"]["dhuhr"] == "11:38"
    assert result["source_status"]["calendar"]["status"] == "ok"


def test_imsak_shown_only_during_ramadan():
    outside = _summary()
    assert outside["prayer"]["timings"]["imsak"] is None
    assert outside["prayer"]["is_ramadan"] is False
    assert "only during Ramadan" in outside["prayer"]["imsak_note"]
    # The other prayer times are still shown outside Ramadan.
    assert outside["prayer"]["timings"]["fajr"] == "04:03"
    during = _summary(prayer=_prayer(is_ramadan=True))
    assert during["prayer"]["timings"]["imsak"] == "03:53"
    assert during["prayer"]["is_ramadan"] is True
    assert during["prayer"]["imsak_note"] is None


def test_missing_sources_degrade_their_own_section_only():
    result = _summary(
        events=None,
        bill_candidates=None,
        open_tasks=None,
        holidays=None,
        prayer=None,
        source_status={
            "calendar": {"status": "not_connected", "detail": "Calendar is not connected."},
            "gmail": {"status": "unavailable", "detail": "Gmail failed."},
            "tasks": {"status": "ok", "detail": "0 open task(s)."},
            "holidays": {"status": "unavailable", "detail": "Holidays failed."},
            "prayer": {"status": "unavailable", "detail": "Prayer failed."},
        },
    )
    assert result["schedule"]["events"] == []
    assert result["bills"]["candidates"] == []
    assert result["holidays"]["today"] == []
    assert result["holidays"]["upcoming"] == []
    assert result["prayer"] is None
    assert result["source_status"]["calendar"]["status"] == "not_connected"
    assert result["source_status"]["gmail"]["status"] == "unavailable"


def test_due_hint_only_from_printed_words():
    assert extract_due_hint("Tagihan listrik jatuh tempo 12 Desember 2026") == "12 Desember 2026"
    assert extract_due_hint("Bayar sebelum 5 Januari ya") == "5 Januari"
    assert extract_due_hint("Jatuh tempo tanggal 28 bulan ini") == "tanggal 28"
    assert extract_due_hint("Tagihan listrik bulan ini") is None
    assert extract_due_hint("") is None


def test_email_subject_from_headers():
    data = {
        "payload": {
            "headers": [
                {"name": "From", "value": "pln@example.id"},
                {"name": "Subject", "value": "Tagihan Listrik November"},
            ]
        }
    }
    assert extract_email_subject(data) == "Tagihan Listrik November"
    assert extract_email_subject({"payload": {"headers": []}}) == ""
    assert extract_email_subject({}) == ""


def test_bill_candidates_are_keyword_matches_and_unverified():
    messages = [
        {
            "id": "m1",
            "snippet": "Tagihan listrik Anda jatuh tempo 12 Desember 2026.",
            "payload": {"headers": [{"name": "Subject", "value": "Tagihan Listrik"}]},
        },
        {
            "id": "m2",
            "snippet": "Promo diskon akhir tahun, belanja sekarang.",
            "payload": {"headers": [{"name": "Subject", "value": "Promo"}]},
        },
        {
            "id": "m3",
            "snippet": "Pembayaran cicilan Anda berhasil.",
            "payload": {"headers": [{"name": "Subject", "value": "Cicilan"}]},
        },
    ]
    candidates = find_bill_candidates(messages)
    assert [item["message_id"] for item in candidates] == ["m1", "m3"]
    first = candidates[0]
    assert first["subject"] == "Tagihan Listrik"
    assert first["due_hint"] == "12 Desember 2026"
    assert first["due_hint_source"] == "subject_or_snippet"
    assert first["verified"] is False
    assert "Open the email" in first["note"]
    assert candidates[1]["due_hint"] is None
    assert candidates[1]["due_hint_source"] is None


# -- K18 focus: the morning briefing's focus is picked by the
# workspace bus (see puspa.morning.summary.select_morning_focus).
# Scores are the fixed bids: schedule 0.75, a bill with a due
# hint 0.77, an open task 0.59, a holiday today 0.61. A bill
# with a due hint outscores a later event on score alone; only
# the imminent-event veto puts the schedule first.

JAKARTA = datetime.timezone(datetime.timedelta(hours=7))


def _at(hour, minute=0):
    return datetime.datetime(2026, 11, 20, hour, minute, tzinfo=JAKARTA)


def _bill(subject="Tagihan Listrik", due_hint="12 Desember 2026"):
    return {"message_id": "m1", "subject": subject, "due_hint": due_hint}


def _event(summary="Rapat", start="2026-11-20T09:00:00+07:00"):
    return {"summary": summary, "start": start, "end": "", "location": ""}


def test_focus_picks_schedule_over_tasks_and_holiday():
    result = _summary(now=_at(7))
    focus = result["focus"]
    assert focus["winner"]["module"] == "schedule"
    assert focus["action"] == "check_schedule"
    assert focus["scores"]["schedule"] == pytest.approx(0.75)
    assert focus["blocked"] == []


def test_focus_bill_with_due_hint_beats_later_event():
    result = _summary(
        events=[_event(start="2026-11-20T17:00:00+07:00")],
        bill_candidates=[_bill()],
        now=_at(8),
    )
    focus = result["focus"]
    assert focus["winner"]["module"] == "bills"
    assert focus["action"] == "review_bill"
    assert focus["scores"]["bills"] == pytest.approx(0.77)
    assert focus["scores"]["schedule"] == pytest.approx(0.75)
    assert focus["blocked"] == []


def test_focus_imminent_event_vetoes_bill():
    result = _summary(
        events=[_event(start="2026-11-20T09:00:00+07:00")],
        bill_candidates=[_bill()],
        now=_at(8, 30),
    )
    focus = result["focus"]
    assert focus["winner"]["module"] == "schedule"
    assert focus["action"] == "check_schedule"
    assert focus["blocked"] == [
        {
            "module": "bills",
            "action": "review_bill",
            "blocked_by": "schedule",
            "reason": "An event starts within the hour, so it owns the morning focus.",
        }
    ]


def test_focus_bill_without_due_hint_loses_to_schedule():
    result = _summary(
        events=[_event(start="2026-11-20T17:00:00+07:00")],
        bill_candidates=[_bill(due_hint=None)],
        now=_at(8),
    )
    focus = result["focus"]
    assert focus["winner"]["module"] == "schedule"
    assert focus["scores"]["bills"] == pytest.approx(0.57)


def test_focus_none_when_every_section_empty():
    result = _summary(
        events=None,
        bill_candidates=None,
        open_tasks=None,
        holidays=None,
        prayer=None,
        source_status={},
    )
    assert result["focus"] is None


def test_focus_records_broadcast_when_recorder_given():
    recorded = []
    result = _summary(now=_at(7), focus_record=recorded.append)
    assert result["focus"] is not None
    assert len(recorded) == 1
    assert recorded[0]["action"] == "check_schedule"
    assert recorded[0]["winner"]["module"] == "schedule"


def test_select_morning_focus_directly():
    """The pure focus helper: a holiday today is the only bid."""
    focus = select_morning_focus(
        day=DAY,
        events=[],
        bill_candidates=[],
        open_tasks=[],
        holidays=[
            {"date": "2026-11-20", "local_name": "Hari Ini", "name": "Today Holiday"},
        ],
        now=_at(7),
    )
    assert focus is not None
    assert focus["winner"]["module"] == "holidays"
    assert focus["action"] == "note_holiday"
    assert focus["scores"]["holidays"] == pytest.approx(0.61)
    assert (
        select_morning_focus(
            day=DAY, events=[], bill_candidates=[], open_tasks=[], holidays=[], now=_at(7)
        )
        is None
    )
