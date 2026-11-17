"""Tes pemicu (§2.12 + §2.14): 4 pemicu MVP, tolak email/webhook."""

from datetime import date, datetime, timezone

import pytest

from puspa.workflow.triggers import (
    TriggerError, match_price_turun, match_due_date, next_trigger_at,
    save_to_penjadwal, validasi_trigger,
)

SEKARANG = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def test_jadwal_tanggal_monthly():
    p = {"type": "jadwal", "mode": "tanggal", "tanggal": 25, "jam_mulai": "07:00"}
    assert validasi_trigger(p) == []
    berikut = next_trigger_at(p, SEKARANG)
    assert (berikut.year, berikut.month, berikut.day) == (2026, 11, 25)
    assert (berikut.hour, berikut.minute) == (7, 0)


def test_schedule_date_past_this_month_to_next_month():
    p = {"type": "jadwal", "mode": "tanggal", "tanggal": 10, "jam_mulai": "07:00"}
    berikut = next_trigger_at(p, SEKARANG)
    assert (berikut.year, berikut.month, berikut.day) == (2026, 12, 10)


def test_jadwal_day_weekly():
    p = {"type": "jadwal", "mode": "day", "day": "senin", "jam_mulai": "07:00"}
    berikut = next_trigger_at(p, SEKARANG)  # 17 Nov 2026 = Selasa
    assert berikut.weekday() == 0
    assert (berikut.year, berikut.month, berikut.day) == (2026, 11, 23)


def test_jadwal_interval_minimal_1_jam():
    p = {"type": "jadwal", "mode": "interval_jam", "jam": 0.5}
    assert any("minimal 1 jam" in m for m in validasi_trigger(p))
    p2 = {"type": "jadwal", "mode": "interval_jam", "jam": 1}
    assert validasi_trigger(p2) == []
    assert next_trigger_at(p2, SEKARANG) == datetime(
        2026, 11, 17, 13, 0, tzinfo=timezone.utc)


def test_trigger_email_and_webhook_rejected():
    for jenis in ("email_login", "webhook"):
        masalah = validasi_trigger({"type": jenis})
        assert masalah and "sesudah MVP" in masalah[0]


def test_due_date_n_day_sebelum():
    p = {"type": "due_date", "day_sebelum": 3, "provider": "PLN"}
    assert validasi_trigger(p) == []
    bill = [
        {"provider": "PLN", "due_date": "2026-11-20", "status": "not_yet_pay"},
        {"provider": "PLN", "due_date": "2026-11-30", "status": "not_yet_pay"},
        {"provider": "PLN", "due_date": "2026-11-19", "status": "already_pay"},
        {"provider": "PDAM", "due_date": "2026-11-19", "status": "not_yet_pay"},
    ]
    match = match_due_date(p, bill, today=date(2026, 11, 17))
    assert len(match) == 1 and match[0]["due_date"] == "2026-11-20"


def test_price_turun_di_bawah_ambang():
    p = {"type": "price_turun", "product_id": "hp-x", "price_threshold": 5000000}
    assert validasi_trigger(p) == []
    assert match_price_turun(p, 4999999) is True
    assert match_price_turun(p, 5000000) is True
    assert match_price_turun(p, 5000001) is False
    assert match_price_turun(p, None) is False  # belum ada data: tidak memicu


def test_save_trigger_jadwal_to_penjadwal_fase2(make_user, test_dsn):
    user_id, _ = make_user()
    p = {"type": "jadwal", "mode": "tanggal", "tanggal": 25, "jam_mulai": "07:00"}
    trigger = save_to_penjadwal(user_id=user_id, workflow_id="alur-uji",
                                 trigger=p, sekarang=SEKARANG, dsn=test_dsn)
    assert trigger is not None
    assert trigger.jenis == "jadwal"
    assert trigger.workflow == "alur-uji"
    assert (trigger.next_trigger_at.year, trigger.next_trigger_at.month,
            trigger.next_trigger_at.day) == (2026, 11, 25)


def test_trigger_non_jadwal_not_disimpan_to_penjadwal():
    assert save_to_penjadwal(user_id="u", workflow_id="a",
                               trigger={"type": "manual"}) is None
