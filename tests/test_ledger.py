# Tes Buku Kerja — definisi 2.3 + CONTRACTS.md bagian "Buku Kerja".
#
# Yang diuji: append + baca kembali, rantai hash utuh, verify_chain mendeteksi
# perubahan diam-diam, jenis di luar himpunan tertutup ditolak, UPDATE/DELETE
# ditolak oleh izin DB peran aplikasi, dua tugas punya rantai independen,
# dan tombstone hapus tugas (append tugas_dihentikan, tanpa UPDATE).
#
# DB tes: Postgres lokal, DSN dari env PUSPA_TEST_DSN atau default peer-auth
# (dbname=puspa_test user=root host=/var/run/postgresql). Tiap tes memakai
# tugas_id unik lalu membersihkan barisnya sendiri, supaya tidak mengganggu
# tes paket lain yang berbagi DB yang sama.

import json
import os
import uuid

import psycopg
import pytest
from psycopg import errors as pg_errors

from puspa.ledger import APP_ROLE, EVENT_TYPES, GENESIS, Ledger
from puspa.ledger.errors import InvalidEventTypeError

DSN = os.environ.get(
    "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
)
AGEN = "agen-tes"


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


def _append(ledger, task_id, event_type="heartbeat", content=None, **kwargs):
    return ledger.append_event(
        task_id=task_id,
        agent_id=AGEN,
        event_type=event_type,
        content=content if content is not None else {"n": 1},
        **kwargs,
    )


def test_append_and_read_kembali(ledger, task_id):
    ev = _append(ledger, task_id, event_type="task_created", content={"purpose": "cek harga"})
    assert ev["id"]
    assert ev["task_id"] == task_id
    assert ev["agent_id"] == AGEN
    assert ev["subagent_id"] is None
    assert ev["type"] == "task_created"
    assert ev["content"] == {"purpose": "cek harga"}
    assert ev["created_at"] is not None  # waktu dari DB, bukan dari memori
    assert ev["previous_hash"] == GENESIS
    assert len(ev["hash"]) == 64  # sha256 hex

    dibaca = ledger.events(task_id)
    assert len(dibaca) == 1
    assert dibaca[0] == ev


def test_rantai_hash_utuh(ledger, task_id):
    pertama = _append(ledger, task_id, event_type="task_created", content={"a": 1})
    kedua = _append(
        ledger, task_id, event_type="plan_created", content={"b": 2}, parent_id=pertama["id"]
    )
    ketiga = _append(ledger, task_id, event_type="heartbeat", content={"c": 3})

    assert kedua["previous_hash"] == pertama["hash"]
    assert ketiga["previous_hash"] == kedua["hash"]
    assert kedua["parent_id"] == pertama["id"]

    ok, putus = ledger.verify_chain(task_id)
    assert ok is True
    assert putus == []

    # Filter jenis mengembalikan hanya kejadian yang cocok.
    assert [e["id"] for e in ledger.events(task_id, event_type="heartbeat")] == [ketiga["id"]]


def test_verify_chain_mendeteksi_perubahan_diam_diam(ledger, task_id):
    _append(ledger, task_id, event_type="task_created", content={"a": 1})
    korban = _append(ledger, task_id, event_type="evidence_recorded", content={"price": 100})
    _append(ledger, task_id, event_type="task_completed", content={"ok": True})

    ok, _ = ledger.verify_chain(task_id)
    assert ok is True

    # Ubah satu isi lewat SQL langsung sebagai pemilik DB (superuser/owner
    # bisa UPDATE; peran aplikasi tidak — lihat tes izin di bawah).
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE events SET content = %s::jsonb WHERE id = %s",
            (json.dumps({"price": 999}), korban["id"]),
        )

    ok, putus = ledger.verify_chain(task_id)
    assert ok is False
    assert putus == [korban["id"]]  # hanya kejadian yang diubah yang putus


def test_jenis_di_luar_himpunan_tertutup_rejected(ledger, task_id):
    assert len(EVENT_TYPES) == 31  # definisi 2.3 revisi 7: 22 lama + 9 baru
    with pytest.raises(InvalidEventTypeError):
        _append(ledger, task_id, event_type="bogus_type", content={})
    with pytest.raises(InvalidEventTypeError):
        ledger.events(task_id, event_type="bogus_type")
    assert ledger.events(task_id) == []  # tidak ada baris yang masuk


def test_update_delete_rejected_peran_aplikasi(ledger, task_id):
    ev = _append(ledger, task_id, event_type="task_created", content={"a": 1})
    # INSERT peran aplikasi diuji di tugas terpisah, supaya baris mentah itu
    # tidak mencemari rantai tugas yang sedang diverifikasi.
    task_sisip = f"{task_id}-sisip"

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"SET ROLE {APP_ROLE}")
        try:
            # SELECT dan INSERT diizinkan untuk peran aplikasi.
            row = conn.execute(
                "SELECT id FROM events WHERE id = %s", (ev["id"],)
            ).fetchone()
            assert row is not None
            conn.execute(
                "INSERT INTO events "
                "(task_id, agent_id, type, content, previous_hash, hash) "
                "VALUES (%s, %s, 'heartbeat', '{}'::jsonb, %s, %s)",
                (task_sisip, AGEN, GENESIS, uuid.uuid4().hex),
            )
            # UPDATE dan DELETE ditolak oleh izin DB.
            with pytest.raises(pg_errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE events SET content = '{}'::jsonb WHERE id = %s",
                    (ev["id"],),
                )
            with pytest.raises(pg_errors.InsufficientPrivilege):
                conn.execute("DELETE FROM events WHERE id = %s", (ev["id"],))
        finally:
            conn.execute("RESET ROLE")

    # Baris asli tetap utuh dan rantai tetap valid.
    ok, _ = ledger.verify_chain(task_id)
    assert ok is True
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM events WHERE task_id = %s", (task_sisip,))


def test_dua_task_punya_rantai_independen(ledger, task_id):
    task_lain = f"tes-{uuid.uuid4().hex[:12]}"
    try:
        a1 = _append(ledger, task_id, event_type="task_created", content={"tasks": "A"})
        b1 = _append(ledger, task_lain, event_type="task_created", content={"tasks": "B"})
        a2 = _append(ledger, task_id, event_type="heartbeat", content={"tasks": "A"})
        b2 = _append(ledger, task_lain, event_type="heartbeat", content={"tasks": "B"})

        assert a1["previous_hash"] == GENESIS
        assert b1["previous_hash"] == GENESIS
        assert a2["previous_hash"] == a1["hash"]  # bukan hash tugas B
        assert b2["previous_hash"] == b1["hash"]  # bukan hash tugas A

        ok_a, _ = ledger.verify_chain(task_id)
        ok_b, _ = ledger.verify_chain(task_lain)
        assert ok_a is True
        assert ok_b is True
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM events WHERE task_id = %s", (task_lain,))


def test_tombstone_delete_task_without_update(ledger, task_id):
    _append(ledger, task_id, event_type="task_created", content={"purpose": "dibuang"})
    _append(ledger, task_id, event_type="heartbeat", content={"n": 1})
    kubur = _append(
        ledger, task_id, event_type="task_stopped", content={"deleted": True}
    )

    # Hapus = append tombstone; rantai hash tetap utuh, tidak ada UPDATE.
    ok, _ = ledger.verify_chain(task_id)
    assert ok is True
    assert kubur["content"] == {"deleted": True}
    assert ledger.events(task_id)[-1]["type"] == "task_stopped"
