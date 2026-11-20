# Tes 9 event_type kejadian baru Buku Kerja — definisi 2.3 revisi 7.
#
# Yang diuji: tiap event_type baru bisa di-append lewat Ledger.append_event dan
# terbaca kembali lewat events() dengan event_type yang benar; rantai hash tetap
# terverifikasi setelah append campuran event_type lama + baru; event_type tak dikenal
# tetap ditolak di Python (InvalidEventTypeError) dan di level DB (CHECK
# constraint menolak INSERT langsung via SQL); CHECK DB juga menerima tiap
# event_type baru lewat INSERT langsung (bukan hanya lewat Python).
#
# DB tes: sama seperti test_ledger.py (Postgres lokal, DSN dari env
# PUSPA_TEST_DSN atau default peer-auth). Tiap tes memakai tugas_id unik lalu
# membersihkan barisnya sendiri.

import itertools
import os
import uuid

import psycopg
import pytest
from psycopg import errors as pg_errors

from puspa.ledger import EVENT_TYPES, GENESIS, Ledger
from puspa.ledger.errors import InvalidEventTypeError

DSN = os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
AGEN = "agen-tes"

#: 9 event_type baru revisi 7 (definisi 2.3, daftar harfiah).
EVENT_TYPES_NEW = [
    "workflow_proposed",
    "workflow_activated",
    "workflow_paused",
    "trigger_fired",
    "trigger_missed",
    "module_proposed",
    "module_activated",
    "false_report",
    "data_consent",
]

#: 2 event_type ambil alih (P-09): takeover adalah event, bukan
#: edit — ``takeover_started`` saat tombol ditekan, ``takeover_released``
#: saat kendali dilepas. Ditambah lewat migrasi 0026 (CHECK
#: diperluas), bukan lewat revisi 7.
EVENT_TYPES_P09 = [
    "takeover_started",
    "takeover_released",
]

#: 22 event_type lama — harus tetap ada semua (tidak ada yang hilang/berubah).
EVENT_TYPES_OLD = [
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


@pytest.fixture(scope="session", autouse=True)
def _takeover_migration():
    """Apply migrations 0026-0027 (P-09 types in the DB CHECK).

    The ``ledger`` fixture below applies only migration 0001,
    whose CHECK lists the 31 revision-7 types. The two P-09
    types only pass the DB CHECK once 0026 has landed — and
    0027 drops the duplicate inline CHECK 0026 could not
    reach — so this fixture applies both (idempotent; the test
    database usually records them already).
    """
    from puspa.migrations import apply_migrations

    apply_migrations(DSN, target="0027")


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


def test_closed_set_of_33_event_types():
    assert len(EVENT_TYPES_OLD) == 22
    assert len(EVENT_TYPES_NEW) == 9
    assert len(EVENT_TYPES_P09) == 2
    assert set(EVENT_TYPES_OLD).isdisjoint(EVENT_TYPES_NEW)
    assert set(EVENT_TYPES_OLD).isdisjoint(EVENT_TYPES_P09)
    assert set(EVENT_TYPES_NEW).isdisjoint(EVENT_TYPES_P09)
    assert set(EVENT_TYPES_OLD) | set(EVENT_TYPES_NEW) | set(EVENT_TYPES_P09) == EVENT_TYPES
    assert len(EVENT_TYPES) == 33


@pytest.mark.parametrize("event_type", EVENT_TYPES_NEW + EVENT_TYPES_P09)
def test_append_new_event_type_and_read_back(ledger, task_id, event_type):
    ev = ledger.append_event(
        task_id=task_id, agent_id=AGEN, event_type=event_type, content={"type": event_type}
    )
    assert ev["type"] == event_type
    assert ev["task_id"] == task_id
    assert ev["previous_hash"] == GENESIS  # kejadian pertama tugas ini
    assert len(ev["hash"]) == 64

    read = ledger.events(task_id)
    assert len(read) == 1
    assert read[0] == ev
    # Filter event_type mengembalikan kejadian yang sama.
    assert [e["id"] for e in ledger.events(task_id, event_type=event_type)] == [ev["id"]]


def test_hash_chain_mixed_old_and_new_still_intact(ledger, task_id):
    # Campuran event_type lama + baru dalam satu tugas, urut append.
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
        "false_report",
        "data_consent",
        "task_completed",
    ]
    created_at = [
        ledger.append_event(task_id=task_id, agent_id=AGEN, event_type=j, content={"urutan": i})
        for i, j in enumerate(urutan)
    ]
    for before, sesudah in itertools.pairwise(created_at):
        assert sesudah["previous_hash"] == before["hash"]
    assert [e["type"] for e in ledger.events(task_id)] == urutan

    ok, putus = ledger.verify_chain(task_id)
    assert ok is True
    assert putus == []


def test_event_type_unknown_rejected_python_and_db(ledger, task_id):
    # Level Python: append dan filter events menolak event_type sembarang.
    with pytest.raises(InvalidEventTypeError):
        ledger.append_event(task_id=task_id, agent_id=AGEN, event_type="bogus_type", content={})
    with pytest.raises(InvalidEventTypeError):
        ledger.events(task_id, event_type="bogus_type")
    assert ledger.events(task_id) == []

    # Level DB: INSERT langsung via SQL dengan event_type sembarang gagal
    # karena CHECK constraint — walau melewati validasi Python.
    with psycopg.connect(DSN, autocommit=True) as conn, pytest.raises(pg_errors.CheckViolation):
        conn.execute(
            "INSERT INTO events "
            "(task_id, agent_id, type, content, previous_hash, hash) "
            "VALUES (%s, %s, 'bogus_type', '{}'::jsonb, %s, %s)",
            (task_id, AGEN, GENESIS, uuid.uuid4().hex),
        )
    assert ledger.events(task_id) == []


@pytest.mark.parametrize("event_type", EVENT_TYPES_NEW + EVENT_TYPES_P09)
def test_check_db_accepts_new_event_type_direct_insert(ledger, task_id, event_type):
    # INSERT langsung via SQL (tanpa Ledger) dengan tiap event_type baru harus
    # lolos CHECK — membuktikan schema.sql, bukan hanya JENIS_KEJADIAN.
    with psycopg.connect(DSN, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO events "
            "(task_id, agent_id, type, content, previous_hash, hash) "
            "VALUES (%s, %s, %s, '{}'::jsonb, %s, %s) RETURNING id, type",
            (task_id, AGEN, event_type, GENESIS, uuid.uuid4().hex),
        ).fetchone()
    assert row[1] == event_type
    assert [e["type"] for e in ledger.events(task_id)] == [event_type]
