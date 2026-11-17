"""Tes kontrak handoff + mesin status + detak (§2.4)."""

from datetime import datetime, timedelta, timezone

import pytest

from puspa.runtime.handoff import (Handoff, HandoffContract, HandoffError,
                                   check_handoff_limit_between_agents,
                                   reject_cycle)


def _kontrak(**kw):
    dasar = {"from_id": "agen-a", "to_id": "agen-b", "purpose": "cari harga",
             "inputs": {"type": "search_price", "product": "laptop"},
             "expected_output": {"type": "price"},
             "completion_definition": "harga + toko + tautan ditemukan"}
    dasar.update(kw)
    return HandoffContract(**dasar)


def test_contract_valid_and_field_required():
    k = _kontrak()
    assert k.timeout_seconds == 45
    assert k.idempotency_key.startswith("handoff-")


def test_contract_without_schema_rejected():
    with pytest.raises(HandoffError):
        _kontrak(inputs={"product": "laptop"})  # tanpa 'jenis'
    with pytest.raises(HandoffError):
        _kontrak(expected_output={"value": 1})
    with pytest.raises(HandoffError):
        _kontrak(permission_class="Raja")
    with pytest.raises(HandoffError):
        _kontrak(from_id="agen-a", to_id="agen-a")
    with pytest.raises(HandoffError):
        _kontrak(completion_definition="  ")


def test_handoff_state_machine_full_status():
    h = Handoff(_kontrak())
    assert h.status == "proposed"
    h.accept()
    h.start()
    assert h.hand_over({"type": "price", "value": 12500000}) == "handed_over_at"
    h.mark_passed()
    assert h.status == "passed"


def test_output_failing_schema_rejected():
    h = Handoff(_kontrak())
    h.accept()
    h.start()
    assert h.hand_over({"type": "bukan_price"}) == "rejected"
    assert h.status == "rejected"


def test_transition_not_valid_rejected():
    h = Handoff(_kontrak())
    with pytest.raises(HandoffError):
        h.start()  # belum diterima
    with pytest.raises(HandoffError):
        h.hand_over({"type": "price"})


def test_one_repeated_trial_then_report():
    h = Handoff(_kontrak())
    h.accept()
    h.start()
    h.reject("sumber tidak bisa dibaca")
    assert h.retry() is True
    assert h.status == "proposed" and h.trial == 2
    h.reject("gagal lagi")
    assert h.retry() is False  # habis: agen melapor ke user


def test_stuck_without_heartbeat_30_seconds():
    h = Handoff(_kontrak())
    h.accept()
    h.start()
    now = h.created_at + timedelta(seconds=31)
    assert h.is_stalled(now) is True
    h.heartbeat(h.created_at + timedelta(seconds=20))
    assert h.is_stalled(h.created_at + timedelta(seconds=40)) is False
    assert h.is_stalled(h.created_at + timedelta(seconds=51)) is True


def test_stuck_past_time_limit():
    h = Handoff(_kontrak(timeout_seconds=45))
    h.accept()
    h.start()
    # Detak terus, tapi tetap lewat batas waktu kontrak.
    h.heartbeat(h.created_at + timedelta(seconds=40))
    assert h.is_stalled(h.created_at + timedelta(seconds=46)) is True


def test_circular_handoff_rejected_and_max_three_handoffs():
    reject_cycle(["agen-a", "agen-b"], "agen-b", "agen-c")  # boleh
    with pytest.raises(HandoffError, match="melingkar"):
        reject_cycle(["agen-a", "agen-b"], "agen-b", "agen-a")
    check_handoff_limit_between_agents(2)
    with pytest.raises(HandoffError):
        check_handoff_limit_between_agents(3)
