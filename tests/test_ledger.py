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

from puspa.ledger import APP_ROLE, GENESIS, JENIS_KEJADIAN, Ledger
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
def tugas_id(ledger):
    """tugas_id unik per tes; barisnya dibersihkan setelah tes selesai."""
    tid = f"tes-{uuid.uuid4().hex[:12]}"
    yield tid
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM kejadian WHERE tugas_id = %s", (tid,))


def _append(ledger, tugas_id, jenis="detak", isi=None, **kwargs):
    return ledger.append_event(
        tugas_id=tugas_id,
        agen_id=AGEN,
        jenis=jenis,
        isi=isi if isi is not None else {"n": 1},
        **kwargs,
    )


def test_append_dan_baca_kembali(ledger, tugas_id):
    ev = _append(ledger, tugas_id, jenis="tugas_dibuat", isi={"tujuan": "cek harga"})
    assert ev["id"]
    assert ev["tugas_id"] == tugas_id
    assert ev["agen_id"] == AGEN
    assert ev["sub_agen_id"] is None
    assert ev["jenis"] == "tugas_dibuat"
    assert ev["isi"] == {"tujuan": "cek harga"}
    assert ev["waktu"] is not None  # waktu dari DB, bukan dari memori
    assert ev["hash_sebelumnya"] == GENESIS
    assert len(ev["hash"]) == 64  # sha256 hex

    dibaca = ledger.events(tugas_id)
    assert len(dibaca) == 1
    assert dibaca[0] == ev


def test_rantai_hash_utuh(ledger, tugas_id):
    pertama = _append(ledger, tugas_id, jenis="tugas_dibuat", isi={"a": 1})
    kedua = _append(
        ledger, tugas_id, jenis="rencana_dibuat", isi={"b": 2}, id_induk=pertama["id"]
    )
    ketiga = _append(ledger, tugas_id, jenis="detak", isi={"c": 3})

    assert kedua["hash_sebelumnya"] == pertama["hash"]
    assert ketiga["hash_sebelumnya"] == kedua["hash"]
    assert kedua["id_induk"] == pertama["id"]

    ok, putus = ledger.verify_chain(tugas_id)
    assert ok is True
    assert putus == []

    # Filter jenis mengembalikan hanya kejadian yang cocok.
    assert [e["id"] for e in ledger.events(tugas_id, jenis="detak")] == [ketiga["id"]]


def test_verify_chain_mendeteksi_perubahan_diam_diam(ledger, tugas_id):
    _append(ledger, tugas_id, jenis="tugas_dibuat", isi={"a": 1})
    korban = _append(ledger, tugas_id, jenis="bukti_dicatat", isi={"harga": 100})
    _append(ledger, tugas_id, jenis="tugas_selesai", isi={"ok": True})

    ok, _ = ledger.verify_chain(tugas_id)
    assert ok is True

    # Ubah satu isi lewat SQL langsung sebagai pemilik DB (superuser/owner
    # bisa UPDATE; peran aplikasi tidak — lihat tes izin di bawah).
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE kejadian SET isi = %s::jsonb WHERE id = %s",
            (json.dumps({"harga": 999}), korban["id"]),
        )

    ok, putus = ledger.verify_chain(tugas_id)
    assert ok is False
    assert putus == [korban["id"]]  # hanya kejadian yang diubah yang putus


def test_jenis_di_luar_himpunan_tertutup_ditolak(ledger, tugas_id):
    assert len(JENIS_KEJADIAN) == 31  # definisi 2.3 revisi 7: 22 lama + 9 baru
    with pytest.raises(InvalidEventTypeError):
        _append(ledger, tugas_id, jenis="jenis_ngawur", isi={})
    with pytest.raises(InvalidEventTypeError):
        ledger.events(tugas_id, jenis="jenis_ngawur")
    assert ledger.events(tugas_id) == []  # tidak ada baris yang masuk


def test_update_delete_ditolak_peran_aplikasi(ledger, tugas_id):
    ev = _append(ledger, tugas_id, jenis="tugas_dibuat", isi={"a": 1})
    # INSERT peran aplikasi diuji di tugas terpisah, supaya baris mentah itu
    # tidak mencemari rantai tugas yang sedang diverifikasi.
    tugas_sisip = f"{tugas_id}-sisip"

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"SET ROLE {APP_ROLE}")
        try:
            # SELECT dan INSERT diizinkan untuk peran aplikasi.
            row = conn.execute(
                "SELECT id FROM kejadian WHERE id = %s", (ev["id"],)
            ).fetchone()
            assert row is not None
            conn.execute(
                "INSERT INTO kejadian "
                "(tugas_id, agen_id, jenis, isi, hash_sebelumnya, hash) "
                "VALUES (%s, %s, 'detak', '{}'::jsonb, %s, %s)",
                (tugas_sisip, AGEN, GENESIS, uuid.uuid4().hex),
            )
            # UPDATE dan DELETE ditolak oleh izin DB.
            with pytest.raises(pg_errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE kejadian SET isi = '{}'::jsonb WHERE id = %s",
                    (ev["id"],),
                )
            with pytest.raises(pg_errors.InsufficientPrivilege):
                conn.execute("DELETE FROM kejadian WHERE id = %s", (ev["id"],))
        finally:
            conn.execute("RESET ROLE")

    # Baris asli tetap utuh dan rantai tetap valid.
    ok, _ = ledger.verify_chain(tugas_id)
    assert ok is True
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM kejadian WHERE tugas_id = %s", (tugas_sisip,))


def test_dua_tugas_punya_rantai_independen(ledger, tugas_id):
    tugas_lain = f"tes-{uuid.uuid4().hex[:12]}"
    try:
        a1 = _append(ledger, tugas_id, jenis="tugas_dibuat", isi={"tugas": "A"})
        b1 = _append(ledger, tugas_lain, jenis="tugas_dibuat", isi={"tugas": "B"})
        a2 = _append(ledger, tugas_id, jenis="detak", isi={"tugas": "A"})
        b2 = _append(ledger, tugas_lain, jenis="detak", isi={"tugas": "B"})

        assert a1["hash_sebelumnya"] == GENESIS
        assert b1["hash_sebelumnya"] == GENESIS
        assert a2["hash_sebelumnya"] == a1["hash"]  # bukan hash tugas B
        assert b2["hash_sebelumnya"] == b1["hash"]  # bukan hash tugas A

        ok_a, _ = ledger.verify_chain(tugas_id)
        ok_b, _ = ledger.verify_chain(tugas_lain)
        assert ok_a is True
        assert ok_b is True
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM kejadian WHERE tugas_id = %s", (tugas_lain,))


def test_tombstone_hapus_tugas_tanpa_update(ledger, tugas_id):
    _append(ledger, tugas_id, jenis="tugas_dibuat", isi={"tujuan": "dibuang"})
    _append(ledger, tugas_id, jenis="detak", isi={"n": 1})
    kubur = _append(
        ledger, tugas_id, jenis="tugas_dihentikan", isi={"dihapus": True}
    )

    # Hapus = append tombstone; rantai hash tetap utuh, tidak ada UPDATE.
    ok, _ = ledger.verify_chain(tugas_id)
    assert ok is True
    assert kubur["isi"] == {"dihapus": True}
    assert ledger.events(tugas_id)[-1]["jenis"] == "tugas_dihentikan"
