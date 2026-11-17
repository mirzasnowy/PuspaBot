# Tes 9 jenis kejadian baru Buku Kerja — definisi 2.3 revisi 7.
#
# Yang diuji: tiap jenis baru bisa di-append lewat Ledger.append_event dan
# terbaca kembali lewat events() dengan jenis yang benar; rantai hash tetap
# terverifikasi setelah append campuran jenis lama + baru; jenis tak dikenal
# tetap ditolak di Python (InvalidEventTypeError) dan di level DB (CHECK
# constraint menolak INSERT langsung via SQL); CHECK DB juga menerima tiap
# jenis baru lewat INSERT langsung (bukan hanya lewat Python).
#
# DB tes: sama seperti test_ledger.py (Postgres lokal, DSN dari env
# PUSPA_TEST_DSN atau default peer-auth). Tiap tes memakai tugas_id unik lalu
# membersihkan barisnya sendiri.

import os
import uuid

import psycopg
import pytest
from psycopg import errors as pg_errors

from puspa.ledger import GENESIS, JENIS_KEJADIAN, Ledger
from puspa.ledger.errors import InvalidEventTypeError

DSN = os.environ.get(
    "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
)
AGEN = "agen-tes"

#: 9 jenis baru revisi 7 (definisi 2.3, daftar harfiah).
JENIS_BARU = [
    "alur_diajukan",
    "alur_diaktifkan",
    "alur_dijeda",
    "pemicu_terpicu",
    "pemicu_terlewat",
    "modul_diajukan",
    "modul_diaktifkan",
    "laporan_salah",
    "persetujuan_data",
]

#: 22 jenis lama — harus tetap ada semua (tidak ada yang hilang/berubah).
JENIS_LAMA = [
    "tugas_dibuat",
    "rencana_dibuat",
    "subagen_dibuat",
    "handoff_diajukan",
    "handoff_diterima",
    "handoff_diserahkan",
    "handoff_ditolak",
    "handoff_kedaluwarsa",
    "detak",
    "tool_dipanggil",
    "tool_selesai",
    "bukti_dicatat",
    "pemeriksaan_selesai",
    "persetujuan_diminta",
    "persetujuan_diberikan",
    "persetujuan_ditolak",
    "pesan_user",
    "pesan_agen",
    "anggaran_habis",
    "tugas_selesai",
    "tugas_gagal",
    "tugas_dihentikan",
]


@pytest.fixture(scope="session")
def ledger():
    """Ledger + skema dibuat sekali per sesi tes (idempotent)."""
    led = Ledger(DSN)
    led.create_schema()
    return led


@pytest.fixture
def tugas_id(ledger):
    """tugas_id unik per tes; barisnya dibersihkan setelah tes selesai."""
    tid = f"tes-{uuid.uuid4().hex[:12]}"
    yield tid
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM kejadian WHERE tugas_id = %s", (tid,))


def test_himpunan_tertutup_31_jenis():
    assert len(JENIS_LAMA) == 22
    assert len(JENIS_BARU) == 9
    assert set(JENIS_LAMA).isdisjoint(JENIS_BARU)
    assert JENIS_KEJADIAN == set(JENIS_LAMA) | set(JENIS_BARU)
    assert len(JENIS_KEJADIAN) == 31


@pytest.mark.parametrize("jenis", JENIS_BARU)
def test_append_jenis_baru_dan_baca_kembali(ledger, tugas_id, jenis):
    ev = ledger.append_event(
        tugas_id=tugas_id, agen_id=AGEN, jenis=jenis, isi={"jenis": jenis}
    )
    assert ev["jenis"] == jenis
    assert ev["tugas_id"] == tugas_id
    assert ev["hash_sebelumnya"] == GENESIS  # kejadian pertama tugas ini
    assert len(ev["hash"]) == 64

    dibaca = ledger.events(tugas_id)
    assert len(dibaca) == 1
    assert dibaca[0] == ev
    # Filter jenis mengembalikan kejadian yang sama.
    assert [e["id"] for e in ledger.events(tugas_id, jenis=jenis)] == [ev["id"]]


def test_rantai_hash_campuran_lama_dan_baru_tetap_utuh(ledger, tugas_id):
    # Campuran jenis lama + baru dalam satu tugas, urut append.
    urutan = [
        "tugas_dibuat",
        "alur_diajukan",
        "alur_diaktifkan",
        "pemicu_terpicu",
        "modul_diajukan",
        "modul_diaktifkan",
        "detak",
        "pemicu_terlewat",
        "alur_dijeda",
        "laporan_salah",
        "persetujuan_data",
        "tugas_selesai",
    ]
    dibuat = [
        ledger.append_event(
            tugas_id=tugas_id, agen_id=AGEN, jenis=j, isi={"urutan": i}
        )
        for i, j in enumerate(urutan)
    ]
    for sebelum, sesudah in zip(dibuat, dibuat[1:]):
        assert sesudah["hash_sebelumnya"] == sebelum["hash"]
    assert [e["jenis"] for e in ledger.events(tugas_id)] == urutan

    ok, putus = ledger.verify_chain(tugas_id)
    assert ok is True
    assert putus == []


def test_jenis_tak_dikenal_ditolak_python_dan_db(ledger, tugas_id):
    # Level Python: append dan filter events menolak jenis sembarang.
    with pytest.raises(InvalidEventTypeError):
        ledger.append_event(
            tugas_id=tugas_id, agen_id=AGEN, jenis="jenis_ngawur", isi={}
        )
    with pytest.raises(InvalidEventTypeError):
        ledger.events(tugas_id, jenis="jenis_ngawur")
    assert ledger.events(tugas_id) == []

    # Level DB: INSERT langsung via SQL dengan jenis sembarang gagal
    # karena CHECK constraint — walau melewati validasi Python.
    with psycopg.connect(DSN, autocommit=True) as conn:
        with pytest.raises(pg_errors.CheckViolation):
            conn.execute(
                "INSERT INTO kejadian "
                "(tugas_id, agen_id, jenis, isi, hash_sebelumnya, hash) "
                "VALUES (%s, %s, 'jenis_ngawur', '{}'::jsonb, %s, %s)",
                (tugas_id, AGEN, GENESIS, uuid.uuid4().hex),
            )
    assert ledger.events(tugas_id) == []


@pytest.mark.parametrize("jenis", JENIS_BARU)
def test_check_db_menerima_jenis_baru_insert_langsung(ledger, tugas_id, jenis):
    # INSERT langsung via SQL (tanpa Ledger) dengan tiap jenis baru harus
    # lolos CHECK — membuktikan schema.sql, bukan hanya JENIS_KEJADIAN.
    with psycopg.connect(DSN, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO kejadian "
            "(tugas_id, agen_id, jenis, isi, hash_sebelumnya, hash) "
            "VALUES (%s, %s, %s, '{}'::jsonb, %s, %s) RETURNING id, jenis",
            (tugas_id, AGEN, jenis, GENESIS, uuid.uuid4().hex),
        ).fetchone()
    assert row[1] == jenis
    assert [e["jenis"] for e in ledger.events(tugas_id)] == [jenis]
