"""Tes kolam sub-agen (§2.2): batas, satu level, subset token, Hentikan."""

import pytest

from puspa.runtime.subagents import SubagentPool, SubagentError


def test_spawn_satu_level_and_subset_token(token_payloads):
    kolam = SubagentPool()
    with pytest.raises(SubagentError, match="satu level"):
        kolam.spawn(agent_id="agen-1", task_id="t1", permission_class="Pembaca",
                    token_agent=token_payloads["agents"], token_subagen=token_payloads["sub"],
                    pembuat_adalah_subagen=True)
    bukan_subset = dict(token_payloads["sub"], tools=["read", "delete_all"])
    with pytest.raises(SubagentError, match="himpunan bagian"):
        kolam.spawn(agent_id="agen-1", task_id="t1", permission_class="Pembaca",
                    token_agent=token_payloads["agents"], token_subagen=bukan_subset)
    sub = kolam.spawn(agent_id="agen-1", task_id="t1", permission_class="Pembaca",
                      token_agent=token_payloads["agents"], token_subagen=token_payloads["sub"])
    assert sub.status == "active"


def test_batas_empat_per_agent_enam_sistem(token_payloads):
    kolam = SubagentPool()
    for i in range(4):
        s = kolam.spawn(agent_id="agen-1", task_id="t1", permission_class="Pembaca",
                        token_agent=token_payloads["agents"], token_subagen=token_payloads["sub"])
        assert s.status == "active"
    antre = kolam.spawn(agent_id="agen-1", task_id="t1", permission_class="Pembaca",
                        token_agent=token_payloads["agents"], token_subagen=token_payloads["sub"])
    assert antre.status == "antre"
    # Agen lain masih bisa sampai batas sistem 6.
    for _ in range(2):
        kolam.spawn(agent_id="agen-2", task_id="t2", permission_class="Pelaksana",
                    token_agent=token_payloads["agents"], token_subagen=token_payloads["sub"])
    assert kolam.amount_active() == 6
    penuh = kolam.spawn(agent_id="agen-2", task_id="t2", permission_class="Pelaksana",
                        token_agent=token_payloads["agents"], token_subagen=token_payloads["sub"])
    assert penuh.status == "antre"
    # Satu selesai -> antrean maju.
    korban = kolam.active_per_agent("agen-1")[0]
    kolam.completed_at(korban.subagent_id)
    assert antre.status == "active"


def test_hentikan_matikan_all_and_cabut_token_dalam_5_detik(token_payloads):
    kolam = SubagentPool()
    for _ in range(3):
        kolam.spawn(agent_id="agen-1", task_id="t-stop", permission_class="Pembaca",
                    token_agent=token_payloads["agents"], token_subagen=token_payloads["sub"])
    result = kolam.hentikan_task("t-stop")
    assert result["dihentikan"] == 3
    assert result["detik"] < 5
    assert kolam.amount_active() == 0
