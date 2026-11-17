"""Tes kontrak handoff + mesin status + detak (§2.4)."""

from datetime import datetime, timedelta, timezone

import pytest

from puspa.runtime.handoff import (Handoff, HandoffContract, HandoffError,
                                   check_batas_handoff_antar_agent,
                                   tolak_lingkaran)


def _kontrak(**kw):
    dasar = {"dari_id": "agen-a", "to_id": "agen-b", "purpose": "cari harga",
             "masukan": {"type": "search_price", "product": "laptop"},
             "expected_output": {"type": "price"},
             "completion_definition": "harga + toko + tautan ditemukan"}
    dasar.update(kw)
    return HandoffContract(**dasar)


def test_kontrak_valid_and_field_wajib():
    k = _kontrak()
    assert k.timeout_seconds == 45
    assert k.idempotency_key.startswith("handoff-")


def test_kontrak_without_skema_rejected():
    with pytest.raises(HandoffError):
        _kontrak(masukan={"product": "laptop"})  # tanpa 'jenis'
    with pytest.raises(HandoffError):
        _kontrak(expected_output={"value": 1})
    with pytest.raises(HandoffError):
        _kontrak(permission_class="Raja")
    with pytest.raises(HandoffError):
        _kontrak(dari_id="agen-a", to_id="agen-a")
    with pytest.raises(HandoffError):
        _kontrak(completion_definition="  ")


def test_mesin_status_lengkap():
    h = Handoff(_kontrak())
    assert h.status == "proposed"
    h.terima()
    h.mulai()
    assert h.serahkan({"type": "price", "value": 12500000}) == "handed_over_at"
    h.nyatakan_passed()
    assert h.status == "passed"


def test_output_not_passed_skema_rejected():
    h = Handoff(_kontrak())
    h.terima()
    h.mulai()
    assert h.serahkan({"type": "bukan_price"}) == "rejected"
    assert h.status == "rejected"


def test_transisi_not_sah_rejected():
    h = Handoff(_kontrak())
    with pytest.raises(HandoffError):
        h.mulai()  # belum diterima
    with pytest.raises(HandoffError):
        h.serahkan({"type": "price"})


def test_satu_trial_ulang_lalu_report():
    h = Handoff(_kontrak())
    h.terima()
    h.mulai()
    h.tolak("sumber tidak bisa dibaca")
    assert h.coba_ulang() is True
    assert h.status == "proposed" and h.trial == 2
    h.tolak("gagal lagi")
    assert h.coba_ulang() is False  # habis: agen melapor ke user


def test_macet_without_heartbeat_30_detik():
    h = Handoff(_kontrak())
    h.terima()
    h.mulai()
    sekarang = h.created_at + timedelta(seconds=31)
    assert h.macet(sekarang) is True
    h.heartbeat(h.created_at + timedelta(seconds=20))
    assert h.macet(h.created_at + timedelta(seconds=40)) is False
    assert h.macet(h.created_at + timedelta(seconds=51)) is True


def test_macet_past_batas_time():
    h = Handoff(_kontrak(timeout_seconds=45))
    h.terima()
    h.mulai()
    # Detak terus, tapi tetap lewat batas waktu kontrak.
    h.heartbeat(h.created_at + timedelta(seconds=40))
    assert h.macet(h.created_at + timedelta(seconds=46)) is True


def test_lingkaran_rejected_and_maks_tiga_handoff():
    tolak_lingkaran(["agen-a", "agen-b"], "agen-b", "agen-c")  # boleh
    with pytest.raises(HandoffError, match="melingkar"):
        tolak_lingkaran(["agen-a", "agen-b"], "agen-b", "agen-a")
    check_batas_handoff_antar_agent(2)
    with pytest.raises(HandoffError):
        check_batas_handoff_antar_agent(3)
