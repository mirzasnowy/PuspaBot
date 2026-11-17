"""Tes Irisan memori (C57) + Konsolidasi saat sepi (C60)."""

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from puspa.memory import consolidate, episodic, procedural, profile, slices


def test_irisan_hanya_kolom_yang_diizinkan(make_user, test_dsn):
    u = make_user()
    profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    episodic.record_episode(u["user_id"], "suka kopi tubruk", "user_message", dsn=test_dsn)
    data = slices.grant_slice(
        u["user_id"], u["agent_id"], str(uuid.uuid4()), "kopi",
        ["episodic"], "token-123", dsn=test_dsn,
    )
    assert set(data) == {"episodic", "_irisan_id", "_dicatat_pada"}
    assert data["episodic"] and "kopi" in data["episodic"][0]["content"]


def test_irisan_rejected_kalau_kolom_not_diizinkan(make_user, test_dsn):
    u = make_user()
    with pytest.raises(ValueError):
        slices.grant_slice(
            u["user_id"], u["agent_id"], str(uuid.uuid4()), "kopi",
            ["vault"], "token-123", dsn=test_dsn,
        )
    with pytest.raises(ValueError):
        slices.grant_slice(
            u["user_id"], u["agent_id"], str(uuid.uuid4()), "kopi",
            [], "token-123", dsn=test_dsn,
        )


def test_pemberian_irisan_dicatat_and_can_diaudit(make_user, test_dsn):
    u = make_user()
    sub = str(uuid.uuid4())
    slices.grant_slice(
        u["user_id"], u["agent_id"], sub, "name",
        ["profile"], "token-abc", dsn=test_dsn,
    )
    grants = slices.list_grants(u["user_id"], dsn=test_dsn)
    assert len(grants) == 1
    assert grants[0]["subagent_id"] == sub
    assert grants[0]["kolom"] == ["profile"]
    assert grants[0]["token_ref"] == "token-abc"


def test_konsolidasi_meringkas_and_menunjuk_event_source(make_user, test_dsn, ledger):
    u = make_user()
    task_id = str(uuid.uuid4())
    ev1 = ledger.append_event(
        task_id=task_id, agent_id=u["agent_id"], event_type="user_message",
        content={"text": "cek pln"},
    )
    ev2 = ledger.append_event(
        task_id=task_id, agent_id=u["agent_id"], event_type="task_completed",
        content={"result": "check_pln: berhasil"},
    )
    # Buat episode "lama" (45 hari) dengan kejadian asal yang sama.
    for ev, event_type, content in (
        (ev1, "user_message", "cek tagihan pln nomor pelanggan"),
        (ev2, "task_completed", "check_pln: berhasil, tagihan terbaca"),
    ):
        episodic.record_episode(
            u["user_id"], content, event_type, task_id=task_id,
            event_id=str(ev["id"]), dsn=test_dsn,
        )
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "UPDATE episodic SET created_at = now() - interval '45 days' WHERE user_id = %s",
            (u["user_id"],),
        )
        conn.commit()
    result = consolidate.consolidate_idle(u["user_id"], older_than_days=30, dsn=test_dsn)
    assert result["summary_created"] == 1
    assert result["prosedur_dipromosikan"] == 1
    summary = consolidate.list_summaries(u["user_id"], dsn=test_dsn)
    assert len(summary) == 1
    assert set(summary[0]["event_ids"]) == {str(ev1["id"]), str(ev2["id"])}
    assert "check_pln" in summary[0]["summaries"]
    procs = procedural.list_procedures(u["user_id"], dsn=test_dsn)
    assert len(procs) == 1 and procs[0]["status"] == "trial"


def test_konsolidasi_not_mengulang_episode_yang_already_diringkas(make_user, test_dsn):
    u = make_user()
    task_id = str(uuid.uuid4())
    episodic.record_episode(
        u["user_id"], "cek pln berhasil", "task_completed",
        task_id=task_id, event_id=str(uuid.uuid4()), dsn=test_dsn,
    )
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "UPDATE episodic SET created_at = now() - interval '45 days' WHERE user_id = %s",
            (u["user_id"],),
        )
        conn.commit()
    assert consolidate.consolidate_idle(u["user_id"], dsn=test_dsn)["summary_created"] == 1
    assert consolidate.consolidate_idle(u["user_id"], dsn=test_dsn)["summary_created"] == 0


def test_konsolidasi_melewatkan_episode_new(make_user, test_dsn):
    u = make_user()
    episodic.record_episode(
        u["user_id"], "cek pln berhasil", "task_completed",
        task_id=str(uuid.uuid4()), event_id=str(uuid.uuid4()), dsn=test_dsn,
    )
    result = consolidate.consolidate_idle(u["user_id"], older_than_days=30, dsn=test_dsn)
    assert result["summary_created"] == 0
