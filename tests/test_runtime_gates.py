"""Tes pengukuran gerbang 11 + 12 (parsial, yang bisa diukur di Fase 2).

Gerbang 11 (agen + sub-agen): buat agen ke-2 dari PWA, pola 3 Pembaca
+ 1 Pemeriksa, Pemeriksa tangkap harga salah 3/3, handoff macet
dilaporkan < 2 menit, Puspa tidak bisa dihapus, token di luar
ditolak 3/3, handoff melingkar ditolak, Hentikan < 5 detik.
Gerbang 12 (tulang punggung + bukti): rantai hash yang diubah
terdeteksi 3/3, angka yatim ditahan 3/3, kaset 9/10 identik.
"""

from puspa.db import connect as _db_connect
import pytest

from puspa.evidence.evidence import record_evidence
from puspa.evidence.renderer import present
from puspa.evidence.verifier import verify_injection
from puspa.runtime.handoff import HandoffError, reject_cycle
from puspa.runtime.subagents import SubagentError, SubagentPool


def test_gate11_verifier_catches_injection_3_of_3():
    def reread(url):
        return {"value": 12500000, "name": "Laptop X"}

    for value_wrong in (9999999, 15000000, 100000):
        b = record_evidence(
            task_id="t1",
            claim="Laptop X",
            value=value_wrong,
            unit="Rp",
            source_url="https://tokopedia.com/produk/laptop-x",
            text_excerpt="x",
            reader_id="sub-1",
        )
        assert verify_injection(b, value_wrong=value_wrong, reread=reread) != "Verified"


def test_gate11_token_outside_rejected_3_of_3(token_payloads):
    pool = SubagentPool()
    for tools in (["delete_all"], ["read", "send_without_permission"], ["pay"]):
        bukan_subset = dict(token_payloads["sub"], tools=tools)
        with pytest.raises(SubagentError):
            pool.spawn(
                agent_id="agen-1",
                task_id="t1",
                permission_class="Pelaksana",
                agent_token=token_payloads["agents"],
                subagent_token=bukan_subset,
            )


def test_gate11_handoff_circular_rejected():
    with pytest.raises(HandoffError):
        reject_cycle(["puspa", "agen-riset"], "agen-riset", "puspa")


def test_gate12_changed_hash_chain_detected_3_of_3(ledger, make_user):
    u = make_user()
    for i in range(3):
        task_id = f"tugas-rantai-{i}"
        ledger.append_event(
            task_id=task_id,
            agent_id=u["agent_id"],
            event_type="task_created",
            content={"purpose": "test"},
        )
        ledger.append_event(
            task_id=task_id, agent_id=u["agent_id"], event_type="task_completed", content={}
        )
        ok_sebelum, _ = ledger.verify_chain(task_id)
        assert ok_sebelum is True
        # Ubah diam-diam satu hash (sebagai pemilik DB; nilai unik per tugas).
        with _db_connect(ledger.dsn) as conn:
            conn.execute(
                "UPDATE events SET hash = %s WHERE task_id = %s AND type = 'task_created'",
                (
                    f"00diubah{i}",
                    task_id,
                ),
            )
            conn.commit()
        ok_sesudah, _ = ledger.verify_chain(task_id)
        assert ok_sesudah is False


def test_gate12_orphan_number_held_3_of_3():
    for text in (
        "Harganya Rp9.999.000.",
        "Tagihan Rp132.500 jatuh tempo 20 Nov.",
        "Selisihnya 15% lebih murah.",
    ):
        result = present(text, {})
        assert result["held"] is True and result["dropped"]
