"""Tes kolam sub-agen (§2.2): batas, satu level, subset token, Hentikan."""

import pytest

from puspa.runtime.subagents import SubagentError, SubagentPool


def test_spawn_one_level_and_subset_token(token_payloads):
    pool = SubagentPool()
    with pytest.raises(SubagentError, match="satu level"):
        pool.spawn(
            agent_id="agen-1",
            task_id="t1",
            permission_class="Pembaca",
            agent_token=token_payloads["agents"],
            subagent_token=token_payloads["sub"],
            creator_is_subagent=True,
        )
    bukan_subset = dict(token_payloads["sub"], tools=["read", "delete_all"])
    with pytest.raises(SubagentError, match="himpunan bagian"):
        pool.spawn(
            agent_id="agen-1",
            task_id="t1",
            permission_class="Pembaca",
            agent_token=token_payloads["agents"],
            subagent_token=bukan_subset,
        )
    sub = pool.spawn(
        agent_id="agen-1",
        task_id="t1",
        permission_class="Pembaca",
        agent_token=token_payloads["agents"],
        subagent_token=token_payloads["sub"],
    )
    assert sub.status == "active"


def test_limit_four_per_agent_six_system_wide(token_payloads):
    pool = SubagentPool()
    for _i in range(4):
        s = pool.spawn(
            agent_id="agen-1",
            task_id="t1",
            permission_class="Pembaca",
            agent_token=token_payloads["agents"],
            subagent_token=token_payloads["sub"],
        )
        assert s.status == "active"
    queued = pool.spawn(
        agent_id="agen-1",
        task_id="t1",
        permission_class="Pembaca",
        agent_token=token_payloads["agents"],
        subagent_token=token_payloads["sub"],
    )
    assert queued.status == "queued"
    # Agen lain masih bisa sampai batas sistem 6.
    for _ in range(2):
        pool.spawn(
            agent_id="agen-2",
            task_id="t2",
            permission_class="Pelaksana",
            agent_token=token_payloads["agents"],
            subagent_token=token_payloads["sub"],
        )
    assert pool.count_active() == 6
    penuh = pool.spawn(
        agent_id="agen-2",
        task_id="t2",
        permission_class="Pelaksana",
        agent_token=token_payloads["agents"],
        subagent_token=token_payloads["sub"],
    )
    assert penuh.status == "queued"
    # Satu selesai -> antrean maju.
    stopped_subagents = pool.active_per_agent("agen-1")[0]
    pool.complete(stopped_subagents.subagent_id)
    assert queued.status == "active"


def test_stop_shuts_down_all_and_revokes_tokens_within_5_seconds(token_payloads):
    pool = SubagentPool()
    for _ in range(3):
        pool.spawn(
            agent_id="agen-1",
            task_id="t-stop",
            permission_class="Pembaca",
            agent_token=token_payloads["agents"],
            subagent_token=token_payloads["sub"],
        )
    result = pool.stop_task("t-stop")
    assert result["stopped"] == 3
    assert result["seconds"] < 5
    assert pool.count_active() == 0
