"""Tes Profil — lapis 1 memori (C53, C58, C59)."""

import pytest

from puspa.memory import profile


def test_add_fact_diucapkan_login_profile(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    assert fact["status"] == "active"
    assert fact["konflik_dengan"] is None
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {"name": "Nero"}


def test_fact_inferred_login_proposed_bukan_profile(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(
        u["user_id"], "suka", "kopi", "inferred", dsn=test_dsn
    )
    assert fact["status"] == "proposed"
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {}
    assert [p["key"] for p in profile.list_proposals(u["user_id"], dsn=test_dsn)] == ["suka"]


def test_konfirmasi_proposed_login_profile(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(u["user_id"], "suka", "kopi", "inferred", dsn=test_dsn)
    confirmed = profile.confirm_proposal(fact["id"], u["user_id"], dsn=test_dsn)
    assert confirmed["status"] == "active"
    assert confirmed["source"] == "user_confirmed"
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {"suka": "kopi"}
    assert profile.list_proposals(u["user_id"], dsn=test_dsn) == []


def test_tolak_proposed_tetap_tercatat(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(u["user_id"], "suka", "kopi", "inferred", dsn=test_dsn)
    rejected = profile.reject_proposal(fact["id"], u["user_id"], dsn=test_dsn)
    assert rejected["status"] == "rejected"
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {}
    with pytest.raises(KeyError):
        profile.confirm_proposal(fact["id"], u["user_id"], dsn=test_dsn)


def test_fact_new_not_menimpa_diam_diam(make_user, test_dsn):
    """Konflik ditandai: versi lama diganti tapi riwayat tetap ada."""
    u = make_user()
    old = profile.add_fact(u["user_id"], "alamat", "Jakarta", "user_stated", dsn=test_dsn)
    new = profile.add_fact(u["user_id"], "alamat", "Bandung", "user_confirmed", dsn=test_dsn)
    assert new["konflik_dengan"] == old["id"]
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {"alamat": "Bandung"}
    riwayat = profile.list_facts(u["user_id"], key="alamat", include_history=True, dsn=test_dsn)
    assert [f["status"] for f in riwayat] == ["replaced", "active"]
    assert riwayat[0]["value"] == "Jakarta"  # versi lama tetap bisa dilihat


def test_koreksi_and_delete_fact(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    profile.correct_fact(u["user_id"], "name", "Nero Mirza", dsn=test_dsn)
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {"name": "Nero Mirza"}
    active = profile.list_facts(u["user_id"], key="name", dsn=test_dsn)
    deleted = profile.delete_fact(active[0]["id"], u["user_id"], dsn=test_dsn)
    assert deleted["status"] == "rejected"
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {}


def test_profile_inti_terlihat_all_agent_fact_agent_not(make_user, test_dsn):
    u = make_user()
    import uuid

    agent_lain = str(uuid.uuid4())
    profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    profile.add_fact(
        u["user_id"], "task_rahasia", "X", "user_stated",
        agent_id=u["agent_id"], dsn=test_dsn,
    )
    assert profile.get_profile(u["user_id"], agent_id=agent_lain, dsn=test_dsn) == {"name": "Nero"}
    assert profile.get_profile(u["user_id"], agent_id=u["agent_id"], dsn=test_dsn) == {
        "name": "Nero",
        "task_rahasia": "X",
    }


def test_isolasi_antar_user(make_user, test_dsn):
    a, b = make_user(), make_user()
    profile.add_fact(a["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    assert profile.get_profile(b["user_id"], dsn=test_dsn) == {}
    assert profile.list_facts(b["user_id"], dsn=test_dsn) == []
    with pytest.raises(KeyError):
        profile.confirm_proposal("00000000-0000-0000-0000-000000000000", b["user_id"], dsn=test_dsn)


def test_source_not_dikenal_rejected(make_user, test_dsn):
    u = make_user()
    with pytest.raises(ValueError):
        profile.add_fact(u["user_id"], "name", "Nero", "menebak", dsn=test_dsn)
