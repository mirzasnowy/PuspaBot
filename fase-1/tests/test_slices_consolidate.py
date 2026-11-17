"""Tes Irisan memori (C57) + Konsolidasi saat sepi (C60)."""

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from puspa.memory import consolidate, episodic, procedural, profile, slices


def test_irisan_hanya_kolom_yang_diizinkan(make_user, test_dsn):
    u = make_user()
    profile.add_fact(u["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    episodic.record_episode(u["user_id"], "suka kopi tubruk", "pesan_user", dsn=test_dsn)
    data = slices.grant_slice(
        u["user_id"], u["agen_id"], str(uuid.uuid4()), "kopi",
        ["episodik"], "token-123", dsn=test_dsn,
    )
    assert set(data) == {"episodik", "_irisan_id", "_dicatat_pada"}
    assert data["episodik"] and "kopi" in data["episodik"][0]["isi"]


def test_irisan_ditolak_kalau_kolom_tidak_diizinkan(make_user, test_dsn):
    u = make_user()
    with pytest.raises(ValueError):
        slices.grant_slice(
            u["user_id"], u["agen_id"], str(uuid.uuid4()), "kopi",
            ["vault"], "token-123", dsn=test_dsn,
        )
    with pytest.raises(ValueError):
        slices.grant_slice(
            u["user_id"], u["agen_id"], str(uuid.uuid4()), "kopi",
            [], "token-123", dsn=test_dsn,
        )


def test_pemberian_irisan_dicatat_dan_bisa_diaudit(make_user, test_dsn):
    u = make_user()
    sub = str(uuid.uuid4())
    slices.grant_slice(
        u["user_id"], u["agen_id"], sub, "nama",
        ["profil"], "token-abc", dsn=test_dsn,
    )
    grants = slices.list_grants(u["user_id"], dsn=test_dsn)
    assert len(grants) == 1
    assert grants[0]["sub_agen_id"] == sub
    assert grants[0]["kolom"] == ["profil"]
    assert grants[0]["token_ref"] == "token-abc"


def test_konsolidasi_meringkas_dan_menunjuk_kejadian_asal(make_user, test_dsn, ledger):
    u = make_user()
    tugas_id = str(uuid.uuid4())
    ev1 = ledger.append_event(
        tugas_id=tugas_id, agen_id=u["agen_id"], jenis="pesan_user",
        isi={"teks": "cek pln"},
    )
    ev2 = ledger.append_event(
        tugas_id=tugas_id, agen_id=u["agen_id"], jenis="tugas_selesai",
        isi={"hasil": "cek_pln: berhasil"},
    )
    # Buat episode "lama" (45 hari) dengan kejadian asal yang sama.
    for ev, jenis, isi in (
        (ev1, "pesan_user", "cek tagihan pln nomor pelanggan"),
        (ev2, "tugas_selesai", "cek_pln: berhasil, tagihan terbaca"),
    ):
        episodic.record_episode(
            u["user_id"], isi, jenis, tugas_id=tugas_id,
            kejadian_id=str(ev["id"]), dsn=test_dsn,
        )
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "UPDATE episodik SET dibuat = now() - interval '45 days' WHERE user_id = %s",
            (u["user_id"],),
        )
        conn.commit()
    hasil = consolidate.consolidate_idle(u["user_id"], older_than_days=30, dsn=test_dsn)
    assert hasil["ringkasan_dibuat"] == 1
    assert hasil["prosedur_dipromosikan"] == 1
    ringkasan = consolidate.list_summaries(u["user_id"], dsn=test_dsn)
    assert len(ringkasan) == 1
    assert set(ringkasan[0]["kejadian_ids"]) == {str(ev1["id"]), str(ev2["id"])}
    assert "cek_pln" in ringkasan[0]["ringkasan"]
    procs = procedural.list_procedures(u["user_id"], dsn=test_dsn)
    assert len(procs) == 1 and procs[0]["status"] == "percobaan"


def test_konsolidasi_tidak_mengulang_episode_yang_sudah_diringkas(make_user, test_dsn):
    u = make_user()
    tugas_id = str(uuid.uuid4())
    episodic.record_episode(
        u["user_id"], "cek pln berhasil", "tugas_selesai",
        tugas_id=tugas_id, kejadian_id=str(uuid.uuid4()), dsn=test_dsn,
    )
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "UPDATE episodik SET dibuat = now() - interval '45 days' WHERE user_id = %s",
            (u["user_id"],),
        )
        conn.commit()
    assert consolidate.consolidate_idle(u["user_id"], dsn=test_dsn)["ringkasan_dibuat"] == 1
    assert consolidate.consolidate_idle(u["user_id"], dsn=test_dsn)["ringkasan_dibuat"] == 0


def test_konsolidasi_melewatkan_episode_baru(make_user, test_dsn):
    u = make_user()
    episodic.record_episode(
        u["user_id"], "cek pln berhasil", "tugas_selesai",
        tugas_id=str(uuid.uuid4()), kejadian_id=str(uuid.uuid4()), dsn=test_dsn,
    )
    hasil = consolidate.consolidate_idle(u["user_id"], older_than_days=30, dsn=test_dsn)
    assert hasil["ringkasan_dibuat"] == 0
