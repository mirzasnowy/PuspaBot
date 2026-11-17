"""Tes registry agen (§2.2): Puspa wajib, maks 3, hapus ditolak."""

import pytest

from puspa.runtime.agents import (buat_agen, daftar_agen, ensure_puspa_agent,
                                  hapus_agen, AgentError)


def test_puspa_otomatis_dan_idempotent(make_user):
    u = make_user()
    agen_id = ensure_puspa_agent(u["user_id"])
    assert agen_id == u["agen_id"]
    lagi = ensure_puspa_agent(u["user_id"])
    assert lagi == agen_id
    daftar = daftar_agen(u["user_id"])
    assert len(daftar) == 1 and daftar[0]["wajib"] is True and daftar[0]["nama"] == "Puspa"


def test_maks_tiga_agen(make_user):
    u = make_user()
    buat_agen(u["user_id"], "Agen Riset", "riset", "mencari artikel")
    buat_agen(u["user_id"], "Agen Belanja", "belanja", "bandingkan harga")
    assert len(daftar_agen(u["user_id"])) == 3
    with pytest.raises(AgentError):
        buat_agen(u["user_id"], "Agen Keempat")


def test_hapus_puspa_ditolak(make_user, test_dsn):
    u = make_user()
    with pytest.raises(AgentError, match="tidak bisa dihapus"):
        hapus_agen(u["user_id"], u["agen_id"])
    assert len(daftar_agen(u["user_id"])) == 1
    # Penolakan di level DATABASE (bukan hanya API): DELETE langsung ditolak.
    import psycopg

    with pytest.raises(psycopg.errors.RaiseException):
        with psycopg.connect(test_dsn) as conn:
            conn.execute("DELETE FROM agen_meta WHERE agen_id = %s", (u["agen_id"],))
            conn.commit()
    assert len(daftar_agen(u["user_id"])) == 1


def test_hapus_agen_biasa_ok_dan_agen_orang_lain_ditolak(make_user):
    u1, u2 = make_user(), make_user()
    agen = buat_agen(u1["user_id"], "Agen Riset")
    with pytest.raises(AgentError):
        hapus_agen(u2["user_id"], agen["agen_id"])
    hapus_agen(u1["user_id"], agen["agen_id"])
    assert len(daftar_agen(u1["user_id"])) == 1


def test_agen_baru_mulai_tanpa_konektor(make_user):
    from puspa.connectors.grants import daftar_konektor

    u = make_user()
    agen = buat_agen(u["user_id"], "Agen Riset")
    assert daftar_konektor(u["user_id"], agen["agen_id"]) == []
    # Puspa mulai dengan ketiganya.
    assert {k["konektor"] for k in daftar_konektor(u["user_id"], u["agen_id"])} == {
        "gmail", "drive", "calendar"}
