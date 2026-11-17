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

from puspa.ledger import GENESIS, JENIS_EVENT, Ledger
from puspa.ledger.errors import InvalidEventTypeError

DSN = os.environ.get(
    "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
)
AGEN = "agen-tes"

#: 9 jenis baru revisi 7 (definisi 2.3, daftar harfiah).
JENIS_NEW = [
    "workflow_proposed",
    "workflow_activated",
    "workflow_paused",
    "trigger_fired",
    "trigger_missed",
    "module_proposed",
    "module_activated",
    "false_reports",
    "data_consents",
]

#: 22 jenis lama — harus tetap ada semua (tidak ada yang hilang/berubah).
JENIS_OLD = [
    "task_created",
    "plan_created",
    "subagent_created",
    "handoff_proposed",
    "handoff_accepted",
    "handoff_handed_over",
    "handoff_rejected",
    "handoff_expired",
    "heartbeat",
    "tool_called",
    "tool_completed",
    "evidence_recorded",
    "verification_completed",
    "approval_requested",
    "approval_granted",
    "approval_rejected",
    "user_message",
    "agent_message",
    "budget_exhausted",
    "task_completed",
    "task_failed",
    "task_stopped",
]


@pytest.fixture(scope="session")
def ledger():
    """Ledger + skema dibuat sekali per sesi tes (idempotent)."""
    led = Ledger(DSN)
    led.create_schema()
    return led


@pytest.fixture
def task_id(ledger):
    """tugas_id unik per tes; barisnya dibersihkan setelah tes selesai."""
    tid = f"tes-{uuid.uuid4().hex[:12]}"
    yield tid
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM events WHERE task_id = %s", (tid,))


def test_himpunan_tertutup_31_jenis():
    assert len(JENIS_OLD) == 22
    assert len(JENIS_NEW) == 9
    assert set(JENIS_OLD).isdisjoint(JENIS_NEW)
    assert JENIS_EVENT == set(JENIS_OLD) | set(JENIS_NEW)
    assert len(JENIS_EVENT) == 31


@pytest.mark.parametrize("type", JENIS_NEW)
def test_append_jenis_new_and_read_kembali(ledger, task_id, jenis):
    ev = ledger.append_event(
        task_id=task_id, agent_id=AGEN, jenis=jenis, content={"type": jenis}
    )
    assert ev["type"] == jenis
    assert ev["task_id"] == task_id
    assert ev["previous_hash"] == GENESIS  # kejadian pertama tugas ini
    assert len(ev["hash"]) == 64

    dibaca = ledger.events(task_id)
    assert len(dibaca) == 1
    assert dibaca[0] == ev
    # Filter jenis mengembalikan kejadian yang sama.
    assert [e["id"] for e in ledger.events(task_id, jenis=jenis)] == [ev["id"]]


def test_rantai_hash_campuran_old_and_new_tetap_utuh(ledger, task_id):
    # Campuran jenis lama + baru dalam satu tugas, urut append.
    urutan = [
        "task_created",
        "workflow_proposed",
        "workflow_activated",
        "trigger_fired",
        "module_proposed",
        "module_activated",
        "heartbeat",
        "trigger_missed",
        "workflow_paused",
        "false_reports",
        "data_consents",
        "task_completed",
    ]
    created_at = [
        ledger.append_event(
            task_id=task_id, agent_id=AGEN, jenis=j, content={"urutan": i}
        )
        for i, j in enumerate(urutan)
    ]
    for sebelum, sesudah in zip(created_at, created_at[1:]):
        assert sesudah["previous_hash"] == sebelum["hash"]
    assert [e["type"] for e in ledger.events(task_id)] == urutan

    ok, putus = ledger.verify_chain(task_id)
    assert ok is True
    assert putus == []


def test_jenis_tak_dikenal_rejected_python_and_db(ledger, task_id):
    # Level Python: append dan filter events menolak jenis sembarang.
    with pytest.raises(InvalidEventTypeError):
        ledger.append_event(
            task_id=task_id, agent_id=AGEN, jenis="jenis_ngawur", content={}
        )
    with pytest.raises(InvalidEventTypeError):
        ledger.events(task_id, jenis="jenis_ngawur")
    assert ledger.events(task_id) == []

    # Level DB: INSERT langsung via SQL dengan jenis sembarang gagal
    # karena CHECK constraint — walau melewati validasi Python.
    with psycopg.connect(DSN, autocommit=True) as conn:
        with pytest.raises(pg_errors.CheckViolation):
            conn.execute(
                "INSERT INTO events "
                "(task_id, agent_id, type, content, previous_hash, hash) "
                "VALUES (%s, %s, 'jenis_ngawur', '{}'::jsonb, %s, %s)",
                (task_id, AGEN, GENESIS, uuid.uuid4().hex),
            )
    assert ledger.events(task_id) == []


@pytest.mark.parametrize("type", JENIS_NEW)
def test_check_db_menerima_jenis_new_insert_langsung(ledger, task_id, jenis):
    # INSERT langsung via SQL (tanpa Ledger) dengan tiap jenis baru harus
    # lolos CHECK — membuktikan schema.sql, bukan hanya JENIS_KEJADIAN.
    with psycopg.connect(DSN, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO events "
            "(task_id, agent_id, type, content, previous_hash, hash) "
            "VALUES (%s, %s, %s, '{}'::jsonb, %s, %s) RETURNING id, type",
            (task_id, AGEN, jenis, GENESIS, uuid.uuid4().hex),
        ).fetchone()
    assert row[1] == jenis
    assert [e["type"] for e in ledger.events(task_id)] == [jenis]
