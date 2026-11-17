"""Tes wedge tagihan PPOB (§4.2 revisi 7) di atas situs uji lokal.

Nomor pelanggan Nero (keputusan 5) belum ada — semua uji memakai
data uji + situs uji lokal (fixture). TIDAK ADA angka tagihan nyata
yang dikarang: nilai yang diuji adalah nilai fixture yang diketahui.
"""

import httpx
import pytest

from puspa.billing import billing
from puspa.billing.fixture import DATA_TEST, SitusTest


def _read_dari_fixture(situs):
    def _read(url):
        provider = url.split("fixture://")[1].split("/")[0]
        return httpx.get(situs.url(provider), timeout=5).text

    return _read


def _read_ulang_dari_fixture(situs):
    def _ulang(url):
        # Pemeriksa membaca ulang situs yang sama, independen (tanpa
        # menerima halaman dari Pembaca — hanya klaim + URL).
        provider = url.split("fixture://")[1].split("/")[0] if "fixture://" in url else "pln"
        text = httpx.get(situs.url(provider), timeout=5).text
        import re

        amount = float(re.search(r"Rp\s*([\d.]+)", text).group(1).replace(".", ""))
        return {"value": amount, "name": f"tagihan {provider}"}

    return _ulang


def test_check_bill_pln_fixture(make_user, test_dsn):
    user = make_user()
    with SitusTest() as situs:
        result = billing.check_bill(
            user_id=user["user_id"], agent_id=user["agent_id"], provider="PLN",
            number_pelanggan="123456789012", read_situs=_read_dari_fixture(situs),
            reread=_read_ulang_dari_fixture(situs),
            task_id="tugas-pln-1", mode="fixture", dsn=test_dsn)
    assert result["success"] is True
    assert result["amount"] == DATA_TEST["pln"]["amount"]
    assert result["due_date"] == DATA_TEST["pln"]["due_date"]
    assert result["status"] == "not_yet_pay"
    assert result["vonis"] == "Verified"
    assert result["evidence_id"]
    # Prosedur PLN lahir otomatis sebagai percobaan.
    assert result["procedures"]["service"] == "PLN"


def test_check_bill_keempat_provider(make_user, test_dsn):
    user = make_user()
    with SitusTest() as situs:
        for provider, number in (("PLN", "123456789012"), ("PDAM", "987654321"),
                                ("airtime", "081234567890"), ("paket data", "081234567890")):
            result = billing.check_bill(
                user_id=user["user_id"], agent_id=user["agent_id"], provider=provider,
                number_pelanggan=number, read_situs=_read_dari_fixture(situs),
                reread=_read_ulang_dari_fixture(situs),
                task_id=f"tugas-{provider}", mode="fixture", dsn=test_dsn)
            assert result["success"] is True, provider
            assert result["amount"] == DATA_TEST[provider.lower()]["amount"], provider


def test_bpjs_rejected_tegas(make_user, test_dsn):
    user = make_user()
    result = billing.check_bill(
        user_id=user["user_id"], agent_id=user["agent_id"], provider="BPJS",
        number_pelanggan="0001234567890", read_situs=lambda u: "",
        reread=lambda u: {}, task_id="tugas-bpjs", mode="fixture", dsn=test_dsn)
    assert result["success"] is False and result["reason"] == "provider_di_luar_mvp"


def test_prosedur_broken_not_dijalankan(make_user, test_dsn):
    user = make_user()
    with SitusTest() as situs:
        def _failed(url):
            raise RuntimeError("situs down")

        for i in range(2):
            result = billing.check_bill(
                user_id=user["user_id"], agent_id=user["agent_id"], provider="PLN",
                number_pelanggan="123456789012", read_situs=_failed,
                reread=lambda u: {}, task_id=f"tugas-gagal-{i}",
                mode="fixture", dsn=test_dsn)
            assert result["success"] is False
        # Prosedur kini rusak -> cek berikutnya menolak tanpa membaca.
        dipanggil = {"n": 0}

        def _hitung(url):
            dipanggil["n"] += 1
            return httpx.get(situs.url("pln"), timeout=5).text

        result = billing.check_bill(
            user_id=user["user_id"], agent_id=user["agent_id"], provider="PLN",
            number_pelanggan="123456789012", read_situs=_hitung,
            reread=_read_ulang_dari_fixture(situs),
            task_id="tugas-sesudah-rusak", mode="fixture", dsn=test_dsn)
        assert result["success"] is False and result["reason"] == "prosedur_broken"
        assert dipanggil["n"] == 0


def test_reminder_due_date():
    from datetime import date

    assert billing.reminder_due_date(due_date="2026-11-20", day_sebelum=3,
                                         today=date(2026, 11, 18)) == {
        "perlu_remind": True, "day_lagi": 2}
    assert billing.reminder_due_date(due_date="2026-11-20", day_sebelum=3,
                                         today=date(2026, 11, 10))["perlu_remind"] is False
    assert billing.reminder_due_date(due_date=None)["perlu_remind"] is False


def test_siapkan_pay_hanya_draft_without_eksekusi():
    draft = billing.siapkan_pay(provider="PLN", amount=132500,
                                 due_date="2026-11-20",
                                 number_pelanggan_masked="1234******12")
    assert draft["mode"] == "prepare_payment"
    assert draft["draft"]["amount"] == 132500.0
    assert len(draft["content_hash"]) == 64
    assert "tidak memegang" in draft["message"]
