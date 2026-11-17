"""Proyeksi Buku Kerja (§2.3 revisi 7).

Semua tampilan PWA adalah turunan log kejadian dan bisa dibangun
ulang kapan saja. Proyeksi di sini murni fungsi atas daftar kejadian
(dict seperti yang dikembalikan `Ledger.events`): tanpa tulisan
ganda yang bisa berselisih.

Proyeksi:
- thread_chat: pesan user/agen, handoff, hasil (layar Chat).
- kantor: status live per agen + sub-agen (layar Kantor).
- audit: semua tool_dipanggil + persetujuan (Pengaturan).
- bukti: bukti_dicatat + pemeriksaan_selesai (panel Bukti).
- biaya: token + Rupiah per kejadian (Kantor, gerbang 10).
"""

from __future__ import annotations


def thread_chat(event: list[dict]) -> list[dict]:
    """Thread chat: pesan + handoff + hasil, urut waktu."""
    logout = []
    for ev in event:
        event_type = ev.get("type", "")
        content = ev.get("content") or {}
        if event_type in ("user_message", "agent_message"):
            logout.append({"type": "message", "dari": content.get("dari", ev.get("agent_id", "")),
                           "text": content.get("text", ""), "created_at": str(ev.get("created_at", ""))})
        elif event_type.startswith("handoff_"):
            logout.append({"type": "handoffs", "status": event_type[len("handoff_"):],
                           "dari": content.get("dari", ""), "to": content.get("to", ""),
                           "purpose": content.get("purpose", ""), "created_at": str(ev.get("created_at", ""))})
        elif event_type in ("task_completed", "task_failed"):
            logout.append({"type": "result", "status": event_type,
                           "task_id": ev.get("task_id", ""), "created_at": str(ev.get("created_at", ""))})
    return logout


def office(event: list[dict]) -> dict:
    """Status live tim: kejadian terakhir tiap agen/sub-agen + biaya."""
    agent: dict[str, dict] = {}
    for ev in event:
        aid = ev.get("agent_id", "")
        if not aid:
            continue
        info = agent.setdefault(aid, {"agent_id": aid, "status": "idle",
                                     "task_terakhir": "", "cost_rp": 0,
                                     "sub_agent": {}})
        event_type = ev.get("type", "")
        content = ev.get("content") or {}
        if event_type == "task_created":
            info["status"] = "bekerja"
            info["task_terakhir"] = ev.get("task_id", "")
        elif event_type in ("task_completed", "task_failed", "task_stopped"):
            info["status"] = "idle"
        elif event_type == "subagent_created":
            sub = content.get("subagent_id") or ev.get("subagent_id") or ""
            info["sub_agent"][sub] = {"status": "bekerja", "permission_class": content.get("permission_class", "")}
        elif event_type == "heartbeat":
            sub = ev.get("subagent_id") or ""
            if sub in info["sub_agent"]:
                info["sub_agent"][sub]["status"] = "bekerja"
        if isinstance(content.get("cost_rp"), (int, float)):
            info["cost_rp"] += int(content["cost_rp"])
    for info in agent.values():
        info["sub_agent"] = list(info["sub_agent"].values())
    return {"agents": list(agent.values())}


def audit(event: list[dict]) -> list[dict]:
    """Semua tool call + persetujuan (Pengaturan)."""
    return [ev for ev in event
            if ev.get("type", "") in ("tool_called", "tool_completed",
                                       "approval_requested", "approval_granted",
                                       "approval_rejected")]


def items_evidence(event: list[dict]) -> list[dict]:
    """Bukti + vonis pemeriksaan (panel Bukti)."""
    evidence = {}
    for ev in event:
        content = ev.get("content") or {}
        if ev.get("type") == "evidence_recorded" and content.get("evidence_id"):
            evidence[content["evidence_id"]] = {"evidence_id": content["evidence_id"], "klaim": content.get("klaim", ""),
                                      "value": content.get("value"), "vonis": "Uncertain"}
        elif ev.get("type") == "verification_completed" and content.get("evidence_id") in evidence:
            evidence[content["evidence_id"]]["vonis"] = content.get("vonis", evidence[content["evidence_id"]]["vonis"])
    return list(evidence.values())


def cost(event: list[dict]) -> dict:
    """Total biaya per tugas + total keseluruhan."""
    per_task: dict[str, int] = {}
    for ev in event:
        content = ev.get("content") or {}
        if isinstance(content.get("cost_rp"), (int, float)):
            tid = ev.get("task_id", "?")
            per_task[tid] = per_task.get(tid, 0) + int(content["cost_rp"])
    return {"per_task": per_task, "total_rp": sum(per_task.values())}
