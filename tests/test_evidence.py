"""Tes Pemeriksa + Buku Bukti + renderer angka yatim (§2.5) + proyeksi (§2.3)."""

from datetime import datetime, timedelta, timezone

import pytest

from puspa.evidence.evidence import (record_evidence, summary_vonis,
                                      skor_keandalan, EvidenceError)
from puspa.evidence.projections import (audit, cost, items_evidence, office,
                                        thread_chat)
from puspa.evidence.renderer import search_number_yatim, render, sajikan
from puspa.evidence.verifier import periksa, periksa_suntikan


def _evidence(value=12500000, url="https://tokopedia.com/produk/laptop-x"):
    return record_evidence(task_id="tugas-1", klaim="Laptop X di Tokopedia",
                       value=value, unit="Rp", sumber_url=url,
                       text_excerpt="Laptop X Rp12.500.000 toko resmi",
                       halaman="<html>Laptop X Rp12.500.000</html>",
                       reader_id="sub-1")


def _read_ulang_correct(url):
    return {"value": 12500000, "name": "Laptop X di Tokopedia",
            "time_take": datetime.now(timezone.utc)}


def test_record_evidence_validasi():
    b = _evidence()
    assert b.evidence_id.startswith("bukti-")
    assert len(b.text_excerpt) <= 200
    assert b.vonis == "Uncertain"
    with pytest.raises(EvidenceError):
        record_evidence(task_id="t", klaim="x", value="bukan angka", unit="Rp",
                    sumber_url="https://tokopedia.com/x", text_excerpt="x",
                    reader_id="s")
    with pytest.raises(EvidenceError):
        record_evidence(task_id="t", klaim="x", value=100, unit="Rp",
                    sumber_url="https://situs-asing.example/x", text_excerpt="x",
                    reader_id="s")


def test_pemeriksa_verified_saat_value_same():
    b = _evidence()
    periksa(b, reread=_read_ulang_correct)
    assert b.vonis == "Verified"


def test_pemeriksa_tangkap_price_wrong_disuntik_3_dari_3():
    for value_wrong in (9999999, 15000000, 100000):
        b = _evidence()
        vonis = periksa_suntikan(b, value_wrong=value_wrong, reread=_read_ulang_correct)
        assert vonis != "Verified", value_wrong


def test_pemeriksa_not_can_dipastikan_saat_sumber_not_can_dibaca():
    b = _evidence()
    periksa(b, reread=lambda url: (_ for _ in ()).throw(RuntimeError("timeout")))
    assert b.vonis == "Cannot be determined"


def test_pemeriksa_not_yet_pasti_saat_value_different_tapi_sumber_berubah():
    b = _evidence()
    def read_new(url):
        return {"value": 12900000, "name": "Laptop X di Tokopedia",
                "time_take": datetime.now(timezone.utc) + timedelta(minutes=5)}
    periksa(b, reread=read_new)
    assert b.vonis == "Uncertain"


def test_renderer_content_penanda_dari_evidence():
    b = _evidence()
    b.vonis = "Verified"
    text, missing = render("Harga laptop {harga:" + b.evidence_id + "} di Tokopedia.",
                         {b.evidence_id: b})
    assert text == "Harga laptop Rp12.500.000 di Tokopedia."
    assert missing == []


def test_number_yatim_ditahan_lalu_dibuang():
    # 3 dari 3 angka yatim yang disuntikkan harus ditahan/dibuang.
    for teks_yatim in ("Harganya Rp9.999.000.", "Tagihan Rp132.500 jatuh tempo 20 Nov.",
                       "Selisihnya 15% lebih murah."):
        result = sajikan(teks_yatim, {})
        assert result["ditahan"] is True
        assert result["dibuang"]
        assert "tidak bisa memastikan angkanya" in result["text"]
        assert search_number_yatim(result["text"]) == []


def test_number_yatim_diperbaiki_satu_kali():
    b = _evidence()
    b.vonis = "Verified"
    def perbaiki(text):
        return text.replace("Rp9.999.000", "{harga:" + b.evidence_id + "}")
    result = sajikan("Harganya Rp9.999.000.", {b.evidence_id: b}, perbaiki=perbaiki)
    assert result["ditahan"] is True
    assert result["dibuang"] == []
    assert "Rp12.500.000" in result["text"]


def test_skor_keandalan_and_turunkan_vonis():
    skor = skor_keandalan(["Verified"] * 46 + ["Uncertain"] * 4)
    assert skor["persen_verified"] == 92.0
    assert skor["turunkan_vonis"] is False
    low = skor_keandalan(["Verified"] * 30 + ["Uncertain"] * 20)
    assert low["turunkan_vonis"] is True
    assert skor_keandalan([])["persen_verified"] is None


def test_summary_vonis_lencana_office():
    b1, b2 = _evidence(), _evidence()
    b1.vonis, b2.vonis = "Verified", "Uncertain"
    assert summary_vonis([b1, b2]) == "1 terverifikasi, 1 belum pasti"


def test_proyeksi_dari_ledger():
    event = [
        {"type": "user_message", "task_id": "t1", "agent_id": "puspa",
         "content": {"dari": "users", "text": "halo"}, "created_at": "2026-11-17"},
        {"type": "task_created", "task_id": "t1", "agent_id": "puspa",
         "content": {"cost_rp": 500}, "created_at": "2026-11-17"},
        {"type": "handoff_proposed", "task_id": "t1", "agent_id": "puspa",
         "content": {"dari": "puspa", "to": "sub-1", "purpose": "search"}, "created_at": "2026-11-17"},
        {"type": "evidence_recorded", "task_id": "t1", "agent_id": "puspa",
         "content": {"evidence_id": "bukti-1", "klaim": "price", "value": 100}, "created_at": "2026-11-17"},
        {"type": "verification_completed", "task_id": "t1", "agent_id": "puspa",
         "content": {"evidence_id": "bukti-1", "vonis": "Verified"}, "created_at": "2026-11-17"},
        {"type": "task_completed", "task_id": "t1", "agent_id": "puspa",
         "content": {"cost_rp": 300}, "created_at": "2026-11-17"},
    ]
    assert len(thread_chat(event)) == 3
    assert office(event)["agents"][0]["status"] == "idle"
    assert office(event)["agents"][0]["cost_rp"] == 800
    assert items_evidence(event)[0]["vonis"] == "Verified"
    assert cost(event)["total_rp"] == 800
    assert audit(event) == []
