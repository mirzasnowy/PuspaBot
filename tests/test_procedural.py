"""Tes Prosedural — lapis 3 memori (C55) + siklus hidup (3.3)."""

import uuid

import pytest

from puspa.memory import procedural

LANGKAH = ["buka situs pln", "masukkan nomor pelanggan", "baca jumlah tagihan"]


def test_task_successful_menjadi_prosedur_trial(make_user, test_dsn):
    u = make_user()
    proc = procedural.record_successful_task(
        u["user_id"], "check_pln", LANGKAH, agent_id=u["agent_id"], dsn=test_dsn
    )
    assert proc["status"] == "trial"
    assert proc["successful_runs"] == 1
    assert proc["step"] == LANGKAH


def test_task_same_not_menduplikasi_prosedur(make_user, test_dsn):
    u = make_user()
    procedural.record_successful_task(u["user_id"], "check_pln", LANGKAH, agent_id=u["agent_id"], dsn=test_dsn)
    proc = procedural.record_successful_task(u["user_id"], "check_pln", LANGKAH, agent_id=u["agent_id"], dsn=test_dsn)
    assert proc["successful_runs"] == 2
    assert len(procedural.list_procedures(u["user_id"], dsn=test_dsn)) == 1


def test_prosedur_verified_setelah_3_jalan_2_agent(make_user, test_dsn):
    u = make_user()
    agen2 = str(uuid.uuid4())
    proc = procedural.record_successful_task(
        u["user_id"], "check_pln", LANGKAH, agent_id=u["agent_id"], dsn=test_dsn
    )
    proc = procedural.mark_run(proc["id"], u["user_id"], True, agent_id=agen2, dsn=test_dsn)
    assert proc["status"] == "trial"  # baru 2 jalan
    proc = procedural.mark_run(proc["id"], u["user_id"], True, agent_id=u["agent_id"], dsn=test_dsn)
    assert proc["status"] == "verified"
    assert proc["successful_runs"] == 3


def test_prosedur_broken_setelah_2_failed_berturut(make_user, test_dsn):
    u = make_user()
    proc = procedural.record_successful_task(u["user_id"], "check_pln", LANGKAH, dsn=test_dsn)
    proc = procedural.mark_run(proc["id"], u["user_id"], False, dsn=test_dsn)
    assert proc["status"] == "trial"
    proc = procedural.mark_run(proc["id"], u["user_id"], False, dsn=test_dsn)
    assert proc["status"] == "broken"
    assert proc["consecutive_failed_runs"] == 2


def test_prosedur_without_data_pribadi(make_user, test_dsn):
    """Prosedur adalah templat: nomor pelanggan tidak boleh masuk."""
    u = make_user()
    proc = procedural.record_successful_task(
        u["user_id"], "check_pln", LANGKAH, dsn=test_dsn
    )
    assert "1234567890" not in str(proc["step"])
    assert proc["user_id"] == u["user_id"]


def test_prosedur_terisolasi_antar_user(make_user, test_dsn):
    a, b = make_user(), make_user()
    proc = procedural.record_successful_task(a["user_id"], "check_pln", LANGKAH, dsn=test_dsn)
    assert procedural.list_procedures(b["user_id"], dsn=test_dsn) == []
    with pytest.raises(KeyError):
        procedural.get_procedure(proc["id"], b["user_id"], dsn=test_dsn)
