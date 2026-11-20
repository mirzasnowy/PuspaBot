"""Tests for the receipt parser and document classifier.

Every amount the parser emits carries an evidence id naming
the photo and the line it was read from; amounts with no line
are held, never guessed. Text inside a photo is untrusted:
instruction-like lines are dropped, never turned into items.
"""

import json
from pathlib import Path

import pytest

from puspa.receipts.documents import classify_document, detect_out_of_scope
from puspa.receipts.parser import evidence_id_for, parse_amount, parse_receipt

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"


def _fixture(name: str):
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return text, expected


@pytest.mark.parametrize("name", ["warung", "supermarket", "hostile"])
def test_fixture_parses_to_expected_draft(name):
    text, expected = _fixture(name)
    draft = parse_receipt(text, photo_id=name)
    assert draft.merchant == expected["merchant"]
    assert draft.expense_date == expected["expense_date"]
    assert draft.total == expected["total"]
    assert [(line.name, line.amount) for line in draft.lines] == [
        (item["name"], item["amount"]) for item in expected["lines"]
    ]
    assert draft.held_amounts == 0
    assert draft.total_is_sum is False


def test_every_amount_carries_a_photo_line_evidence_id():
    text, _ = _fixture("warung")
    draft = parse_receipt(text, photo_id="photo-abc")
    assert draft.total_evidence_id == "receipt-photo-abc-total"
    for line in draft.lines:
        assert line.evidence_id == f"receipt-photo-abc-line-{line.line_index}"
        assert line.evidence_id.startswith("receipt-photo-abc-")


def test_total_falls_back_to_sum_of_items_when_no_total_line():
    draft = parse_receipt("TOKO A\nSabun 10.000\nShampo 15.000\n", photo_id="x")
    assert draft.total == 25000
    assert draft.total_is_sum is True
    assert draft.total_evidence_id == "receipt-x-total"


def test_bare_amount_with_no_description_is_held_not_guessed():
    draft = parse_receipt("TOKO A\nSabun 10.000\n99.999\n", photo_id="x")
    assert draft.total == 10000
    assert draft.held_amounts == 1
    assert all(line.name == "Sabun" for line in draft.lines)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("52.500", 52500),
        ("1.234.567", 1234567),
        ("12.500,00", 12500),
        ("Rp 18.000", 18000),
        ("100000", 100000),
        ("not an amount", None),
        ("", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_evidence_id_for():
    assert evidence_id_for("p1", "total") == "receipt-p1-total"
    assert evidence_id_for("p1", "line-3") == "receipt-p1-line-3"


def test_hostile_text_in_photo_produces_no_actionable_item():
    text, _ = _fixture("hostile")
    draft = parse_receipt(text, photo_id="hostile")
    names = " ".join(line.name for line in draft.lines)
    assert "999.000" not in names
    assert "999000" not in names
    assert "TRANSFER" not in names.upper()
    assert draft.total == 27000
    assert all(line.amount < 100000 for line in draft.lines)


def test_empty_text_parses_to_empty_draft():
    draft = parse_receipt("", photo_id="x")
    assert draft.merchant is None
    assert draft.total is None
    assert draft.lines == []


@pytest.mark.parametrize(
    "text",
    [
        "KARTU TANDA PENDUDUK\nNIK: 3201234567890001\nNama: Budi Santoso",
        "KARTU KELUARGA\nNo. 3201012345678901\nKepala Keluarga: Budi",
        "PASPOR REPUBLIK INDONESIA\nNama: Budi Santoso",
        "KTP\nNIK 3201234567890001",
    ],
)
def test_identity_documents_are_refused(text):
    refused, reason = detect_out_of_scope(text)
    assert refused is True
    assert reason
    assert classify_document(text) == "id_document"


def test_official_letter_is_refused():
    text = (
        "SURAT KETERANGAN\nYang bertanda tangan di bawah ini\n"
        "Nama lengkap: Budi Santoso\nmenerangkan bahwa yang bersangkutan adalah warga kami."
    )
    refused, _ = detect_out_of_scope(text)
    assert refused is True
    assert classify_document(text) == "official_letter"


@pytest.mark.parametrize(
    "text",
    [
        "WARUNG KOPI\nKopi susu 18.000\nTOTAL Rp 18.000",
        "TOKO SURAT KABAR\nKoran 5.000\nTOTAL 5.000",
    ],
)
def test_receipts_are_not_refused(text):
    refused, _ = detect_out_of_scope(text)
    assert refused is False


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("WARUNG KOPI\nKopi 10.000\nTOTAL 10.000", "receipt"),
        ("NOTA BELANJA\nBeras 50.000\nTOTAL 50.000", "note"),
        ("TAGIHAN LISTRIK\nTOTAL Rp 150.000", "bill"),
        ("MENU HARI INI\nNasi goreng 15.000", "menu"),
        ("random words with no numbers", "unknown"),
    ],
)
def test_classify_document(text, kind):
    assert classify_document(text) == kind
