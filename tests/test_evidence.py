"""Tes Pemeriksa + Buku Bukti + renderer angka yatim (§2.5) + proyeksi (§2.3)."""

from datetime import datetime, timedelta, timezone

import pytest

from puspa.evidence.evidence import (record_evidence, verdict_summary,
                                      reliability_score, EvidenceError)
from puspa.evidence.projections import (audit, cost, items_evidence, office,
                                        thread_chat)
from puspa.evidence.renderer import find_orphan_numbers, render, present
from puspa.evidence.verifier import verify, verify_injection


def _evidence(value=12500000, url="https://tokopedia.com/produk/laptop-x"):
    return record_evidence(task_id="tugas-1", claim="Laptop X di Tokopedia",
                       value=value, unit="Rp", source_url=url,
                       text_excerpt="Laptop X Rp12.500.000 toko resmi",
                       page="<html>Laptop X Rp12.500.000</html>",
                       reader_id="sub-1")


def _read_ulang_correct(url):
    return {"value": 12500000, "name": "Laptop X di Tokopedia",
            "time_take": datetime.now(timezone.utc)}


def test_record_evidence_validation():
    b = _evidence()
    assert b.evidence_id.startswith("bukti-")
    assert len(b.text_excerpt) <= 200
    assert b.verdict == "Uncertain"
    with pytest.raises(EvidenceError):
        record_evidence(task_id="t", claim="x", value="bukan angka", unit="Rp",
                    source_url="https://tokopedia.com/x", text_excerpt="x",
                    reader_id="s")
    with pytest.raises(EvidenceError):
        record_evidence(task_id="t", claim="x", value=100, unit="Rp",
                    source_url="https://situs-asing.example/x", text_excerpt="x",
                    reader_id="s")


def test_verifier_marks_verified_when_value_same():
    b = _evidence()
    verify(b, reread=_read_ulang_correct)
    assert b.verdict == "Verified"


def test_verifier_catches_injected_wrong_price_3_of_3():
    for value_wrong in (9999999, 15000000, 100000):
        b = _evidence()
        verdict = verify_injection(b, value_wrong=value_wrong, reread=_read_ulang_correct)
        assert verdict != "Verified", value_wrong


def test_verifier_cannot_confirm_when_source_unreadable():
    b = _evidence()
    verify(b, reread=lambda url: (_ for _ in ()).throw(RuntimeError("timeout")))
    assert b.verdict == "Cannot be determined"


def test_verifier_uncertain_when_value_differs_but_source_changes():
    b = _evidence()
    def read_new(url):
        return {"value": 12900000, "name": "Laptop X di Tokopedia",
                "time_take": datetime.now(timezone.utc) + timedelta(minutes=5)}
    verify(b, reread=read_new)
    assert b.verdict == "Uncertain"


def test_renderer_content_marker_from_evidence():
    b = _evidence()
    b.verdict = "Verified"
    text, missing = render("Harga laptop {harga:" + b.evidence_id + "} di Tokopedia.",
                         {b.evidence_id: b})
    assert text == "Harga laptop Rp12.500.000 di Tokopedia."
    assert missing == []


def test_number_orphan_held_then_discarded():
    # 3 dari 3 angka yatim yang disuntikkan harus ditahan/dibuang.
    for teks_yatim in ("Harganya Rp9.999.000.", "Tagihan Rp132.500 jatuh tempo 20 Nov.",
                       "Selisihnya 15% lebih murah."):
        result = present(teks_yatim, {})
        assert result["held"] is True
        assert result["dropped"]
        assert "tidak bisa memastikan angkanya" in result["text"]
        assert find_orphan_numbers(result["text"]) == []


def test_number_orphan_fixed_once():
    b = _evidence()
    b.verdict = "Verified"
    def perbaiki(text):
        return text.replace("Rp9.999.000", "{harga:" + b.evidence_id + "}")
    result = present("Harganya Rp9.999.000.", {b.evidence_id: b}, fix=perbaiki)
    assert result["held"] is True
    assert result["dropped"] == []
    assert "Rp12.500.000" in result["text"]


def test_score_reliability_and_lower_verdict():
    score = reliability_score(["Verified"] * 46 + ["Uncertain"] * 4)
    assert score["percent_verified"] == 92.0
    assert score["downgrade_verdict"] is False
    low = reliability_score(["Verified"] * 30 + ["Uncertain"] * 20)
    assert low["downgrade_verdict"] is True
    assert reliability_score([])["percent_verified"] is None


def test_summary_verdict_badge_office():
    b1, b2 = _evidence(), _evidence()
    b1.verdict, b2.verdict = "Verified", "Uncertain"
    assert verdict_summary([b1, b2]) == "1 verified, 1 uncertain"


def test_projection_from_ledger():
    event = [
        {"type": "user_message", "task_id": "t1", "agent_id": "puspa",
         "content": {"sender": "users", "text": "halo"}, "created_at": "2026-11-17"},
        {"type": "task_created", "task_id": "t1", "agent_id": "puspa",
         "content": {"cost_rp": 500}, "created_at": "2026-11-17"},
        {"type": "handoff_proposed", "task_id": "t1", "agent_id": "puspa",
         "content": {"sender": "puspa", "to": "sub-1", "purpose": "search"}, "created_at": "2026-11-17"},
        {"type": "evidence_recorded", "task_id": "t1", "agent_id": "puspa",
         "content": {"evidence_id": "bukti-1", "claim": "price", "value": 100}, "created_at": "2026-11-17"},
        {"type": "verification_completed", "task_id": "t1", "agent_id": "puspa",
         "content": {"evidence_id": "bukti-1", "verdict": "Verified"}, "created_at": "2026-11-17"},
        {"type": "task_completed", "task_id": "t1", "agent_id": "puspa",
         "content": {"cost_rp": 300}, "created_at": "2026-11-17"},
    ]
    assert len(thread_chat(event)) == 3
    assert office(event)["agents"][0]["status"] == "idle"
    assert office(event)["agents"][0]["cost_rp"] == 800
    assert items_evidence(event)[0]["verdict"] == "Verified"
    assert cost(event)["total_rp"] == 800
    assert audit(event) == []
