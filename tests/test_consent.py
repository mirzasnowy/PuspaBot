"""Tes Persetujuan data + ekspor/hapus akun (C127, C129)."""

import pytest

from puspa.consent import (
    TEXT_APPROVAL,
    TEKS_VERSI,
    delete_account,
    export_account,
    has_consented,
    list_consents,
    record_consent,
)
from puspa.memory import episodic, procedural, profile


def test_teks_approval_memuat_6_hal_required():
    for frasa in (
        "What data is stored",
        "Where",
        "How long",
        "Who can see it",
        "Export and delete",
        "Shared graph",
    ):
        assert frasa in TEXT_APPROVAL


def test_approval_dicatat_table_and_book_kerja(make_user, test_dsn, ledger):
    u = make_user()
    assert has_consented(u["user_id"], dsn=test_dsn) is False
    task_id = "22222222-2222-2222-2222-222222222222"
    result = record_consent(
        u["user_id"], dsn=test_dsn, ledger=ledger,
        agent_id=u["agent_id"], task_id=task_id,
    )
    assert result["text_version"] == TEKS_VERSI
    assert result["event_id"] is not None
    assert has_consented(u["user_id"], dsn=test_dsn) is True
    assert len(list_consents(u["user_id"], dsn=test_dsn)) == 1
    events = ledger.events(task_id, event_type="data_consents")
    assert len(events) == 1
    assert events[0]["content"]["text_version"] == TEKS_VERSI


def test_approval_without_ledger_tetap_tercatat_di_table(make_user, test_dsn):
    u = make_user()
    result = record_consent(u["user_id"], dsn=test_dsn)
    assert result["event_id"] is None
    assert has_consented(u["user_id"], dsn=test_dsn) is True


def test_export_memuat_data_without_rahasia(make_user, test_dsn, vault_master_key):
    from puspa.accounts.vault import put as vault_put

    u = make_user()
    profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    episodic.record_episode(u["user_id"], "suka kopi", "user_message", dsn=test_dsn)
    procedural.record_successful_task(u["user_id"], "check_pln", ["buka situs"], dsn=test_dsn)
    record_consent(u["user_id"], dsn=test_dsn)
    vault_put(u["user_id"], "google_oauth", '{"access_token": "RAHASIA"}', dsn=test_dsn)
    export = export_account(u["user_id"], dsn=test_dsn)
    assert export["profile"] == {"name": "Nero"}
    assert len(export["episodic"]) == 1
    assert len(export["procedures"]) == 1
    assert len(export["approval"]) == 1
    assert "RAHASIA" not in str(export)  # token vault tidak ikut ekspor


def test_delete_akun_menghapus_data_di_luar_book_kerja(make_user, test_dsn, ledger):
    u = make_user()
    profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    episodic.record_episode(u["user_id"], "suka kopi", "user_message", dsn=test_dsn)
    record_consent(u["user_id"], dsn=test_dsn)
    counts = delete_account(u["user_id"], dsn=test_dsn)
    assert counts["profile_facts"] == 1
    assert counts["episodic"] == 1
    assert counts["data_consents"] == 1
    assert counts["users"] == 1
    assert profile.get_profile(u["user_id"], dsn=test_dsn) == {}
    assert episodic.list_episodes(u["user_id"], dsn=test_dsn) == []
    assert has_consented(u["user_id"], dsn=test_dsn) is False
