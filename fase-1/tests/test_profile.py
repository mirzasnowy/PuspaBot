"""Tes Profil — lapis 1 memori (C53, C58, C59)."""

import pytest

from puspa.memory import profile


def test_tambah_fakta_diucapkan_masuk_profil(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(u["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    assert fact["status"] == "berlaku"
    assert fact["konflik_dengan"] is None
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {"nama": "Nero"}


def test_fakta_disimpulkan_masuk_usulan_bukan_profil(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(
        u["user_id"], "suka", "kopi", "disimpulkan", dsn=test_dsn
    )
    assert fact["status"] == "usulan"
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {}
    assert [p["kunci"] for p in profile.list_proposals(u["user_id"], dsn=test_dsn)] == ["suka"]


def test_konfirmasi_usulan_masuk_profil(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(u["user_id"], "suka", "kopi", "disimpulkan", dsn=test_dsn)
    confirmed = profile.confirm_proposal(fact["id"], u["user_id"], dsn=test_dsn)
    assert confirmed["status"] == "berlaku"
    assert confirmed["asal"] == "dikonfirmasi_user"
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {"suka": "kopi"}
    assert profile.list_proposals(u["user_id"], dsn=test_dsn) == []


def test_tolak_usulan_tetap_tercatat(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(u["user_id"], "suka", "kopi", "disimpulkan", dsn=test_dsn)
    rejected = profile.reject_proposal(fact["id"], u["user_id"], dsn=test_dsn)
    assert rejected["status"] == "ditolak"
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {}
    with pytest.raises(KeyError):
        profile.confirm_proposal(fact["id"], u["user_id"], dsn=test_dsn)


def test_fakta_baru_tidak_menimpa_diam_diam(make_user, test_dsn):
    """Konflik ditandai: versi lama diganti tapi riwayat tetap ada."""
    u = make_user()
    lama = profile.add_fact(u["user_id"], "alamat", "Jakarta", "diucapkan_user", dsn=test_dsn)
    baru = profile.add_fact(u["user_id"], "alamat", "Bandung", "dikonfirmasi_user", dsn=test_dsn)
    assert baru["konflik_dengan"] == lama["id"]
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {"alamat": "Bandung"}
    riwayat = profile.list_facts(u["user_id"], kunci="alamat", include_history=True, dsn=test_dsn)
    assert [f["status"] for f in riwayat] == ["diganti", "berlaku"]
    assert riwayat[0]["nilai"] == "Jakarta"  # versi lama tetap bisa dilihat


def test_koreksi_dan_hapus_fakta(make_user, test_dsn):
    u = make_user()
    fact = profile.add_fact(u["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    profile.correct_fact(u["user_id"], "nama", "Nero Mirza", dsn=test_dsn)
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {"nama": "Nero Mirza"}
    aktif = profile.list_facts(u["user_id"], kunci="nama", dsn=test_dsn)
    dihapus = profile.delete_fact(aktif[0]["id"], u["user_id"], dsn=test_dsn)
    assert dihapus["status"] == "ditolak"
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {}


def test_profil_inti_terlihat_semua_agen_fakta_agen_tidak(make_user, test_dsn):
    u = make_user()
    import uuid

    agen_lain = str(uuid.uuid4())
    profile.add_fact(u["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    profile.add_fact(
        u["user_id"], "tugas_rahasia", "X", "diucapkan_user",
        agen_id=u["agen_id"], dsn=test_dsn,
    )
    assert profile.get_profile(u["user_id"], agen_id=agen_lain, dsn=test_dsn) == {"nama": "Nero"}
    assert profile.get_profile(u["user_id"], agen_id=u["agen_id"], dsn=test_dsn) == {
        "nama": "Nero",
        "tugas_rahasia": "X",
    }


def test_isolasi_antar_pengguna(make_user, test_dsn):
    a, b = make_user(), make_user()
    profile.add_fact(a["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    assert profile.get_profile(b["user_id"], dsn=test_dsn) == {}
    assert profile.list_facts(b["user_id"], dsn=test_dsn) == []
    with pytest.raises(KeyError):
        profile.confirm_proposal("00000000-0000-0000-0000-000000000000", b["user_id"], dsn=test_dsn)


def test_asal_tidak_dikenal_ditolak(make_user, test_dsn):
    u = make_user()
    with pytest.raises(ValueError):
        profile.add_fact(u["user_id"], "nama", "Nero", "menebak", dsn=test_dsn)
