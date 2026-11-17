"""Tes Prosedural — lapis 3 memori (C55) + siklus hidup (3.3)."""

import uuid

import pytest

from puspa.memory import procedural

LANGKAH = ["buka situs pln", "masukkan nomor pelanggan", "baca jumlah tagihan"]


def test_tugas_berhasil_menjadi_prosedur_percobaan(make_user, test_dsn):
    u = make_user()
    proc = procedural.record_successful_task(
        u["user_id"], "cek_pln", LANGKAH, agen_id=u["agen_id"], dsn=test_dsn
    )
    assert proc["status"] == "percobaan"
    assert proc["jalan_sukses"] == 1
    assert proc["langkah"] == LANGKAH


def test_tugas_sama_tidak_menduplikasi_prosedur(make_user, test_dsn):
    u = make_user()
    procedural.record_successful_task(u["user_id"], "cek_pln", LANGKAH, agen_id=u["agen_id"], dsn=test_dsn)
    proc = procedural.record_successful_task(u["user_id"], "cek_pln", LANGKAH, agen_id=u["agen_id"], dsn=test_dsn)
    assert proc["jalan_sukses"] == 2
    assert len(procedural.list_procedures(u["user_id"], dsn=test_dsn)) == 1


def test_prosedur_terverifikasi_setelah_3_jalan_2_agen(make_user, test_dsn):
    u = make_user()
    agen2 = str(uuid.uuid4())
    proc = procedural.record_successful_task(
        u["user_id"], "cek_pln", LANGKAH, agen_id=u["agen_id"], dsn=test_dsn
    )
    proc = procedural.mark_run(proc["id"], u["user_id"], True, agen_id=agen2, dsn=test_dsn)
    assert proc["status"] == "percobaan"  # baru 2 jalan
    proc = procedural.mark_run(proc["id"], u["user_id"], True, agen_id=u["agen_id"], dsn=test_dsn)
    assert proc["status"] == "terverifikasi"
    assert proc["jalan_sukses"] == 3


def test_prosedur_rusak_setelah_2_gagal_berturut(make_user, test_dsn):
    u = make_user()
    proc = procedural.record_successful_task(u["user_id"], "cek_pln", LANGKAH, dsn=test_dsn)
    proc = procedural.mark_run(proc["id"], u["user_id"], False, dsn=test_dsn)
    assert proc["status"] == "percobaan"
    proc = procedural.mark_run(proc["id"], u["user_id"], False, dsn=test_dsn)
    assert proc["status"] == "rusak"
    assert proc["jalan_gagal_berturut"] == 2


def test_prosedur_tanpa_data_pribadi(make_user, test_dsn):
    """Prosedur adalah templat: nomor pelanggan tidak boleh masuk."""
    u = make_user()
    proc = procedural.record_successful_task(
        u["user_id"], "cek_pln", LANGKAH, dsn=test_dsn
    )
    assert "1234567890" not in str(proc["langkah"])
    assert proc["user_id"] == u["user_id"]


def test_prosedur_terisolasi_antar_pengguna(make_user, test_dsn):
    a, b = make_user(), make_user()
    proc = procedural.record_successful_task(a["user_id"], "cek_pln", LANGKAH, dsn=test_dsn)
    assert procedural.list_procedures(b["user_id"], dsn=test_dsn) == []
    with pytest.raises(KeyError):
        procedural.get_procedure(proc["id"], b["user_id"], dsn=test_dsn)
