"""Tes uji kaset (§2.11) + 10 kaset acuan."""

import json
from pathlib import Path

import pytest

from puspa.cassette.cassette import (CassetteError, CassettePlayer,
                                     TASK_REFERENCE, delete_cassette_task,
                                     load_cassette, record_from_ledger,
                                     save_cassette, inject_price_wrong)

CASSETTE_DIR = Path(__file__).resolve().parent.parent / "cassettes"


def test_ten_reference_cassettes_exist():
    assert len(TASK_REFERENCE) == 10
    for name in TASK_REFERENCE:
        path = CASSETTE_DIR / f"{name}.json"
        assert path.exists(), f"kaset acuan hilang: {name}"
        data = load_cassette(path)
        assert data["recording"], f"kaset {name} kosong"
        assert all("tool" in r and "output" in r for r in data["recording"])


def test_record_from_ledger_and_replay_identically(tmp_path):
    event = [
        {"type": "tool_completed", "task_id": "t1",
         "content": {"tool": "read_price", "output": {"type": "price", "value": 12500000}}},
        {"type": "tool_completed", "task_id": "t1",
         "content": {"tool": "read_price", "output": {"type": "price", "value": 11999000}}},
        {"type": "agent_message", "task_id": "t1", "content": {"text": "bukan tool"}},
    ]
    cassette = record_from_ledger(event, "t1")
    assert len(cassette["recording"]) == 2
    file_path = save_cassette(cassette, "uji-putar", cassette_dir=tmp_path)
    player = CassettePlayer(load_cassette(file_path))
    assert player.play("read_price") == {"type": "price", "value": 12500000}
    assert player.play("read_price") == {"type": "price", "value": 11999000}
    with pytest.raises(CassetteError):
        player.play("read_price")  # kaset habis: jujur, bukan karangan


def test_injected_wrong_price_caught_by_verifier(tmp_path):
    cassette = {"task_id": "t1", "recording": [
        {"tool": "read_price", "output": {"type": "price", "value": 12500000}}]}
    disuntik = inject_price_wrong(cassette, tool="read_price", value_new=1000)
    assert cassette["recording"][0]["output"]["value"] == 12500000  # asli tidak diubah
    player = CassettePlayer(disuntik)
    assert player.play("read_price")["value"] == 1000
    # Pemeriksa membandingkan dengan baca ulang yang benar.
    from puspa.evidence.evidence import record_evidence
    from puspa.evidence.verifier import verify

    b = record_evidence(task_id="t1", claim="Laptop X", value=1000, unit="Rp",
                    source_url="https://tokopedia.com/produk/laptop-x",
                    text_excerpt="Laptop X Rp1.000", reader_id="sub-1")
    verify(b, reread=lambda url: {"value": 12500000, "name": "Laptop X"})
    assert b.verdict != "Verified"


def test_cassette_also_deleted_when_task_deleted(tmp_path):
    cassette = {"task_id": "t-hapus", "recording": [{"tool": "a", "output": 1}]}
    save_cassette(cassette, "kaset-hapus", cassette_dir=tmp_path)
    assert (tmp_path / "kaset-hapus.json").exists()
    result = delete_cassette_task("t-hapus", cassette_dir=tmp_path)
    assert result["deleted"] == ["kaset-hapus"]
    assert not (tmp_path / "kaset-hapus.json").exists()


def test_replay_cassette_reference_numbers_identical_9_of_10():
    """Gerbang 12 (parsial): 10 kaset acuan diputar ulang, angka hasil
    putar ulang identik dengan aslinya di minimal 9 dari 10."""
    identik = 0
    for name in TASK_REFERENCE:
        data = load_cassette(CASSETTE_DIR / f"{name}.json")
        player = CassettePlayer(data)
        value_start = []
        for recording in data["recording"]:
            output = player.play(recording["tool"])
            if isinstance(output, dict) and "value" in output:
                value_start.append(output["value"])
        value_asli = [r["output"]["value"] for r in data["recording"]
                      if isinstance(r["output"], dict) and "value" in r["output"]]
        if value_start == value_asli:
            identik += 1
    assert identik >= 9
