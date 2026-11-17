"""Tes uji kaset (§2.11) + 10 kaset acuan."""

import json
from pathlib import Path

import pytest

from puspa.cassette.cassette import (CassetteError, CassettePlayer,
                                     TASK_REFERENCE, delete_cassette_task,
                                     muat_cassette, rekam_dari_ledger,
                                     save_cassette, suntik_price_wrong)

CASSETTE_DIR = Path(__file__).resolve().parent.parent / "cassettes"


def test_sepuluh_cassette_reference_ada():
    assert len(TASK_REFERENCE) == 10
    for name in TASK_REFERENCE:
        path = CASSETTE_DIR / f"{name}.json"
        assert path.exists(), f"kaset acuan hilang: {name}"
        data = muat_cassette(path)
        assert data["recording"], f"kaset {name} kosong"
        assert all("tool" in r and "output" in r for r in data["recording"])


def test_rekam_dari_ledger_and_putar_ulang_identik(tmp_path):
    event = [
        {"type": "tool_completed", "task_id": "t1",
         "content": {"tool": "read_price", "output": {"type": "price", "value": 12500000}}},
        {"type": "tool_completed", "task_id": "t1",
         "content": {"tool": "read_price", "output": {"type": "price", "value": 11999000}}},
        {"type": "agent_message", "task_id": "t1", "content": {"text": "bukan tool"}},
    ]
    cassette = rekam_dari_ledger(event, "t1")
    assert len(cassette["recording"]) == 2
    berkas = save_cassette(cassette, "uji-putar", cassette_dir=tmp_path)
    player = CassettePlayer(muat_cassette(berkas))
    assert player.putar("read_price") == {"type": "price", "value": 12500000}
    assert player.putar("read_price") == {"type": "price", "value": 11999000}
    with pytest.raises(CassetteError):
        player.putar("read_price")  # kaset habis: jujur, bukan karangan


def test_suntik_price_wrong_ditangkap_pemeriksa(tmp_path):
    cassette = {"task_id": "t1", "recording": [
        {"tool": "read_price", "output": {"type": "price", "value": 12500000}}]}
    disuntik = suntik_price_wrong(cassette, tool="read_price", value_new=1000)
    assert cassette["recording"][0]["output"]["value"] == 12500000  # asli tidak diubah
    player = CassettePlayer(disuntik)
    assert player.putar("read_price")["value"] == 1000
    # Pemeriksa membandingkan dengan baca ulang yang benar.
    from puspa.evidence.evidence import record_evidence
    from puspa.evidence.verifier import periksa

    b = record_evidence(task_id="t1", klaim="Laptop X", value=1000, unit="Rp",
                    sumber_url="https://tokopedia.com/produk/laptop-x",
                    text_excerpt="Laptop X Rp1.000", reader_id="sub-1")
    periksa(b, reread=lambda url: {"value": 12500000, "name": "Laptop X"})
    assert b.vonis != "Verified"


def test_cassette_ikut_terhapus_saat_task_deleted(tmp_path):
    cassette = {"task_id": "t-hapus", "recording": [{"tool": "a", "output": 1}]}
    save_cassette(cassette, "kaset-hapus", cassette_dir=tmp_path)
    assert (tmp_path / "kaset-hapus.json").exists()
    result = delete_cassette_task("t-hapus", cassette_dir=tmp_path)
    assert result["deleted"] == ["kaset-hapus"]
    assert not (tmp_path / "kaset-hapus.json").exists()


def test_putar_ulang_cassette_reference_number_identik_9_dari_10():
    """Gerbang 12 (parsial): 10 kaset acuan diputar ulang, angka hasil
    putar ulang identik dengan aslinya di minimal 9 dari 10."""
    identik = 0
    for name in TASK_REFERENCE:
        data = muat_cassette(CASSETTE_DIR / f"{name}.json")
        player = CassettePlayer(data)
        value_start = []
        for recording in data["recording"]:
            output = player.putar(recording["tool"])
            if isinstance(output, dict) and "value" in output:
                value_start.append(output["value"])
        value_asli = [r["output"]["value"] for r in data["recording"]
                      if isinstance(r["output"], dict) and "value" in r["output"]]
        if value_start == value_asli:
            identik += 1
    assert identik >= 9
