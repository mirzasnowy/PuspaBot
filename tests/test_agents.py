"""Tes registry agen (§2.2): Puspa wajib, maks 3, hapus ditolak."""

import pytest

from puspa.runtime.agents import (create_agent, items_agent, ensure_puspa_agent,
                                  delete_agent, AgentError)


def test_puspa_automatic_and_idempotent(make_user):
    u = make_user()
    agent_id = ensure_puspa_agent(u["user_id"])
    assert agent_id == u["agent_id"]
    lagi = ensure_puspa_agent(u["user_id"])
    assert lagi == agent_id
    items = items_agent(u["user_id"])
    assert len(items) == 1 and items[0]["wajib"] is True and items[0]["name"] == "Puspa"


def test_maks_tiga_agent(make_user):
    u = make_user()
    create_agent(u["user_id"], "Agen Riset", "riset", "mencari artikel")
    create_agent(u["user_id"], "Agen Belanja", "belanja", "bandingkan harga")
    assert len(items_agent(u["user_id"])) == 3
    with pytest.raises(AgentError):
        create_agent(u["user_id"], "Agen Keempat")


def test_delete_puspa_rejected(make_user, test_dsn):
    u = make_user()
    with pytest.raises(AgentError, match="tidak bisa dihapus"):
        delete_agent(u["user_id"], u["agent_id"])
    assert len(items_agent(u["user_id"])) == 1
    # Penolakan di level DATABASE (bukan hanya API): DELETE langsung ditolak.
    import psycopg

    with pytest.raises(psycopg.errors.RaiseException):
        with psycopg.connect(test_dsn) as conn:
            conn.execute("DELETE FROM agent_meta WHERE agent_id = %s", (u["agent_id"],))
            conn.commit()
    assert len(items_agent(u["user_id"])) == 1


def test_delete_agent_biasa_ok_and_agent_orang_lain_rejected(make_user):
    u1, u2 = make_user(), make_user()
    agent = create_agent(u1["user_id"], "Agen Riset")
    with pytest.raises(AgentError):
        delete_agent(u2["user_id"], agent["agent_id"])
    delete_agent(u1["user_id"], agent["agent_id"])
    assert len(items_agent(u1["user_id"])) == 1


def test_agent_new_mulai_without_connector(make_user):
    from puspa.connectors.grants import items_connector

    u = make_user()
    agent = create_agent(u["user_id"], "Agen Riset")
    assert items_connector(u["user_id"], agent["agent_id"]) == []
    # Puspa mulai dengan ketiganya.
    assert {k["connectors"] for k in items_connector(u["user_id"], u["agent_id"])} == {
        "gmail", "drive", "calendar"}
